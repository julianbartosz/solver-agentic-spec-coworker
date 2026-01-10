# ADR-0015: Streamlit Production Hardening

**Status**: Proposed  
**Date**: 2025-12-22  
**Deciders**: Engineering Team

## Context and Problem Statement

The Streamlit UI has critical gaps preventing production parity with `demo-final-showcase.sh`. Users see "Advanced Options" (timeout, sandbox gates, live tests) but these are **stored in session_state and never wired to the execution layer**.

### Evidence Collected

| Bug | Location | Impact |
|-----|----------|--------|
| Import crash: `run_with_timeout` vs `run_pipeline_with_timeout` | `src/integration_coworker/harness/__init__.py#L14` | Harness module completely broken |
| Function name mismatch: `run_integration_workflow` doesn't exist | `src/integration_coworker/harness/runner.py#L104` | Subprocess script would crash |
| Advanced Options ignored | `src/integration_coworker/ui/streamlit_app.py#L517` | timeout/sandbox/live not passed |
| No `st.status` for progress | `src/integration_coworker/ui/streamlit_app.py#L516` | Only `st.spinner`, no progress detail |
| Default profile = development | `src/integration_coworker/config/profiles.py#L83` | `enable_sandbox_execution=False` |

### Proof of Broken Chain

```
UI Session State             IntegrationOptions        CodegenProfile
┌──────────────────┐         ┌──────────────────┐      ┌──────────────────┐
│ timeout: 600     │──X──    │ (no timeout)     │      │                  │
│ sandbox_gates:   │──X──    │ (no sandbox)     │      │ enable_sandbox:  │
│   [ruff,mypy...] │         │ (no gates list)  │      │   False (dev)    │
│ enable_live: T/F │──X──    │ (no live field)  │      │ enable_live:     │
└──────────────────┘         └──────────────────┘      │   False (dev)    │
        │                                              └────────┬─────────┘
        │                                                       │
        └───────────────────────────────────────────────────────┘
        BROKEN: UI stores values but never passes them through
```

---

## Decision 1: Execution Driver

### Considered Options

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A: Direct call (current)** | `design_and_generate_integration()` in UI thread | Simple, no overhead | **No timeout kill capability**. Thread timeouts can't kill stuck LLM/DB calls. |
| **B: Subprocess harness** | `run_pipeline_with_timeout()` spawns Python subprocess | **Reliable timeout via SIGKILL**. Process isolation. Demo parity. | ~1s overhead. Must serialize result. |
| **C: External service** | Separate FastAPI/Celery worker | Scales to multi-user. Background jobs. | Complex. Requires Redis/RabbitMQ queue. Overkill for demo. |

### Decision: **Option B (Subprocess Harness)**

**Justification:**
1. **Timeout is non-negotiable**: LLM calls can hang for minutes. Only subprocess kill works.
2. **Already partially written**: `harness/runner.py` exists (just needs bug fixes).
3. **Demo parity**: `demo-final-showcase.sh` uses subprocess isolation.
4. **Minimal overhead**: ~1-2s for subprocess spawn is acceptable for 1-5 minute pipelines.

Option C (external service) is better for SaaS but wrong for single-user demo/dev.

---

## Decision 2: Configuration Plumbing

### Considered Options

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A: Env var injection** | Set `CODEGEN_PROFILE=production` before call | Minimal code changes | Global state mutation. Thread-unsafe. |
| **B: Extend IntegrationOptions** | Add `profile_name`, `sandbox_gates`, `enable_live` fields | Clean API. Explicit. Thread-safe. | 4+ files to change. Must plumb through. |
| **C: Harness-only params** | Pass config to subprocess via env/args | Isolated to harness. No API changes. | Harness becomes the only way to configure. |

### Decision: **Option C (Harness-Only Params)** for now, with **Option B as follow-up**

**Justification:**
1. **Fastest to implement**: Harness already takes params, just needs to set env vars.
2. **Subprocess isolation means env vars are safe**: No thread contamination.
3. **API changes (Option B) can come later** as a clean refactor.

The subprocess runs with:
```python
env["CODEGEN_PROFILE"] = "production"  # Enables sandbox
env["SANDBOX_GATES"] = ",".join(gates)  # For future per-gate control
env["ALLOW_LIVE"] = "1" if enable_live else "0"
```

---

## Decision 3: Timeout Implementation

