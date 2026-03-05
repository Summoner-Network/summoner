import argparse, json, asyncio
from typing import Any, Optional
import random
import time
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
# NEW vs Example 13:
# - Adds stdlib support for:
#   - timekeeping (time)
#   - background threads (threading)
#   - opening a browser (webbrowser)
#   - serving an HTTP dashboard (http.server)
# - This is used to expose a local "relations dashboard" in the browser.

from summoner.client import SummonerClient
from summoner.protocol import Test, Move, Stay, Event, Direction, Node, Action
from summoner.visionary import ClientFlowVisualizer
from summoner.curl_tools import CurlToolCompiler, SecretResolver
from dotenv import load_dotenv
# NEW vs Example 13:
# - Adds `.env` support and two helper classes (SecretResolver, CurlToolCompiler)
#   to build LLM backends that are called via curl-like templates.

load_dotenv()
# What this does:
# - Loads environment variables from a local `.env` file into the process.
# - This is how ANTHROPIC_API_KEY / OPENAI_API_KEY (etc.) become available to curl templates.


# =========================
# LLM framework (your style)
# =========================
# NEW vs Example 13:
# - Introduces an explicit LLM calling layer used for:
#   - deciding state transitions ("move" vs "stay")
#   - inferring whether the sender treats us as good/bad/neutral (optional)
#   - generating broadcast messages and status messages
# - The design keeps the rest of the agent logic minimal and makes LLM use a swap-able backend.

secrets = SecretResolver()
compiler = CurlToolCompiler(secrets=secrets)
# What this does:
# - SecretResolver reads $ENV vars (and possibly other secret sources, depending on your implementation).
# - CurlToolCompiler turns "curl templates" into callable async clients, resolving $VARS safely.

claude_client = compiler.parse(r"""
curl https://api.anthropic.com/v1/messages \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -H "X-Api-Key: $ANTHROPIC_API_KEY" \
  -m 60 \
  -d '{
    "model": "{{model_id}}",
    "max_tokens": {{max_tokens}},
    "temperature": {{temperature}},
    "messages": [
      { "role": "user", "content": "{{prompt}}" }
    ]
  }'
""")
# What this does:
# - Builds a callable Anthropic client from a raw curl template.
# - Placeholders {{model_id}}, {{max_tokens}}, {{temperature}}, {{prompt}} are substituted at call time.
# - $ANTHROPIC_API_KEY is resolved from the environment via SecretResolver.

gpt_client = compiler.parse(r"""
curl https://api.openai.com/v1/responses \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $OPENAI_API_KEY" \
    -d '{
        "model":"{{model_id}}",
        "input":"{{prompt}}"
    }'
""")
# What this does:
# - Builds a callable OpenAI client.
# - Same idea: template parameters are filled at runtime, key comes from $OPENAI_API_KEY.

def openclaw(agent: str, prompt: str) -> str:
    import subprocess
    cmd = [
        "openclaw",
        "agent",
        "--agent", agent,
        "--message", prompt
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return result.stderr.strip()
# What this does:
# - Adds a third backend: a local CLI tool ("openclaw") invoked via subprocess.
# - This backend is synchronous, so later the code runs it inside a thread via asyncio.to_thread.


# =========================
# Original orchestration data
# =========================
# Compared to Example 13:
# - This section keeps the same overall architecture (listen gate + relation tracking)
#   but prepares extra state for LLM decisions and dashboard monitoring.

state_lock = asyncio.Lock()

relations = {}
outside_view = {}
listening = True

AGENT_ID = f"ChangeMe_Agent_14_{random.randint(0,1000)}"
viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=random.randint(7777,8887))

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()

# =========================
# Minimal LLM + behavior layer
# =========================
# NEW vs Example 13:
# - Adds configuration knobs for which LLM backend to use and how to interpret messages.
# - Adds caching for repeated decisions on identical inputs.
# - Adds a notion of a single OUTSIDE_GOAL generated at startup that conditions behavior.

LLM_MODEL = "openai"          # set in main
MODEL_ID = None               # set in main (optional override)
OPENCLAW_AGENT = None         # set in main
USE_LLM_FLAGS = False         # set in main
OUTSIDE_GOAL = None           # generated once at startup

