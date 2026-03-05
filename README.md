<p align="center">
  <a href="https://summoner.org">
    <img src="assets/img/Github_graphic.png" alt="Summoner Logo" width="400"/>
  </a>
</p>
<p align="center">
  <img width="500px" src="assets/img/summoner-logo-mark-gold-type.png" />
</p>
<p align="center">
  <strong>The coordination protocol for autonomous AI agents</strong><br>
  <em>Build, run, and coordinate agents across machines and organizations over a WAN</em>
</p>

<p align="center">
  <a href="https://github.com/Summoner-Network/summoner/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/Summoner-Network?style=social"></a>
  <a href="https://github.com/Summoner-Network/summoner-agents"><img alt="Agent blueprints" src="https://img.shields.io/badge/agent_blueprints-58-blue?style=flat-square"></a>
  <a href="https://discord.gg/NnrBJwFtEn"><img alt="Discord" src="https://img.shields.io/discord/1348699433365147668?style=flat-square&label=discord&color=5865F2"></a>
  <a href="https://github.com/Summoner-Network/summoner/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache_2.0-green?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9+-3776AB?style=flat-square&logo=python&logoColor=white"></a>
</p>

<p align="center">
  <a href="https://summoner.org">Website</a> · 
  <a href="https://github.com/Summoner-Network/summoner-docs">Docs</a> · 
  <a href="https://github.com/Summoner-Network/summoner-agents">Agent Blueprints</a> · 
  <a href="https://discord.gg/NnrBJwFtEn">Discord</a> · 
  <a href="https://twitter.com/SummonerNetwork">Twitter</a>
</p>

Summoner is an open protocol and SDK for **networked AI agents**. Most agent frameworks coordinate reasoning inside a single process or organization. Summoner coordinates **independent agents** — running as separate processes — that connect to shared relay servers, like players joining an MMO world. Agents can **travel between servers**, **form peer relationships**, and **coordinate across organizational boundaries**, with self-assigned identities and built-in mechanisms for learning and adapting behavior.

<p align="center">
Python SDK. Rust server. Easy deployment via the desktop app. No central orchestrator. 
</p>
<p align="center">
In other words: Your agents, your code, your network.
</p>


<p align="center">
  <img width="500px" src="assets/img/desktop_screenshot_rounded.png" alt="Desktop map visual"/>
  <br>
  <em>Map view provided by the Summoner Desktop app to monitor your agent network across the globe.</em>
</p>


## Quickstart

### Clone

Clone this repo:

```bash
git clone https://github.com/Summoner-Network/summoner.git
cd summoner
```

### Install

#### POSIX (macOS, Linux) using Bash

```bash
source build_sdk.sh setup --server python && bash install_requirements.sh
```

If `source build_sdk.sh setup` does not work in your shell:

```bash
bash build_sdk.sh setup --server python
source venv/bin/activate && bash install_requirements.sh
```

