// servers/memory-mcp.ts
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

// Resolve repo root relative to this file
const here = fileURLToPath(import.meta.url);
const repoRoot = resolve(here, "..", "..");

// Small helper to run the CLI and capture stdout
function runCli(mode: string, args: Record<string, string>): Promise<string> {
  const kvArgs = Object.entries(args)
    .filter(([, v]) => v !== undefined && v !== null && String(v).length)
    .map(([k, v]) => `${k}=${v}`);

  return new Promise((resolveP, rejectP) => {
    const child = spawn(
      process.execPath,
      [
        // ts-node/esm loader so we can run the TS file directly
        "--loader",
        "ts-node/esm",
        "scripts/prompt-memory.ts",
        mode,
        ...kvArgs,
      ],
      { cwd: repoRoot },
    );

    let out = "";
    let err = "";

    child.stdout.on("data", (d) => (out += d.toString()));
    child.stderr.on("data", (d) => (err += d.toString()));

    child.on("close", (code) => {
      if (code === 0) {
        resolveP(out || "ok");
      } else {
        rejectP(new Error(err || `prompt-memory exited with code ${code}`));
      }
    });
  });
}

// Create the MCP server
const server = new Server(
  {
    name: "prompt-memory-mcp",
    version: "1.0.0",
  },
  {
    capabilities: {
      tools: {},
    },
  },
);

// Advertise our three tools
server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: [
      {
        name: "memory_update_context",
        description:
          "Set the current task snapshot in .github/prompts/35_current-task.md (Area, Status, Summary, Key files, Next steps).",
        inputSchema: {
          type: "object",
          properties: {
            area: { type: "string", description: "Feature/bug/refactor area" },
            status: { type: "string", description: "Status label, e.g., active/blocked" },
            summary: {
              type: "string",
              description: "1–3 sentence human summary of the current task",
            },
            files: {
              type: "string",
              description: "Key file paths (comma or space separated)",
            },
            next: {
              type: "string",
              description: "Pipe-separated bullets for next steps (e.g. 'A|B|C')",
            },
          },
          required: ["summary"],
        },
      },
      {
        name: "memory_update_progress",
        description:
          "Update development status snapshot in 30_development-status.md (Done, Doing, Next).",
        inputSchema: {
          type: "object",
          properties: {
            done: {
              type: "string",
              description: "Pipe-separated list of things finished this iteration",
            },
            doing: {
              type: "string",
              description: "Pipe-separated list of things currently in progress",
            },
            next: {
              type: "string",
              description: "Pipe-separated list of up-next items",
            },
          },
          required: [],
        },
      },
      {
        name: "memory_log_decision",
        description:
          "Append a line to 90_decision-log.md capturing a design/architecture decision.",
        inputSchema: {
          type: "object",
          properties: {
            decision: { type: "string", description: "Short decision statement" },
            rationale: {
              type: "string",
              description: "Why we chose this, tradeoffs, etc.",
            },
            links: {
              type: "string",
              description: "Issue/PR links or docs (optional)",
            },
          },
          required: ["decision"],
        },
      },
    ],
  };
});

// Route tool calls to the CLI
server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const name = req.params.name;
  const args = (req.params.arguments ?? {}) as Record<string, string>;

  try {
    switch (name) {
      case "memory_update_context": {
        const text = await runCli("update-context", args);
        return { content: [{ type: "text", text }] };
      }
      case "memory_update_progress": {
        const text = await runCli("update-progress", args);
        return { content: [{ type: "text", text }] };
      }
      case "memory_log_decision": {
        const text = await runCli("log-decision", args);
        return { content: [{ type: "text", text }] };
      }
      default:
        throw new Error(`Unknown tool: ${name}`);
    }
  } catch (err: any) {
    const message = err?.message ?? String(err);
    // Surface errors back to the model
    return {
      content: [
        {
          type: "text",
          text: `Error while running ${name}: ${message}`,
        },
      ],
      isError: true,
    } as any;
  }
});

// Bootstrap the stdio transport
async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("[prompt-memory-mcp] server running on stdio");
}

main().catch((err) => {
  console.error("[prompt-memory-mcp] fatal error", err);
  process.exit(1);
});