_decision_cache: dict[str, str] = {}
# What this does:
# - Memoizes decisions "move/stay" keyed by (kind, sender, message text).
# - Goal: avoid repeated LLM calls for the same stimulus and keep behavior stable.

# Dashboard tracking
_last_seen: dict[str, float] = {}
_last_message: dict[str, str] = {}
# NEW:
# - Tracks the last time we heard from each sender and the last message content.
# - This is used only for the local dashboard.

_dashboard_lock = threading.Lock()
_dashboard_snapshot = {
    "agent_id": AGENT_ID,
    "outside_goal": "",
    "listening": True,
    "rows": []
}
# NEW:
# - A thread-safe snapshot that the HTTP handler can serve without touching asyncio structures.
# - Key design choice: build a plain JSON-ish dict and guard it with a threading lock.

def _msg_text(msg: Any) -> str:
    if isinstance(msg, dict):
        v = msg.get("message", "")
        if isinstance(v, str):
            return v
        try:
            return json.dumps(v, sort_keys=True)
        except Exception:
            return str(v)
    return str(msg)
# NEW:
# - Normalizes "what is the message text?" so both caching and dashboard can treat messages uniformly.

def _cache_key(kind: str, msg: dict) -> str:
    return f"{kind}|{msg.get('from','')}|{_msg_text(msg)}"
# NEW:
# - Builds a stable cache key from:
#   - decision kind (register->contact, etc.)
#   - sender id
#   - normalized message text

def _fallback_goal() -> str:
    goals = [
        "Your interest is finding reliable collaborators. Your goal is forming a small coalition for mutual advantage.",
        "Your interest is information flow. Your goal is testing who shares useful signals versus noise.",
        "Your interest is stability. Your goal is reducing hostile interactions and keeping only constructive contacts.",
        "Your interest is influence. Your goal is building friendships that amplify your reach and isolating adversaries.",
        "Your interest is trade. Your goal is exchanging favors and tracking who reciprocates.",
    ]
    return random.choice(goals)
# NEW:
# - Provides a deterministic "no LLM available" baseline for OUTSIDE_GOAL.

def _fallback_move_decision(kind: str, msg: dict) -> str:
    t = _msg_text(msg).lower()
    pos = ["hello", "hi", "hey", "contact", "collab", "cooperate", "ally", "friend", "like", "good", "help"]
    neg = ["ban", "banned", "block", "hate", "enemy", "bad", "don't like", "dont like", "go away", "shut up"]
    score = 0
    for w in pos:
        if w in t:
            score += 1
    for w in neg:
        if w in t:
            score -= 2

    if kind in ("register->contact", "contact->friend", "good->very_good"):
        return "move" if score >= 1 else "stay"
    if kind in ("register->ban", "neutral->bad"):
        return "move" if score <= -1 else "stay"
    if kind == "neutral->good":
        return "move" if score >= 1 else "stay"
    return "stay"
# NEW:
# - Fallback decision logic to keep the agent functional without LLM access.
# - Encodes a simple keyword-based sentiment score and maps it to move/stay per decision kind.

def _fallback_flag(msg: dict) -> str:
    t = _msg_text(msg).lower()
    if any(w in t for w in ["ban", "banned", "block", "hate", "don't like", "dont like", "enemy"]):
        return "bad"
    if any(w in t for w in ["friend", "contact", "ally", "like", "good", "welcome"]):
        return "good"
    return "neutral"
# NEW:
# - Simple classification of how the sender seems to treat us: good/bad/neutral.

async def llm_text(system: str, user: str, *, max_tokens: int = 128, temperature: float = 0.0) -> str:
    prompt = f"{system}\n\n{user}".strip()

    if LLM_MODEL == "openai":
        try:
            s = await gpt_client.call({"model_id": MODEL_ID, "prompt": prompt})
            return s.response_json["output"][0]["content"][0]["text"]
        except Exception as e:
            client.logger.info(f"[llm:openai] fallback due to error: {e}")
            return ""

    if LLM_MODEL == "claude":
        try:
            s = await claude_client.call({
                "model_id": MODEL_ID,
                "prompt": prompt,
                "max_tokens": int(max_tokens),
                "temperature": float(temperature),
            })
            return s.response_json["content"][0]["text"]
        except Exception as e:
            client.logger.info(f"[llm:claude] fallback due to error: {e}")
            return ""

    if LLM_MODEL == "openclaw":
        if not OPENCLAW_AGENT:
            client.logger.info("[llm:openclaw] missing OPENCLAW_AGENT, fallback")
            return ""
        try:
            return await asyncio.to_thread(openclaw, OPENCLAW_AGENT, prompt)
        except Exception as e:
            client.logger.info(f"[llm:openclaw] fallback due to error: {e}")
            return ""

    return ""
