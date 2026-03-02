# From One Agent to Many: An Intro to Summoner

This repo is a seminar walk-through that starts with a single agent and scales to interacting populations, flow-graphs, stateful routing, and (in later examples) travel between servers.

## Prerequisites

- Git
- Python 3.10+ (3.11 recommended)
- (Optional) Rust toolchain (`rustup`) if you want the Rust server option

## Quick start

```bash
git clone https://github.com/Summoner-Network/learn-summoner.git
cd learn-summoner
```

Most examples use a shared seminar server. The default in many examples is:

* **Seminar server:** `187.77.102.80:8888`

If your seminar host gives you a different host/port, update the example's `client.run(host=..., port=...)` call or the referenced config.

## Installation

### POSIX (macOS, Linux) using Bash

First install:

```bash
source build_sdk.sh setup --server python && bash install_requirements.sh
# or, if rustup is installed:
source build_sdk.sh setup && bash install_requirements.sh
```

Reset (clean environment):

```bash
source build_sdk.sh reset --server python && bash install_requirements.sh
# or, if rustup is installed:
source build_sdk.sh reset && bash install_requirements.sh
```

If `source build_sdk.sh setup/reset` does not work in your shell:

```bash
bash build_sdk.sh setup   # or: bash build_sdk.sh reset
source venv/bin/activate
bash install_requirements.sh
```

### Windows (PowerShell)

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\build_sdk_on_windows.ps1 setup
.\install_requirements_on_windows.ps1
```

## How to run the seminar examples

All examples live under `learn/`. Run them from the repo root.

### Template (sanity check)

This should open a browser window showing an empty flow graph:

```bash
python learn/template/agent.py
```

### Example 1 (minimal receive routes)

You should see an empty graph. This is a minimal “I can connect and respond” agent:

```bash
python learn/example_1/agent.py
```

### Example 8 (simple relationship transitions)

This example introduces relationship transitions like `register -> contact` based on received messages.
If other agents are connected to the same server, you should see state transitions:

```bash
python learn/example_8/agent.py
```

## Traveling between servers (Examples 13 and 14)

Examples 13 and 14 demonstrate a two-step setup:

1. Start an agent on a **local server** (so we can issue commands like `/travel` from a controlled environment).
2. Trigger **travel** to the **seminar server**, then participate there.

### Example 13 (travel showcase)

Open three terminals:

```bash
# Terminal 1: start a local server
python server.py
```

```bash
# Terminal 2: run the traveling agent (starts in "listen" mode locally)
python learn/example_13/agent.py
```

```bash
# Terminal 3: run the input agent that sends commands/messages
python agents/agent_InputAgent/agent.py
```

In the input agent terminal, type:

```text
/travel
```

What you should observe:

* The Example 13 agent starts in a **listen** state on your local server.
* When it receives `/travel`, it connects to the seminar server and begins interacting there.

### Example 14 (travel + LLM decisions + dashboard)

Example 14 builds on Example 13's orchestration and adds:

* An LLM-generated outside goal (once at startup)
* LLM-driven decisions for `move` vs `stay`
* Optional LLM classification for `good/bad/neutral` flags
* A local dashboard (auto-opened) showing encountered agents and states

Run the same three-terminal setup:

```bash
# Terminal 1
python server.py
```

```bash
# Terminal 2
python learn/example_14/agent.py
```

```bash
# Terminal 3
python agents/agent_InputAgent/agent.py
```

Then type:

```text
/travel
```

#### Selecting the LLM backend (Example 14)

Example 14 supports three backends:

* OpenAI (default)
* Claude
* OpenClaw

Examples:

```bash
# OpenAI (default)
python learn/example_14/agent.py --model openai

# Claude
python learn/example_14/agent.py --model claude --model-id claude-3-haiku-20240307

# OpenClaw
python learn/example_14/agent.py --model openclaw --openclaw-agent MyOpenClawAgent
```

Optional: enable LLM-based good/bad/neutral flag inference:

```bash
python learn/example_14/agent.py --llm-flags
```

## Common issues

### “Nothing happens” or “no other agents”

You may be alone on the server. Run multiple agents (in separate terminals) or coordinate with other attendees so you share the same server and port.

### Port already in use (local server)

Stop the process using the port (usually `server.py`) and rerun, or change the local server port if your setup supports it.

### Environment variables for Example 14

Example 14 expects API keys to be available (often via `.env`):

* `OPENAI_API_KEY` for OpenAI
* `ANTHROPIC_API_KEY` for Claude

If keys are missing or invalid, Example 14 falls back to heuristic behavior.

## Suggested seminar flow

1. Template: verify installation and visualize an empty graph
2. Example 1: connect and respond (basic receive routes)
3. Example 8: transitions and stateful routing
4. Example 13: travel between servers
5. Example 14: LLM-conditioned behavior + dashboard observability
