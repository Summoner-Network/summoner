# Two-Agent / Two-Repo Live Demo (5-10 min)

## Demo Story

- `CodexA` owns backend repo: `task-service-api` (FastAPI)
- `CodexB` owns frontend repo: `task-portal-web` (React + Vite)
- Both collaborate via `/collab` on API contract and integration fixes

This is designed for a live Zoom walkthrough with visible agent collaboration value.

## Terminal Layout

- Terminal 1: Summoner server
- Terminal 2: `CodexA` agent (backend repo)
- Terminal 3: `CodexB` agent (frontend repo)
- Terminal 4: optional runtime checks (uvicorn / vite)

## 0) Install the Codex CLI (prerequisite)

The agent requires the Codex CLI binary. Install it once via npm:

```bash
npm i -g @openai/codex
```

Verify it's on your PATH:

```bash
codex --version
```

If you see a version number, you're good. If not, ensure Node.js v18+ is installed and your npm global bin directory is in your `PATH`.

## 1) One-time shell setup (outside `codex>`)

Side: host shell

```bash
mkdir -p ~/demo/task-service-api ~/demo/task-portal-web
cd ~/demo/task-service-api && git init
cd ~/demo/task-portal-web && git init
```

Installation in both repos:

```bash
python3 -m venv venv
source venv/bin/activate
git clone https://github.com/Summoner-Network/summoner.git
bash summoner/build_sdk.sh setup --server python --venv ../venv 
python3 -m pip install -r summoner/agent_collab/requirements.txt
```

## 2) Start Summoner server

Side: Terminal 1

```bash
cd /path/to/summoner
python3 server.py
```

## 3) Start CodexA (backend agent)

Side: Terminal 2

```bash
source venv/bin/activate
python3 summoner/agent_collab/agent.py \
  --host 127.0.0.1 --port 8888 \
  --name CodexA --model gpt-5.3-codex \
  --workspace-root "$(pwd)" \
  --default-peer CodexB \
  --allowed-peer CodexB \
  --collab-mode open \
  --ui-backend prompt_toolkit \
  --viz-port 8788
```

## 4) Start CodexB (frontend agent, review gate)

Side: Terminal 3

```bash
source venv/bin/activate
python3 summoner/agent_collab/agent.py \
  --host 127.0.0.1 --port 8888 \
  --name CodexB --model gpt-5.3-codex \
  --workspace-root "$(pwd)" \
  --default-peer CodexA \
  --allowed-peer CodexA \
  --collab-mode review_only \
  --ui-backend prompt_toolkit \
  --viz-port 8789
```

## 5) Create backend quickly

Side: `CodexA` (`codex>` prompt)

```text
Create a minimal FastAPI app for a task service.
Requirements:
- endpoints:
  - GET /health -> {"ok": true}
  - GET /tasks -> list of tasks
  - POST /tasks with JSON body {title: string, dueDate: string|null} -> created task
- in-memory storage is fine
- add README with run instructions
- add requirements.txt
- run a quick local sanity check and summarize results
```

## 6) Create frontend quickly

Side: `CodexB` (`codex>` prompt)

```text
Create a minimal React + Vite app named task-portal-web.
Requirements:
- page title "Task Portal"
- form with fields title and due date
- button "Create Task" posts to http://127.0.0.1:8000/tasks
- below, render GET http://127.0.0.1:8000/tasks
- show errors in a visible red box
- add README with run instructions
- keep code simple and demo-ready
```

## 7) Collaboration moment #1 (API contract)

Side: `CodexB`

```text
/collab Ask CodexA to provide exact POST /tasks request/response JSON contract and field naming rules.
```

Side: `CodexB` (approval)

```text
/review
/approve next
```

## 8) Collaboration moment #2 (integration fix: field naming)

Side: `CodexB`

```text
/collab Tell CodexA frontend expects dueDate camelCase; ensure backend accepts dueDate and returns dueDate consistently, and provide one example payload.
```

Side: `CodexB` (approval)

```text
/review
/approve next
```

## 9) Collaboration moment #3 (CORS)

Side: `CodexB`

```text
/collab Ask CodexA to add CORS for local frontend dev origins http://127.0.0.1:5173 and http://localhost:5173.
```

Side: `CodexB` (approval)

```text
/review
/approve next
```

## 10) Frontend follow-through

Side: `CodexB`

```text
Use CodexA's final responses to update frontend fetch code and error handling as needed. Then give me a 3-line run checklist.
```

## 11) Optional runtime proof

Side: Terminal 4 (backend)