# NEW:
# - Unifies all LLM access behind one async function returning plain text.
# - Implements:
#   - OpenAI backend via gpt_client.call(...)
#   - Anthropic backend via claude_client.call(...)
#   - openclaw backend via subprocess executed in a thread
# - Failure returns "" so callers can trigger fallback logic.

async def generate_outside_goal() -> str:
    system = (
        "You generate a single-line agent objective used as an external goal.\n"
        "Output exactly one line, in this exact format:\n"
        "Your interest is <concrete interest>. Your goal is <concrete goal>."
    )
    user = "Generate the interest and goal. Keep it concise and specific. No extra commentary."
    txt = (await llm_text(system, user, max_tokens=96, temperature=0.2)).strip()
    return txt if txt else _fallback_goal()
# NEW:
# - Generates OUTSIDE_GOAL once at startup to "condition" behavior.

async def decide_move(kind: str, msg: dict, context: str) -> str:
    key = _cache_key(kind, msg)
    if key in _decision_cache:
        return _decision_cache[key]
    # NEW:
    # - Caches move/stay decisions to reduce LLM usage and keep consistency.

    system = (
        "You are a strict controller deciding state transitions in a multi-agent simulation.\n"
        "Return exactly one token: move or stay.\n"
        "No punctuation, no explanation."
    )
    user = (
        f"Outside goal:\n{OUTSIDE_GOAL}\n\n"
        f"Decision kind: {kind}\n"
        f"Context: {context}\n\n"
        f"Incoming message from {msg.get('from')}:\n{_msg_text(msg)}\n\n"
        "Token:"
    )

    txt = (await llm_text(system, user, max_tokens=8, temperature=0.0)).strip().lower()
    if txt not in ("move", "stay"):
        txt = _fallback_move_decision(kind, msg)
        # What this does:
        # - If the LLM fails or returns malformed output, fall back to heuristic.

    _decision_cache[key] = txt
    return txt
# NEW:
# - Replaces Example 13's hard-coded triggers ("Hello", "I like you", ...)
#   with a policy decision conditioned on OUTSIDE_GOAL and a textual context string.

async def infer_flag_from_msg(msg: dict) -> str:
    if not USE_LLM_FLAGS:
        return _fallback_flag(msg)
    # NEW:
    # - Optional: keep the "good/bad/neutral" inference purely heuristic unless enabled by flag.

    system = (
        "You classify how the sender seems to treat us.\n"
        "Return exactly one token: good, bad, or neutral.\n"
        "No explanation."
    )
    user = (
        f"Outside goal:\n{OUTSIDE_GOAL}\n\n"
        f"Message from {msg.get('from')}:\n{_msg_text(msg)}\n\n"
        "Token:"
    )
    txt = (await llm_text(system, user, max_tokens=8, temperature=0.0)).strip().lower()
    return txt if txt in ("good", "bad", "neutral") else _fallback_flag(msg)
# NEW:
# - Allows the "to_them" state machine to be driven by an LLM classifier instead of exact strings.

async def generate_broadcast_message() -> str:
    stance = random.choice(["friendly", "neutral", "hostile"])
    system = (
        "You generate a short broadcast message to other agents.\n"
        "1-2 sentences. No emojis. No meta-talk.\n"
        "Reflect the outside goal and adopt the requested stance.\n"
        "Output only the message."
    )
    user = (
        f"Outside goal:\n{OUTSIDE_GOAL}\n\n"
        f"Stance: {stance}\n"
        "Write a message that invites reactions and reveals preferences."
    )
    txt = (await llm_text(system, user, max_tokens=96, temperature=0.7)).strip()
    if txt:
        return txt
    # What this does:
    # - Broadcast message is now goal-conditioned and varies by "stance".
    # - If the LLM fails, we fall back to a small template set.

    templates = {
        "friendly": [
            "I am looking for collaborators who trade useful signals. If you have something concrete, talk to me.",
            "I prefer steady allies over noise. If you want a reliable contact, say what you want and what you offer.",
        ],
        "neutral": [
            "I am evaluating who communicates clearly. Send a goal and a constraint, and I will respond.",
            "State your intent. I am tracking who is useful and who is disruptive.",
        ],
        "hostile": [
            "If you waste my time, I will remember it. Speak precisely or do not speak at all.",
            "I do not tolerate empty chatter. Offer value or expect distance.",
        ],
    }
    return random.choice(templates[stance])