#### Windows (PowerShell)

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\build_sdk_on_windows.ps1 setup
.\install_requirements_on_windows.ps1
```

### Run Agents

Choose your starting point:

- ⚡ **Fastest way to see Summoner working** — visual sanity check (recommended for first-time users)  
- 🚀 **Run real networked agents** — full multi-agent interaction  

#### ⚡ Fastest Way to See Summoner Working

Want to verify your setup and visualize an agent immediately?

**Run the template agent**

```bash
python learn/webinar_01/template/agent.py
```

**What you’ll see**

- Browser opens with the flow graph  
- Agent connects to the network  
- Environment is fully validated  

This is the quickest way to confirm everything is working.

**Next steps**

- Try `learn/webinar_01/example_1` for basic receive routes  
- Try `learn/webinar_01/example_8` for state transitions  
- Or jump below to run fully interactive agents  

**🚀 Run Real Networked Agents**

Run two independent agents and watch them interact.

In Terminal 1:

```bash
bash get_agent.sh EchoAgent_0
bash get_agent.sh InputAgent
python server.py
```

In Terminal 2:

```bash
source venv/bin/activate
python agents/agent_EchoAgent_0/agent.py
```

In Terminal 3:

```bash
source venv/bin/activate
python agents/agent_EchoAgent_0/agent.py
```

In Terminal 4:

```bash
source venv/bin/activate
python agents/agent_InputAgent/agent.py
```

In Terminal 4, type `Hello`:
```text
> Hello
```

You will see the two `EchoAgent_0` agents interact:
- In Terminal 2:
  ```log
  2026-03-05 08:28:13.897 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:55043).
  ```
- In Terminal 3:
  ```log
  2026-03-05 08:28:32.963 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:55036).
  ```
- In Terminal 4:
  ```log
  [Received] Hello
  [Received] Hello
  ```

**What just happened**

- Two independent processes connected to a shared relay  
- They discovered each other automatically  
- Messages flowed without HTTP or orchestration  

You now have live, networked agents exchanging messages.

## Where to go next

| I want to… | Start here |
|---|---|
| **Learn step by step** | [`summoner`](https://github.com/Summoner-Network/summoner) — seminar from single agent to cross-server travel |
| **Run agents immediately** | [`summoner-agents`](https://github.com/Summoner-Network/summoner-agents) — 58 ready-to-run blueprints from beginner to expert |
| **Build my own agent from scratch** | [`summoner-sdk`](https://github.com/Summoner-Network/summoner-sdk) — define a `build.txt` recipe and compose your own SDK |
| **Read the full docs** | [`summoner-docs`](https://github.com/Summoner-Network/summoner-docs) — guides, API reference, architecture |
| **Hack on core infrastructure** | [`summoner-core`](https://github.com/Summoner-Network/summoner-core) — protocol primitives, Rust server, client runtime |
| **Use the desktop UI** | [`summoner-desktop`](https://github.com/Summoner-Network/summoner-desktop) — visual interface for launching servers and agents |
| **Read the protocol spec** | [`summoner-standard`](https://github.com/Summoner-Network/summoner-standard) — formal specification and conformance requirements |

## The Programming Model

Define behavior with decorators. Summoner handles networking, transport, and concurrency.

```python
# test.py
from summoner.client import SummonerClient
import asyncio

class MyAgent(SummonerClient):
    def __init__(self):
        super().__init__()
        self.queue = asyncio.Queue()

agent = MyAgent()

# Handle incoming messages
@agent.receive(route="chat")
async def on_message(msg: dict) -> None:
    print(f"Got: {msg}")
    agent.queue.put_nowait(msg)      # forward to send loop

# Produce outgoing messages
@agent.send(route="chat")
async def respond() -> str:
    msg = await agent.queue.get()
    return f"Echo: {msg}"

agent.run(host="127.0.0.1", port=8888)
```

No HTTP server to build. No executor to deploy. No orchestrator in the middle. The Rust relay handles transport, fan-out, and backpressure — your agent just defines behavior.

To run the example above (see `test.py`):
```bash
# Terminal 1
python server.py

# Terminal 2
python test.py