### Considered Options

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A: Threading + signal.alarm** | SIGALRM in main thread | Simple | **Doesn't work on Windows. Can't kill stuck C extensions or network calls.** |
| **B: ThreadPoolExecutor.submit(timeout)** | `future.result(timeout=T)` | Clean async pattern | **Thread still runs after timeout!** Can't kill LLM call. |
| **C: subprocess.Popen + SIGTERM/SIGKILL** | Run pipeline in child process | **Actually kills the process**. Works on all OS. | Must serialize result. ~1s overhead. |

### Decision: **Option C (Subprocess SIGTERM/SIGKILL)**

**Justification:**
1. **This is the only approach that reliably terminates stuck processes**.
2. LLM calls (OpenAI/Anthropic SDK) can block for 30+ seconds on network timeouts.
3. DB connections can block indefinitely on lock contention.
4. `os.killpg(pgid, SIGKILL)` kills the entire process group including children.

The existing `run_pipeline_with_timeout()` already implements this pattern correctly (lines 300-340).

---

## Decision 4: Live Validation Safety

### Considered Options

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A: Default ON, blocklist** | Allow all hosts except explicit deny list | Convenient | **Dangerous**. Accidental API charges. |
| **B: Default OFF, user opt-in checkbox** | Checkbox in UI + explicit enable | Safe by default | Requires extra click. |
| **C: Default OFF, allowlist + env passthrough** | Checkbox + host allowlist + env var whitelist | **Most secure**. Controls exactly which hosts and secrets are exposed. | More complex. |

### Decision: **Option C (Default OFF, Allowlist + Env Passthrough)**

**Justification:**
1. **pytest-socket pattern**: Already using `--disable-socket` + `--allow-hosts` in tests.
2. **Profile already supports it**: `live_host_allowlist` and `live_env_passthrough` fields exist.
3. **Visible warning**: UI shows "🔴 Live tests enabled" when checkbox is checked.

Implementation:
```python
# In subprocess env
if enable_live_tests:
    env["ALLOW_LIVE"] = "1"
    env["LIVE_HOST_ALLOWLIST"] = "api.stripe.com,api.openai.com"
    env["LIVE_ENV_PASSTHROUGH"] = "STRIPE_SECRET_KEY,OPENAI_API_KEY"
```

---

## Decision 5: Progress Display

### Considered Options

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A: st.spinner only (current)** | Generic "Running..." message | Simple | No progress visibility. User doesn't know what's happening. |
| **B: st.status with updates** | Expandable status container with sub-steps | **Shows progress**. User sees "Ingesting spec...", "Generating code...", etc. | Requires callback mechanism to UI. |
| **C: Real-time streaming** | WebSocket/SSE stream of node completions | Full real-time visibility | Complex. Requires subprocess IPC. |

### Decision: **Option B (st.status)** initially, **Option C as enhancement**

**Justification:**
1. **st.status is Streamlit's recommended pattern** for long-running operations.
2. **Subprocess complicates real-time updates**: Would need to poll stdout or use pipe.
3. **Initial implementation**: Show status before/after, expand with result details.

For Phase 1:
```python
with st.status("Running integration workflow...", expanded=True) as status:
    status.update(label="⏳ Starting pipeline subprocess...")
    result = run_pipeline_with_timeout(...)
    if result.success:
        status.update(label="✅ Pipeline completed!", state="complete")
    else:
        status.update(label="❌ Pipeline failed", state="error")
```

---

## Consequences

### Positive
- **Timeout actually works**: Subprocess can be killed.
- **Sandbox gates run**: `CODEGEN_PROFILE=production` enables them.
- **Live tests are safe**: Default OFF, explicit opt-in.
- **Progress visibility**: `st.status` shows what's happening.

### Negative
- **~1-2s overhead per run**: Subprocess spawn cost.
- **Result serialization**: Must convert to JSON for IPC.
- **No real-time node updates**: Phase 1 is before/after only.

### Risks
- Harness subprocess script needs maintenance when API changes.
- env var approach is a stopgap; proper IntegrationOptions extension is cleaner.

---

## Implementation Plan

See `history/plans-archive/IMPLEMENTATION_PLAN_V4.md` for file-by-file changes (archived).

## References

- [Streamlit st.status docs](https://docs.streamlit.io/develop/api-reference/status/st.status)
- [pytest-socket plugin](https://pypi.org/project/pytest-socket/)
- Production hardening audit: `history/prod-hardening/PRODUCTION_HARDENING_AUDIT_V3.md` (archived)