async def generate_status_message(kind: str) -> str:
    system = (
        "You write one short direct message.\n"
        "1 sentence, optionally 2. No emojis. No meta-talk.\n"
        "Output only the message."
    )
    user = f"Outside goal:\n{OUTSIDE_GOAL}\n\nWrite a message for kind='{kind}'."

    txt = (await llm_text(system, user, max_tokens=64, temperature=0.6)).strip()
    if txt:
        return txt
    # NEW:
    # - All state-change messages ("You are my contact", etc.) are replaced by goal-conditioned text.

    if kind == "contact":
        return "I am keeping you as a contact because you seem constructive relative to my goal."
    if kind == "ban":
        return "I am banning you because your message conflicts with my goal and adds risk."
    if kind == "friend":
        return "I am treating you as a friend because your behavior aligns with my goal and you seem reliable."
    if kind == "good_flag":
        return "I like your direction. Stay constructive and we will cooperate."
    if kind == "bad_flag":
        return "I do not like your direction. Back off or expect resistance."
    return "Acknowledged."
# NEW:
# - Provides semantic message types rather than fixed strings.
# - The LLM (or fallback) translates those types into natural language.


def _refresh_dashboard_snapshot():
    rows = []
    now = time.time()

    # union of keys we have seen
    ids = set(relations.keys()) | set(outside_view.keys()) | set(_last_seen.keys())
    for sender_id in sorted(ids):
        rows.append({
            "agent": sender_id,
            "to_me": str(relations.get(sender_id, "")),
            "to_them": str(outside_view.get(sender_id, "")),
            "last_seen_s": int(now - _last_seen.get(sender_id, now)) if sender_id in _last_seen else None,
            "last_message": _last_message.get(sender_id, ""),
        })
    # NEW:
    # - Builds a table-like list where each row is one encountered sender.
    # - Includes both state machines plus recent activity.

    with _dashboard_lock:
        _dashboard_snapshot["agent_id"] = AGENT_ID
        _dashboard_snapshot["outside_goal"] = OUTSIDE_GOAL or ""
        _dashboard_snapshot["listening"] = bool(listening)
        _dashboard_snapshot["rows"] = rows
    # NEW:
    # - Publishes a thread-safe snapshot that the HTTP server can serve immediately.

