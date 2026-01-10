// scripts/prompt-memory.ts
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { resolve, dirname } from "node:path";

type Mode = "update-context" | "update-progress" | "log-decision";

const ROOT = resolve(process.cwd(), ".github", "prompts");

const FILES = {
  current: resolve(ROOT, "35_current-task.md"),
  status: resolve(ROOT, "30_development-status.md"),
  decisions: resolve(ROOT, "90_decision-log.md"),
};

function ensureFile(path: string, fallback: string) {
  const dir = dirname(path);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  if (!existsSync(path)) writeFileSync(path, fallback.trim() + "\n\n", "utf8");
}

function upsertSection(filePath: string, marker: string, contentMd: string) {
  const start = `<!-- ${marker}:start -->`;
  const end = `<!-- ${marker}:end -->`;
  const raw = readFileSync(filePath, "utf8");

  const block = `${start}\n${contentMd.trim()}\n${end}`;

  const next = raw.includes(start) && raw.includes(end)
    ? raw.replace(new RegExp(`${start}[\\s\\S]*?${end}`), block)
    : `${raw.trim()}\n\n${block}\n`;

  writeFileSync(filePath, next, "utf8");
}

function appendBetween(filePath: string, marker: string, line: string) {
  const start = `<!-- ${marker}:start -->`;
  const end = `<!-- ${marker}:end -->`;
  const raw = readFileSync(filePath, "utf8");

  const next = raw.replace(
    new RegExp(`(${start})([\\s\\S]*?)(${end})`),
    (_m, a, mid, c) => {
      const trimmed = mid.trim();
      const body = trimmed.length ? `${trimmed}\n${line}` : line;
      return `${a}\n${body}\n${c}`;
    },
  );

  writeFileSync(filePath, next, "utf8");
}

function now(ts = new Date()) {
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${ts.getFullYear()}-${pad(ts.getMonth() + 1)}-${pad(ts.getDate())} ${pad(
    ts.getHours(),
  )}:${pad(ts.getMinutes())}`;
}

async function main() {
  const [, , modeArg, ...rest] = process.argv as [string, string, Mode, ...string[]];
  const mode = modeArg as Mode | undefined;

  if (!mode || !["update-context", "update-progress", "log-decision"].includes(mode)) {
    console.error("Usage: node scripts/prompt-memory.ts <mode> [k=v …]");
    console.error("Modes: update-context | update-progress | log-decision");
    process.exit(1);
  }

  ensureFile(FILES.current, "# Current Task (living)");
  ensureFile(FILES.status, "# Development Status (living)");
  ensureFile(FILES.decisions, "# Decision Log (append-only)");

  const kv: Record<string, string> = Object.fromEntries(
    rest
      .map((a) => {
        const [k, ...v] = a.split("=");
        return [k.trim(), v.join("=").trim()];
      })
      .filter(([k, v]) => k && v),
  );

  switch (mode) {
    case "update-context": {
      const md = [
        `## ${now().split(" ")[0]} — Snapshot`,
        `Area: ${kv.area ?? "unspecified"}`,
        `Status: ${kv.status ?? "active"}`,
        `Summary: ${kv.summary ?? "-"}`,
        `Key files: ${kv.files ?? "-"}`,
        "Next steps:",
        ...(kv.next ? kv.next.split("|").map((s) => `- ${s}`) : ["- -"]),
      ].join("\n");

      upsertSection(FILES.current, "mem:current-task", md);
      break;
    }

    case "update-progress": {
      const md = [
        `## ${now().split(" ")[0]} — Snapshot`,
        "Done:",
        ...(kv.done ? kv.done.split("|").map((s) => `- ${s}`) : ["- -"]),
        "Doing:",
        ...(kv.doing ? kv.doing.split("|").map((s) => `- ${s}`) : ["- -"]),
        "Next:",
        ...(kv.next ? kv.next.split("|").map((s) => `- ${s}`) : ["- -"]),
      ].join("\n");

      upsertSection(FILES.status, "mem:dev-status", md);
      break;
    }

    case "log-decision": {
      const line = `${now()} — Decision: ${kv.decision ?? "-"} — Rationale: ${
        kv.rationale ?? "-"
      } — Links: ${kv.links ?? "-"}`;
      appendBetween(FILES.decisions, "mem:decision-log", line);
      break;
    }
  }

  process.stdout.write("Memory prompts updated.\n");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