```bash
cd ~/demo/task-service-api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Side: Terminal 4 (frontend, new tab)

```bash
cd ~/demo/task-portal-web
npm install
npm run dev
```

## Narration cues (for Zoom)

- "Two independent repos, two specialized agents."
- "Frontend agent requests backend contract details via `/collab`."
- "Review-only mode provides human approval for remote execution."
- "Cross-repo integration bugs (field names + CORS) are fixed collaboratively."

## Fast fallback (if short on time)

If code generation runs long, do only:

1. contract `/collab`,
2. `dueDate` compatibility `/collab`,
3. CORS `/collab`,

and focus on streamed responses + review/approve workflow.

## What Each Startup Flag Actually Does

### Connectivity

- `--host`, `--port`
  - Summoner server endpoint.
  - wire effect: where this agent sends/receives collaboration traffic.

### Identity and peer policy

- `--name`
  - logical agent identity in protocol payloads.
- `--allowed-peer` (repeatable)
  - allowlist filter for inbound messages by sender name.

### Codex runtime

- `--model`
  - model used when starting Codex threads.
- `--codex-cwd`
  - working directory for Codex app-server execution.
- `--codex-bin`
  - optional explicit path to Codex binary (normally auto-detected from `CODEX_BIN`, PATH, or VS Code extension install).

### Workspace awareness

- `--workspace-root`
  - repo path used for `git status` snapshots.
- `--workspace-poll-seconds`
  - polling interval for workspace changes.
- `--max-files-per-update`
  - truncates changed-file list in awareness payload.
- `--no-share-workspace`
  - disables broadcasting workspace updates.

### Collaboration policy

- `--collab-mode open|review_only|locked`
  - controls inbound collab execution behavior.
- `--no-accept-remote-collabs`
  - hard kill-switch for inbound collab execution.
- `--default-peer`
  - allows shorthand `/collab <prompt>`.
- `--max-collab-chars`
  - prevents oversized prompt payloads.
- `--max-pending-review-collabs`
  - queue capacity for review-only mode.

### UX and streaming behavior

- `--ui-backend auto|threaded|prompt_toolkit`
  - chooses input engine.
- `--history-file`
  - prompt history path (prompt_toolkit mode).
- `--local-wait-interval`
  - local thinking animation cadence.
- `--collab-wait-interval`
  - requester wait-check interval.
- `--collab-wait-notify-seconds`
  - how often to emit wait reminders.
- `--collab-max-wait-seconds`
  - requester-side timeout for pending `/collab` requests.
  - default is `300`; set `0` to disable timeout.
- `--collab-stream-flush-seconds`
  - remote delta batching interval.
- `--show-awareness-live`
  - print awareness updates immediately.
- `--no-color`
  - disable ANSI colorized labels.

### Resilience and security

- `--state-file`
  - persisted state path.
- `--shared-secret`
  - enables HMAC signing for messages.
- `--sig-ttl-seconds`
  - timestamp and replay window checks.

### Visualization

- `--viz-port`
  - local Visionary HTTP port.
  - note: this is local UI binding, not Summoner server host.
- `--no-viz`
  - disable Visionary server.
- `--viz-no-browser`
  - do not auto-open browser window.

## In-Agent Commands with Side Effects

- `/help`
  - prints command list.
- `/context`
  - reads summarized awareness history from memory.
- `/pending` or `/collab-pending`
  - inspects pending outbound collab request map.
- `/mode <open|review|locked>`
  - updates in-memory mode and persists it to state file.
- `/review`
  - lists queued remote collab requests.
- `/approve <id|next>`
  - removes one item from review queue, executes remote turn.
- `/reject <id|next>`
  - removes one item from review queue, emits rejection response.
- `/collab <peer> <prompt>`
  - creates request id, stores pending metadata, sends remote request.
- `/local <prompt>` or plain text
  - executes local turn only.
- `/quit` or `/exit`
  - broadcasts offline awareness state and terminates.

## Collaboration Modes and Operational Policy

### `open`

- inbound collab requests execute immediately.
- best for trusted pair sessions with low latency requirements.

### `review_only`

- inbound collab requests queue until human approval.
- best default mode for teams.

### `locked`

- inbound collab requests are rejected.
- useful during sensitive refactors, incident response, or private debugging.

## Streaming, Waiting, and Terminal UX

Local path:

- you type prompt,
- spinner appears (`thinking...`),
- on first local token, spinner stops,
- assistant output streams or prints final text.

Remote path:

- requester sends `/collab`,
- responder executes and streams `collab_delta`,
- requester renders remote stream and then final collab status.
- in `prompt_toolkit` mode, input is intentionally paused while remote stream tokens are rendering,
- in threaded mode, local typing can continue while remote stream output is printing.
- if no terminal response arrives before `--collab-max-wait-seconds`, requester marks it timed out and clears pending state.
- if `--collab-max-wait-seconds=0`, the pause in `prompt_toolkit` mode remains until a terminal response arrives.

### Why prompt_toolkit is strongly recommended

- proper arrow-key editing/history,
- better coexistence between background async prints and active typing,
- fewer redraw artifacts.

## State Persistence and Recovery

Persisted fields:

- current collab mode,
- pending remote review queue entries,
- pending outbound request metadata.

On restart, agent reloads this state so work-in-progress coordination is not lost.

Files created (default names):

- `.agent_collab_state_<agent_name>.json`
- `.agent_collab_history_<agent_name>.txt`

## Security Model

Current controls:

1. channel separation (`awareness` vs `collab`),
2. sender allowlist,
3. prompt size limits,
4. manual review queue,
5. optional HMAC signature verification,
6. replay/time-window checks via timestamp + nonce.

Recommended deployment policy:

- localhost demos: allowlist may be sufficient,
- cross-machine usage: enable `--shared-secret` on all peers,
- production-like environments: use review mode by default.

## Visionary Graph and State Visualization

When enabled, Visionary shows state transitions and message routes.

Key clarification:

- Summoner `--host` is remote/server connectivity,
- Visionary `--viz-port` is local UI port.

If requested `--viz-port` is occupied:

- agent selects next available local port in a bounded range,
- startup logs include fallback information.