def start_dashboard_server(
    port_range: tuple[int, int] = (4444, 5555),
    tries: int = 40
) -> tuple[ThreadingHTTPServer, int]:
    import socket
    # NEW:
    # - Creates a small local web UI that auto-refreshes from /state every 1 second.

    html = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Summoner Agent Relations</title>
  <style>
    body { font-family: sans-serif; margin: 16px; }
    .meta { margin-bottom: 12px; }
    .goal { white-space: pre-wrap; padding: 8px; border: 1px solid #ddd; border-radius: 6px; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 8px; vertical-align: top; }
    th { background: #f5f5f5; text-align: left; }
    .small { color: #666; font-size: 12px; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }
  </style>
</head>
<body>
  <div class="meta">
    <div><b>Agent:</b> <span id="agent_id" class="mono"></span></div>
    <div><b>Listening:</b> <span id="listening"></span></div>
    <div class="small">Auto-refresh every 1s</div>
  </div>

  <div>
    <b>Outside goal</b>
    <div id="goal" class="goal mono"></div>
  </div>

  <h3>Encountered agents</h3>
  <table>
    <thead>
      <tr>
        <th>Agent</th>
        <th>to_me</th>
        <th>to_them</th>
        <th>last_seen (s ago)</th>
        <th>last_message</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>

<script>
async function refresh() {
  const r = await fetch("/state");
  const s = await r.json();

  document.getElementById("agent_id").textContent = s.agent_id || "";
  document.getElementById("listening").textContent = String(!!s.listening);
  document.getElementById("goal").textContent = s.outside_goal || "";

  const tb = document.getElementById("rows");
  tb.innerHTML = "";
  for (const row of (s.rows || [])) {
    const tr = document.createElement("tr");

    function td(text, mono=false) {
      const x = document.createElement("td");
      x.textContent = text == null ? "" : String(text);
      if (mono) x.className = "mono";
      return x;
    }

    tr.appendChild(td(row.agent, true));
    tr.appendChild(td(row.to_me, true));
    tr.appendChild(td(row.to_them, true));
    tr.appendChild(td(row.last_seen_s));
    tr.appendChild(td(row.last_message));
    tb.appendChild(tr);
  }
}

setInterval(refresh, 1000);
refresh();
</script>
</body>
</html>
"""
    # What this does:
    # - Defines the full HTML page in a string literal.
    # - The page fetches JSON from /state and renders it as a table.

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/" or self.path.startswith("/?"):
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == "/state":
                with _dashboard_lock:
                    payload = json.dumps(_dashboard_snapshot).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            self.send_response(404)
            self.end_headers()

        def log_message(self, format, *args):
            return
    # What this does:
    # - Serves two endpoints:
    #   - GET /      : HTML dashboard
    #   - GET /state : JSON snapshot
    # - Suppresses HTTP request logging for cleaner terminal logs.

    def _pick_free_port(lo: int, hi: int, tries_: int) -> int:
        for _ in range(tries_):
            p = random.randint(lo, hi)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind(("127.0.0.1", p))
                    return p
                except OSError:
                    continue
        raise RuntimeError(f"Could not find a free port in [{lo},{hi}] after {tries_} tries")
    # What this does:
    # - Picks a random available port in a range by attempting to bind locally.
    # - If binding fails, it retries.

    lo, hi = port_range
    last_err: Optional[Exception] = None
    for _ in range(max(1, tries)):
        port = _pick_free_port(lo, hi, 1)
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            return server, port
        except OSError as e:
            last_err = e
            continue

    raise RuntimeError(f"Failed to start dashboard server in range [{lo},{hi}]") from last_err
    # What this does:
    # - Starts the HTTP server in a daemon thread.
    # - Returns (server, chosen_port) so main can open the browser to the right URL.


# =========================
# Hooks and routes (minimal edits)
# =========================
# Compared to Example 13:
# - Same structure, but:
#   - validate() now also updates dashboard last_seen/last_message
#   - upload_states() and download_states() refresh the dashboard snapshot
#   - the transition logic is replaced by LLM-driven decisions and inferences
#   - clock/reputation messages are now LLM-generated instead of fixed strings

@client.hook(direction=Direction.RECEIVE, priority=0)
async def validate(msg: Any) -> Optional[dict]:
    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg): return
    content: Any = msg["content"]

    if content == "/travel" and listening:
        client.logger.info(f"[hook:recv] /travel instruction received")
        return content
    if not isinstance(content, dict): return
    if content.get("to", "") not in [None, AGENT_ID]: return
    if "from" not in content:
        client.logger.info(f"[hook:recv] missing content.from")
        return

    # Track last seen/message for dashboard
    sender = content.get("from")
    if isinstance(sender, str) and sender:
        _last_seen[sender] = time.time()
        _last_message[sender] = _msg_text(content)
    # NEW vs Example 13:
    # - Records activity so the dashboard can show recency + last message.

    return content

@client.hook(direction=Direction.RECEIVE, priority=1)
async def check_sender(content: dict) -> Optional[dict]:
    if content == "/travel" and listening:
        client.logger.info(f"[hook:recv] /travel instruction received")
        return content
    elif any(s == content.get("from","")[:len(s)] for s in [
        "ChangeMe_Agent_6",
        "ChangeMe_Agent_7",
        "ChangeMe_Agent_8",
        "ChangeMe_Agent_9",
        "ChangeMe_Agent_10",
        "ChangeMe_Agent_11",
        "ChangeMe_Agent_12",
        "ChangeMe_Agent_13",
        "ChangeMe_Agent_14",
    ]):
        return content
    else:
        client.logger.info(f"[hook:recv] reject 'from':{content.get('from')} | 'type':{content.get('type')}")
    # CHANGE vs Example 13:
    # - Allowlist extended to include "ChangeMe_Agent_14" prefix.

@client.upload_states()
async def upload_states(msg: Any) -> Any:
    global relations, outside_view
    print(msg)
    # Same debugging print as Example 13.

    if listening:
        viz.push_states(["listen"])
        _refresh_dashboard_snapshot()
        return "listen"
        # CHANGE vs Example 13:
        # - Also refreshes dashboard while listening, so the web UI stays accurate.

    sender_id = msg.get("from")
    if not sender_id:
        _refresh_dashboard_snapshot()
        return
        # NEW:
        # - Even if sender_id is missing, we still refresh dashboard snapshot.

    async with state_lock:
        relations.setdefault(sender_id, "register")
        outside_view.setdefault(sender_id, "neutral")

    view_states = {f"1:{k}": v for k, v in relations.items()}
    view_states.update({f"2:{k}": v for k, v in outside_view.items()})
    async with state_lock:
        viz.push_states(view_states)

    _refresh_dashboard_snapshot()
    # NEW:
    # - Updates the dashboard after any state upload update.

    return {f"to_me:{sender_id}": relations[sender_id], f"to_them:{sender_id}": outside_view[sender_id]}

@client.receive(route="listen --> register")
async def on_register(msg: Any) -> Optional[Event]:
    global listening
    if listening and msg == "/travel":
        await client.travel_to(host="187.77.102.80", port=8888)
        async with state_lock:
            listening = False
        _refresh_dashboard_snapshot()
        # NEW vs Example 13:
        # - Dashboard immediately reflects that listening=False.

        return Move(Trigger.ok)

contact_list = []
@client.receive(route="register --> contact")
async def on_register(msg: Any) -> Optional[Event]:
    global contact_list
    decision = await decide_move(
        "register->contact",
        msg,
        context="Move if the sender seems constructive and potentially cooperative."
    )
    # CHANGE vs Example 13:
    # - Replaces `if msg["message"] == "Hello"` with LLM-conditioned policy.

    if decision == "move":
        async with state_lock:
            contact_list.append(msg["from"])
        return Move(Trigger.ok)

ban_list = []
@client.receive(route="register --> ban")
async def on_register(msg: Any) -> Optional[Event]:
    global ban_list
    decision = await decide_move(
        "register->ban",
        msg,
        context="Move if the sender seems hostile, disruptive, or risky."
    )
    # CHANGE:
    # - Replaces hard-coded "I don't like you" trigger with LLM decision.

    if decision == "move":
        async with state_lock:
            ban_list.append(msg["from"])
        return Move(Trigger.ok)

friend_list = []
@client.receive(route="contact --> friend")
async def on_register(msg: Any) -> Optional[Event]:
    global friend_list
    decision = await decide_move(
        "contact->friend",
        msg,
        context="Move if the sender shows clear alignment or trust, not just small talk."
    )
    # CHANGE:
    # - Replaces hard-coded "I like you" trigger with LLM decision.

    if decision == "move":
        async with state_lock:
            friend_list.append(msg["from"])
        return Move(Trigger.ok)

to_them_list = []
@client.receive(route="neutral --> good")
async def on_register(msg: Any) -> Optional[Event]:
    global to_them_list
    flag = await infer_flag_from_msg(msg)
    # CHANGE vs Example 13:
    # - Instead of requiring msg["message"] == "You are my contact",
    #   it infers "good" from the message content (LLM or fallback heuristic).

    if flag == "good":
        async with state_lock:
            to_them_list.append({"to": msg["from"], "status": "good"})
        return Move(Trigger.ok)

@client.receive(route="neutral --> bad")
async def on_register(msg: Any) -> Optional[Event]:
    global to_them_list
    flag = await infer_flag_from_msg(msg)
    # CHANGE:
    # - Likewise, "bad" is inferred rather than matched exactly to a phrase.

    if flag == "bad":
        async with state_lock:
            to_them_list.append({"to": msg["from"], "status": "bad"})
        return Move(Trigger.ok)

@client.receive(route="good --> very_good")
async def on_register(msg: Any) -> Optional[Event]:
    global to_them_list
    decision = await decide_move(
        "good->very_good",
        msg,
        context="Move if the sender escalates trust and long-term alignment."
    )
    # CHANGE vs Example 13:
    # - Previously: exact match "You are my friend".
    # - Now: policy decision (move/stay) conditioned by goal and context.

    if decision == "move":
        async with state_lock:
            to_them_list.append({"to": msg["from"], "status": "good"})
        return Move(Trigger.ok)

@client.download_states()
async def download_states(possible_states: dict[str, list[Node]]) -> None:
    if listening:
        viz.push_states(["listen"])
        _refresh_dashboard_snapshot()
        return
        # CHANGE vs Example 13:
        # - Dashboard stays in sync even while listening.

    if isinstance(possible_states, list):
        possible_states = {"default": possible_states}
        # Same compatibility shim as Example 13.

    global relations, outside_view
    for sender_id_, sender_states in possible_states.items():
        if sender_id_.startswith("to_me:"):
            sender_id = sender_id_.split("to_me:")[1]
            states = [s for s in sender_states if str(s) != str(relations.get(sender_id, ""))]
            if states:
                async with state_lock:
                    relations[sender_id] = states[0]

        if sender_id_.startswith("to_them:"):
            sender_id = sender_id_.split("to_them:")[1]
            states = [s for s in sender_states if str(s) != str(outside_view.get(sender_id, ""))]
            if states:
                async with state_lock:
                    outside_view[sender_id] = states[0]

    view_states = {f"1:{k}": v for k, v in relations.items()}
    view_states.update({f"2:{k}": v for k, v in outside_view.items()})
    async with state_lock:
        viz.push_states(view_states)

    _refresh_dashboard_snapshot()
    # NEW:
    # - Dashboard table updates whenever states change.

@client.receive(route="register")
async def on_register(msg: Any) -> Event:
    client.logger.info(msg)
    return Test(Trigger.ok)
# Unchanged "sink" handlers for each base state.

@client.receive(route="contact")
async def on_contact(msg: Any) -> Event:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="friend")
async def on_friend(msg: Any) -> Event:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="ban")
async def on_ban(msg: Any) -> Event:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.send(route="clock")
async def send_on_clock() -> Optional[str]:
    async with state_lock:
        if listening:
            await asyncio.sleep(0.1)
            return
        viz.push_states(["clock"])
    await asyncio.sleep(3)
    text = await generate_broadcast_message()
    return {"message": text, "to": None}
    # CHANGE vs Example 13:
    # - Broadcast content is no longer fixed "Hello".
    # - It is now generated (LLM or templates), conditioned on OUTSIDE_GOAL and a random stance.

@client.send(route="reputation", multi=True)
async def send_on_clock() -> list[str]:
    async with state_lock:
        if listening:
            await asyncio.sleep(0.1)
            return []
    await asyncio.sleep(3)
    async with state_lock:
        viz.push_states(["reputation"])

    out = []
    for d in to_them_list:
        if d["status"] == "good":
            msg_txt = await generate_status_message("good_flag")
        else:
            msg_txt = await generate_status_message("bad_flag")
        out.append({"to": d["to"], "message": msg_txt})
    return out
    # CHANGE vs Example 13:
    # - Instead of sending "I like you" / "I don't like you",
    #   it sends richer, goal-conditioned reputation messages ("good_flag" / "bad_flag").

@client.send(route="register --> contact", multi=True, on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_from_register_to_contact():
    global contact_list
    try:
        msg_txt = await generate_status_message("contact")
        return [{"to": contact_id, "message": msg_txt} for contact_id in contact_list]
    finally:
        async with state_lock:
            contact_list = []
    # CHANGE vs Example 13:
    # - Status text is generated (LLM/templates) rather than fixed "You are my contact".
    # - Still clears contact_list after sending, keeping "one-shot" semantics.

@client.send(route="register --> ban", multi=True, on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_from_register_to_contact():
    global ban_list
    try:
        msg_txt = await generate_status_message("ban")
        return [{"to": banned_id, "message": msg_txt} for banned_id in ban_list]
    finally:
        async with state_lock:
            ban_list = []
    # CHANGE:
    # - Generated ban message rather than "You are banned".

@client.send(route="contact --> friend", multi=True, on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_from_register_to_contact():
    global friend_list
    try:
        msg_txt = await generate_status_message("friend")
        return [{"to": friend_id, "message": msg_txt} for friend_id in friend_list]
    finally:
        async with state_lock:
            friend_list = []
    # CHANGE:
    # - Generated friend message rather than "You are my friend".

@client.hook(direction=Direction.SEND)
async def sign(msg: Any) -> Optional[dict]:
    client.logger.info(f"[hook:send] sign {AGENT_ID}")
    if isinstance(msg, str):
        msg = {"message": msg}
    if not isinstance(msg, dict):
        return
    msg.update({"from": AGENT_ID})
    return msg
    # Unchanged in purpose:
    # - Ensures outbound messages include our agent id in "from".
    # - Also normalizes plain strings into {"message": "..."}.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    parser.add_argument("--model", choices=["openai", "claude", "openclaw"], default="openai", help="Which backend to use for goal/messages/decisions.")
    parser.add_argument("--model-id", default=None, help="Model id to use for openai/claude. If omitted, defaults are used.")
    parser.add_argument("--openclaw-agent", default=None, help="OpenClaw agent name (only used when --model openclaw).")
    parser.add_argument("--llm-flags", action="store_true", help="If set, use the LLM to infer good/bad/neutral flags.")
    args = parser.parse_args()
    # NEW vs Example 13:
    # - Adds CLI flags controlling the LLM backend and behavior.
    # - `--llm-flags` specifically enables LLM-based good/bad/neutral classification.

    # Set globals
    LLM_MODEL = args.model
    OPENCLAW_AGENT = args.openclaw_agent
    USE_LLM_FLAGS = bool(args.llm_flags)
    # What this does:
    # - Routes all future llm_text(...) calls to the selected backend.
    # - Enables/disables LLM flag inference.

    if args.model_id:
        MODEL_ID = args.model_id
    else:
        # Reasonable defaults
        if LLM_MODEL == "openai":
            MODEL_ID = "gpt-4o-mini"
        elif LLM_MODEL == "claude":
            MODEL_ID = "claude-3-haiku-20240307"
        else:
            MODEL_ID = ""  # openclaw does not use model id
    # What this does:
    # - Picks a default model id per backend if none is provided.

    # Outside goal once at startup
    OUTSIDE_GOAL = asyncio.run(generate_outside_goal())
    client.logger.info(f"[outside_goal] {OUTSIDE_GOAL}")
    # NEW vs Example 13:
    # - Generates the external goal once and logs it.
    # - This goal now conditions:
    #   - decide_move(...) for transitions
    #   - generate_broadcast_message(...) for clock broadcasts
    #   - generate_status_message(...) for directed messages

    # Start the dashboard server and open it
    _refresh_dashboard_snapshot()
    server, dash_port = start_dashboard_server()
    webbrowser.open(f"http://127.0.0.1:{dash_port}/")
    # NEW vs Example 13:
    # - Starts the local HTTP dashboard and opens it in the browser.
    # - The dashboard displays:
    #   - agent id, listening state, outside goal
    #   - per-sender to_me/to_them + recency + last message

    # Start visual window (browser) and build graph from dna
    viz.attach_logger(client.logger)
    viz.start(open_browser=True)
    viz.set_graph_from_dna(json.loads(client.dna()), parse_route=client_flow.parse_route)
    viz.push_states(["listen"])
    # Same as Example 13:
    # - Visualizer still starts in "listen" mode.

    # client.run usually blocks forever, so anything "after" it may not execute.
    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
    # RUNTIME BEHAVIOR (what changed from Example 13):
    # - The agent still boots into "listen" and only activates on "/travel".
    # - Once active:
    #   - Relationship transitions are no longer exact string matches.
    #     They are policy decisions (LLM or heuristic fallback) conditioned on OUTSIDE_GOAL.
    #   - "to_them" inference can be heuristic by default, or LLM-based with --llm-flags.
    #   - Broadcasts and directed status messages become goal-conditioned natural language.
    # - A local dashboard now runs in parallel showing:
    #   - both state machines per sender
    #   - last-seen time and last message
    #   - outside goal and listening flag