# Terminal 3
python agents/agent_InputAgent/agent.py
> Hello
```


## What Makes Summoner Different

🔁 **Agent mobility** — Agents can `travel_to(host, port)` at runtime, moving between relay servers while preserving identity and resuming conversations.

🔄 **True duplex messaging** — `@receive` and `@send` run independently in parallel. Events, queues, and hooks connect them. No request/response bottleneck.

🧠 **Explicit state machines** — Routes compose into typed automata with hooks for validation, signing, and replay protection at each transition.

🌐 **Relay servers as shared spaces** — Servers are meeting points, not controllers. Agents connect outbound (NAT/firewall friendly) and interact with whoever else is in the room. Any party can run a relay.

## How Summoner Compares

| | **Summoner** | **MCP** (Anthropic) | **A2A** (Google) | **LangGraph** |
|---|---|---|---|---|
| **Scope** | SDK + relays for live agent networking | Model-to-tool/data protocol | Agent discovery + server executors | Graph DSL for workflows |
| **Agent mobility** | Yes (`travel()` / resume) | N/A | No (bound to server) | N/A (in-process) |
| **Messaging** | Direct, duplex events; parallel sends | Host-routed | Task/stream over HTTP | In-app node calls |
| **Orchestration** | In the agent (routes/flows/automata) | Host agent | Host + server executor | App graph engine |
| **Identity** | Hooks for user-defined DID | Host-managed | Registry/OIDC + Agent Cards | None |
| **Cross-org** | Designed for untrusted, cross-boundary | Single-organization | Shared enterprise security | Single-process |

Summoner doesn't replace these tools — it **connects them across the network**. Use LangChain for reasoning, CrewAI for task planning, MCP for tool access, and Summoner for the hard part: live, cross-machine agent coordination.

## 58 ready-to-run blueprints

You can directly import agents from the [`summoner-agents`](https://github.com/Summoner-Network/summoner-agents) repo into this repo:

```bash
bash get_agent.sh --list
bash get_agent.sh <agent_name>
```

The `summoner-agents` repo ships agents from beginner to advanced:

| Category | Examples | What you'll learn |
|---|---|---|
| **Core Messaging** | SendAgent, RecvAgent, EchoAgent, StreamAgent | `@send`, `@receive`, `@hook`, LLM streaming |
| **Chat & Control** | ChatAgent 0–3 | Interactive UI, remote commands, automaton routing |
| **Feedback** | ReportAgent, ExamAgent | Queued reports, timed Q&A flows |
| **Graph-Based** | CatArrowAgent, CatTriangleAgent, CatUpdateAgent | State machines, decision flows, supply chain modeling |
| **Connectors** | ConnectAgent, OrchBridgeAgent | SQLite, LangChain, CrewAI integration |
| **MCP** | MCPArXivAgent, MCPPubMedAgent, MCPGitHubAgent | MCP tool servers for external services |
| **Security** | HSAgent, HSSellAgent, HSBuyAgent | Nonce handshakes, DID identity, negotiation |
| **MMO Game** | GameMasterAgent, GamePlayerAgent | Multiplayer 2D sandbox over the protocol |
| **DNA** | DNACloneAgent, DNAMergeAgent | Agent cloning, merging, genetic composition |

<p align="center">
  <img width="600px" src="https://github.com/Summoner-Network/summoner-agents/blob/main/assets/mov2gif/gifs/demo_mmo_2_framed.gif" alt="MMO game agents"/>
  <br>
  <em>Multiplayer game running entirely over the Summoner protocol</em>
</p>

### Integrations

Summoner is the **transport and coordination layer** — your agent code calls whatever LLMs, APIs, and frameworks you already use:

| Integration | How it works |
|---|---|
| **OpenAI / Claude / local LLMs** | Call model APIs directly from `@receive` or `@send` handlers |
| **LangChain** | Run LangChain chains inside Summoner routes via `OrchBridgeAgent` |
| **CrewAI** | Route Summoner payloads to CrewAI crews for multi-step reasoning |
| **MCP** | Connect to any MCP tool server (arXiv, PubMed, GitHub, Notion, Reddit) |
| **SQLite / databases** | Persistent agent memory via `ConnectAgent` patterns |

## Why this exists

The future of AI is not single agents running inside a single process. It is **agent populations** coordinating across machines, organizations, and trust boundaries — forming relationships, negotiating outcomes, and building reputation over time.

Today's frameworks solve the single-organization problem well. But very few are building the protocol layer for what comes next: autonomous agents that coordinate across the open internet the way web services coordinate over HTTP.

That's what Summoner is for.

## Contributing

We welcome contributions at every level:

- **🌱 First time?** Check out issues labeled [`good-first-issue`](https://github.com/Summoner-Network/summoner/labels/good-first-issue) — each one includes context and guidance
- **🧠 Build an agent?** Add it to [`summoner-agents`](https://github.com/Summoner-Network/summoner-agents) — follow the [agent folder structure](https://github.com/Summoner-Network/summoner-agents#desktop-app-compatibility)
- **🔧 Extend the SDK?** Use the [`extension-template`](https://github.com/Summoner-Network/extension-template) to create a module
- **📝 Improve docs?** PRs welcome on [`summoner-docs`](https://github.com/Summoner-Network/summoner-docs)
<!-- - **💡 Propose a change?** Open an issue or start a [Discussion](https://github.com/Summoner-Network/summoner/discussions) -->

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines.

## Community

- 💬 **[Discord](https://discord.gg/NnrBJwFtEn)** — Chat with the team and other builders
- 🐦 **[Twitter](https://twitter.com/SummonerNetwork)** — Updates and announcements
- 📧 **[info@summoner.org](mailto:info@summoner.org)** — Reach us directly

<p align="center">
  <strong>If Summoner is useful to you, please consider giving it a ⭐</strong><br>
  It helps other developers find the project.
</p>
