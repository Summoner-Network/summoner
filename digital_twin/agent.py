import argparse, json, asyncio, random, threading, time, os
from typing import Any, Optional
from pathlib import Path
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import webbrowser

from summoner.client import SummonerClient
from summoner.protocol import Event, Direction, Move, Stay, Action, Node
from summoner.visionary import ClientFlowVisualizer
from summoner.curl_tools import CurlToolCompiler, SecretResolver
from summoner.gpt_guardrails.cost import count_chat_tokens
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
ID_PATH = BASE_DIR / "id.json"
OWNER_ID_PATH = BASE_DIR / "owner_id.json"
WORKSPACE_DIR = BASE_DIR / "workspace"
DAYBYDAY_DIR = WORKSPACE_DIR / "daybyday"
MEMORY_DIR = WORKSPACE_DIR / "memory"

load_dotenv()

with ID_PATH.open("r", encoding="utf-8") as f:
    AGENT_ID_OBJ = json.load(f)

AGENT_ID = AGENT_ID_OBJ.get("name", "DigitalTwinAgent")

secrets = SecretResolver()
compiler = CurlToolCompiler(secrets=secrets)

# OpenAI client with structured JSON output
openai_client = compiler.parse(r"""
curl https://api.openai.com/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model": "{{model_id}}",
    "input": [
      {"role": "system", "content": [{"type": "input_text", "text": "{{system}}"}]},
      {"role": "user", "content": [{"type": "input_text", "text": "{{user}}"}]}
    ],
    "text": { "format": { "type": "json_object" } }
  }'
""")

openai_text_client = compiler.parse(r"""
curl https://api.openai.com/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model": "{{model_id}}",
    "input": [
      {"role": "system", "content": [{"type": "input_text", "text": "{{system}}"}]},
      {"role": "user", "content": [{"type": "input_text", "text": "{{user}}"}]}
    ]
  }'
""")

notion_list_blocks = compiler.parse(r"""
curl -H "Authorization: Bearer $NOTION_TOKEN" \
     -H "Notion-Version: 2022-06-28" \
     "https://api.notion.com/v1/blocks/$NOTION_PAGE_ID/children?page_size=100"
""")

notion_list_blocks_with_cursor = compiler.parse(r"""
curl -H "Authorization: Bearer $NOTION_TOKEN" \
     -H "Notion-Version: 2022-06-28" \
     "https://api.notion.com/v1/blocks/$NOTION_PAGE_ID/children?page_size=100&start_cursor={{start_cursor}}"
""")

notion_delete_block = compiler.parse(r"""
curl -X DELETE -H "Authorization: Bearer $NOTION_TOKEN" \
     -H "Notion-Version: 2022-06-28" \
     "https://api.notion.com/v1/blocks/{{block_id}}"
""")

notion_append_blocks = compiler.parse(r"""
curl -X PATCH "https://api.notion.com/v1/blocks/$NOTION_PAGE_ID/children" \
  -H "Authorization: Bearer $NOTION_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Notion-Version: 2022-06-28" \
  --data '{{payload}}'
""")

MODEL_ID = "gpt-4o"
NOTION_ENABLED = os.getenv("NOTION_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
NOTION_PAGE_ID = os.getenv("NOTION_PAGE_ID", "").strip()
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()

viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=random.randint(7777,8887))

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()

OWNER_ID: Optional[Any] = None
FOLLOW_UP_QUEUE: list[dict] = []
follow_up_lock = asyncio.Lock()
NEGOTIATION_QUEUE: list[dict] = []
negotiation_lock = asyncio.Lock()
PLAN_REPLY_QUEUE: list[dict] = []
plan_reply_lock = asyncio.Lock()
request_arbitration_lock = asyncio.Lock()
LAST_REQUEST_TS: Optional[float] = None
LAST_REQUEST_ID: Optional[str] = None
WAITING_FOR_RESPONSE: bool = False
ACTIVE_CHAIN_NONCE: Optional[str] = None
EXPECTED_CHAIN_NONCE: Optional[str] = None

# Report dashboard state
_report_lock = threading.Lock()
_report_snapshot = {
    "agent_id": AGENT_ID,
    "updated_at": "",
    "report": "",
}

if OWNER_ID_PATH.exists():
    try:
        with OWNER_ID_PATH.open("r", encoding="utf-8") as f:
            OWNER_ID = json.load(f).get("owner_id")
    except Exception:
        OWNER_ID = None


def _message_text(msg: Any) -> str:
    if isinstance(msg, dict):
        content = {k: v for k, v in msg.items() if k not in ("from", "to")}
        if isinstance(content, dict):
            if len(content) == 1:
                v = next(iter(content.values()))
                if isinstance(v, str):
                    return v
                try:
                    return json.dumps(v, sort_keys=True)
                except Exception:
                    return str(v)
            try:
                return json.dumps(content, sort_keys=True)
            except Exception:
                return str(content)
        return str(content)
    return str(msg)


def _normalize_value(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if v == {} or v == []:
        return ""
    try:
        return json.dumps(v, indent=2, ensure_ascii=False)
    except Exception:
        return str(v)


def _append_md(path: Path, text: str) -> None:
    if not text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        path.write_text(path.read_text(encoding="utf-8") + "\n\n" + text + "\n", encoding="utf-8")
    else:
        path.write_text(text + "\n", encoding="utf-8")


def _write_md(path: Path, text: str) -> None:
    if not text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _save_owner_id(owner: Any) -> None:
    OWNER_ID_PATH.write_text(json.dumps({"owner_id": owner}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_workspace() -> None:
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    DAYBYDAY_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    for name in ["SOUL.md", "USER.md", "MEMORY.md", "GOALS.md", "SELF.md", "REPORT.md"]:
        path = WORKSPACE_DIR / name
        if not path.exists():
            path.write_text("", encoding="utf-8")


def _refresh_report_snapshot() -> None:
    report = _read_text(WORKSPACE_DIR / "REPORT.md")
    with _report_lock:
        _report_snapshot["agent_id"] = AGENT_ID
        _report_snapshot["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        _report_snapshot["report"] = report


def _start_report_refresher(interval_s: float = 2.0) -> None:
    def _worker():
        while True:
            try:
                _refresh_report_snapshot()
            except Exception:
                pass
            time.sleep(interval_s)
    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def start_report_dashboard(
    port_range: tuple[int, int] = (4567, 5567),
    tries: int = 40
) -> tuple[ThreadingHTTPServer, int]:
    import socket

    html = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Digital Twin Report</title>
  <style>
    :root {
      --bg: #f5f3ef;
      --card: #ffffff;
      --ink: #1e1e1e;
      --muted: #666;
      --accent: #2b5d8a;
      --border: #e4e1da;
    }
    body {
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Palatino, serif;
      background: linear-gradient(180deg, #f5f3ef 0%, #efece6 100%);
      color: var(--ink);
      margin: 0;
      padding: 24px;
    }
    .frame {
      max-width: 980px;
      margin: 0 auto;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.06);
      padding: 22px 26px 28px;
    }
    .title {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      border-bottom: 1px solid var(--border);
      padding-bottom: 10px;
      margin-bottom: 16px;
    }
    h1 {
      font-size: 22px;
      letter-spacing: 0.3px;
      margin: 0;
      color: var(--accent);
    }
    .meta {
      font-size: 12px;
      color: var(--muted);
      text-align: right;
    }
    .report {
      white-space: pre-wrap;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Palatino, serif;
      line-height: 1.5;
      font-size: 15px;
      background: #faf9f6;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px;
    }
    .footer {
      margin-top: 12px;
      font-size: 11px;
      color: var(--muted);
    }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }
  </style>
</head>
<body>
  <div class="frame">
    <div class="title">
      <h1>Digital Twin Report</h1>
      <div class="meta">
        <div>Agent: <span id="agent_id" class="mono"></span></div>
        <div>Updated: <span id="updated_at"></span></div>
      </div>
    </div>
    <div id="report" class="report"></div>
    <div class="footer">Auto-refresh every 2s</div>
  </div>

<script>
async function refresh() {
  const r = await fetch("/state");
  const s = await r.json();
  document.getElementById("agent_id").textContent = s.agent_id || "";
  document.getElementById("updated_at").textContent = s.updated_at || "";
  document.getElementById("report").textContent = s.report || "";
}
setInterval(refresh, 2000);
refresh();
</script>
</body>
</html>
"""

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
                with _report_lock:
                    payload = json.dumps(_report_snapshot).encode("utf-8")
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

    raise RuntimeError(f"Failed to start report dashboard in range [{lo},{hi}]") from last_err


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _memory_json_text(blob: dict) -> str:
    if not isinstance(blob, dict):
        return ""
    summary = blob.get("summary", "")
    entries = blob.get("entries", [])
    parts: list[str] = []
    if isinstance(summary, str) and summary.strip():
        parts.append("Summary:")
        parts.append(summary.strip())
    if isinstance(entries, list) and entries:
        parts.append("Entries:")
        for item in entries:
            if isinstance(item, str) and item.strip():
                parts.append(f"- {item.strip()}")
    return "\n".join(parts).strip()


def _new_nonce() -> str:
    return f"{int(time.time() * 1000)}-{random.randint(100000, 999999)}"


async def _append_or_compact_json(path: Path, new_text: str, token_limit: int) -> None:
    if not new_text:
        return
    blob = _read_json(path)
    entries = blob.get("entries")
    if not isinstance(entries, list):
        entries = []
    entries.append(new_text)
    blob["entries"] = entries
    combined = _memory_json_text(blob)
    if _count_tokens(combined) <= token_limit:
        _write_json(path, blob)
        return
    compacted = await _compact_markdown(path, combined, "")
    if compacted:
        blob = {"summary": compacted, "entries": []}
        _write_json(path, blob)


def _extract_text_from_openai_response(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    if "error" in payload:
        err = payload.get("error")
        if err:
            return json.dumps(err, ensure_ascii=False)
    if "output" in payload:
        try:
            content = payload["output"][0]["content"][0]
            if isinstance(content, dict):
                if "text" in content and isinstance(content["text"], str):
                    return content["text"]
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    return content["text"]
            return ""
        except Exception:
            return ""
    if "output_text" in payload:
        if isinstance(payload["output_text"], str):
            return payload["output_text"]
    if "choices" in payload:
        try:
            return payload["choices"][0]["message"]["content"]
        except Exception:
            return ""
    return ""


def _count_tokens(text: str) -> int:
    if not text:
        return 0
    return count_chat_tokens([{"role": "user", "content": text}], model=MODEL_ID)


async def _summarize_goals_text(goals_text: str) -> str:
    if not goals_text:
        return ""
    if _count_tokens(goals_text) <= 200:
        return goals_text
    system = "Summarize the goals into 1-3 concise sentences. Preserve key objectives."
    user = goals_text
    try:
        s = await openai_text_client.call({
            "model_id": MODEL_ID,
            "system": system,
            "user": user,
        })
        text = _extract_text_from_openai_response(s.response_json)
        return text.strip()
    except Exception as e:
        client.logger.warning(f"[llm:openai] goals summary error: {e}")
        return goals_text


async def _assess_goal_alignment(their_goals: str, our_goals: str) -> tuple[bool, str]:
    if not (their_goals or "").strip() or not (our_goals or "").strip():
        return True, ""
    system = (
        "You are assessing whether two agents' goals are compatible for collaboration. "
        "Return a JSON object with keys: align (true/false), rationale (string)."
    )
    user = (
        "Our goals:\n"
        f"{our_goals}\n\n"
        "Their goals:\n"
        f"{their_goals}\n"
    )
    try:
        s = await asyncio.wait_for(
            openai_client.call({
                "model_id": MODEL_ID,
                "system": system,
                "user": user,
            }),
            timeout=6.0,
        )
        text = _extract_text_from_openai_response(s.response_json)
        data = json.loads(text)
        align = bool(data.get("align"))
        rationale = str(data.get("rationale", "")).strip()
        if align:
            return True, rationale
        conflict_words = ("conflict", "incompatible", "opposed", "mutually exclusive", "clash")
        if any(w in rationale.lower() for w in conflict_words):
            return False, rationale
        return True, rationale
    except Exception as e:
        client.logger.warning(f"[llm:openai] alignment error: {e}")
        return True, ""


async def _assess_response_interest(response_text: str) -> bool:
    system = (
        "Determine if the response indicates willingness to discuss or collaborate. "
        "Return a JSON object with key: willing (true/false)."
    )
    try:
        s = await openai_client.call({
            "model_id": MODEL_ID,
            "system": system,
            "user": response_text,
        })
        text = _extract_text_from_openai_response(s.response_json)
        data = json.loads(text)
        return bool(data.get("willing", True))
    except Exception as e:
        client.logger.warning(f"[llm:openai] response interest error: {e}")
        return True


async def _plan_discussion(msg: Any) -> dict:
    today = datetime.now(timezone.utc).date()
    yesterday = today.fromordinal(today.toordinal() - 1)
    today_path = MEMORY_DIR / f"{today.isoformat()}.json"
    yesterday_path = MEMORY_DIR / f"{yesterday.isoformat()}.json"

    report = _read_text(WORKSPACE_DIR / "REPORT.md")
    lt_memory = _read_text(WORKSPACE_DIR / "MEMORY.md")
    goals = _read_text(WORKSPACE_DIR / "GOALS.md")
    self_text = _read_text(WORKSPACE_DIR / "SELF.md")
    user_text = _read_text(WORKSPACE_DIR / "USER.md")
    soul_text = _read_text(WORKSPACE_DIR / "SOUL.md")
    today_mem = _memory_json_text(_read_json(today_path))
    yday_mem = _memory_json_text(_read_json(yesterday_path))

    system = (
        "You are a digital twin collaborating with another agent to do real, topic-agnostic work. "
        "Always produce 2–3 distinct options. For each option, include a short risk or tradeoff. "
        "Then compare options explicitly using general criteria (impact, feasibility, speed, cost) "
        "and converge on a single best option. End with a concrete next step or question that moves "
        "the work forward. Avoid domain-specific assumptions; stay general and adaptable. "
        "Return a JSON object with keys: reply, report, lt_memory, st_memory, decision. "
        "report = delta insights for REPORT.md. lt_memory = durable facts for MEMORY.md. "
        "st_memory = short-term notes for today. decision = current best strategy in 1-3 sentences."
    )
    user = (
        "Context:\n"
        f"GOALS:\n{goals}\n\n"
        f"SELF:\n{self_text}\n\n"
        f"USER:\n{user_text}\n\n"
        f"SOUL:\n{soul_text}\n\n"
        f"REPORT:\n{report}\n\n"
        f"MEMORY:\n{lt_memory}\n\n"
        f"MEMORY_TODAY:\n{today_mem}\n\n"
        f"MEMORY_YESTERDAY:\n{yday_mem}\n\n"
        "Incoming message:\n"
        f"{_message_text(msg)}"
    )
    try:
        s = await openai_client.call({
            "model_id": MODEL_ID,
            "system": system,
            "user": user,
        })
        text = _extract_text_from_openai_response(s.response_json)
        data = json.loads(text)
        return {
            "reply": str(data.get("reply", "")).strip(),
            "report": str(data.get("report", "")).strip(),
            "lt_memory": str(data.get("lt_memory", "")).strip(),
            "st_memory": str(data.get("st_memory", "")).strip(),
            "decision": str(data.get("decision", "")).strip(),
        }
    except Exception as e:
        client.logger.warning(f"[llm:openai] plan discussion error: {e}")
        return {"reply": "", "report": "", "lt_memory": "", "st_memory": "", "decision": ""}


def _plan_fallback_prompt() -> str:
    return (
        "I’m ready to brainstorm strategies. "
        "What direction should we start with: positioning, channels, or specific tactics?"
    )

def _notion_blocks_from_text(text: str, max_chunk: int = 1800) -> list[dict]:
    def _rich_text_from_md(s: str) -> list[dict]:
        if not s:
            return []
        out: list[dict] = []
        i = 0
        bold = False
        while i < len(s):
            if s.startswith("**", i):
                bold = not bold
                i += 2
                continue
            j = s.find("**", i)
            if j == -1:
                chunk = s[i:]
                if chunk:
                    out.append({
                        "type": "text",
                        "text": {"content": chunk},
                        "annotations": {"bold": bold},
                    })
                break
            chunk = s[i:j]
            if chunk:
                out.append({
                    "type": "text",
                    "text": {"content": chunk},
                    "annotations": {"bold": bold},
                })
            i = j
        return out

    def _split_chunks(s: str) -> list[str]:
        return [s[i:i + max_chunk] for i in range(0, len(s), max_chunk)] if s else []

    def _mk_blocks(block_type: str, content: str, language: Optional[str] = None) -> list[dict]:
        blocks: list[dict] = []
        for chunk in _split_chunks(content):
            if block_type == "code":
                payload = {"rich_text": [{"type": "text", "text": {"content": chunk}}]}
                payload["language"] = language or "plain text"
            else:
                payload = {"rich_text": _rich_text_from_md(chunk)}
            blocks.append({
                "object": "block",
                "type": block_type,
                block_type: payload,
            })
        return blocks

    blocks: list[dict] = []
    para_buf: list[str] = []
    in_code = False
    code_lang = ""
    code_buf: list[str] = []

    def _flush_para() -> None:
        nonlocal para_buf
        if para_buf:
            blocks.extend(_mk_blocks("paragraph", "\n".join(para_buf)))
            para_buf = []

    def _flush_code() -> None:
        nonlocal code_buf, code_lang
        if code_buf:
            blocks.extend(_mk_blocks("code", "\n".join(code_buf), language=code_lang))
            code_buf = []
            code_lang = ""

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped == "":
            if in_code:
                code_buf.append("")
            else:
                _flush_para()
            continue

        if stripped.startswith("```"):
            if in_code:
                _flush_code()
                in_code = False
            else:
                _flush_para()
                in_code = True
                code_lang = stripped[3:].strip()
            continue

        if in_code:
            code_buf.append(line)
            continue

        if stripped.startswith("#"):
            _flush_para()
            level = len(stripped.split(" ", 1)[0])
            content = stripped[level:].strip()
            if content:
                if level == 1:
                    blocks.extend(_mk_blocks("heading_1", content))
                elif level == 2:
                    blocks.extend(_mk_blocks("heading_2", content))
                else:
                    blocks.extend(_mk_blocks("heading_3", content))
            continue

        if stripped.startswith("> "):
            _flush_para()
            blocks.extend(_mk_blocks("quote", stripped[2:].strip()))
            continue

        if stripped.startswith(("- ", "* ")):
            _flush_para()
            blocks.extend(_mk_blocks("bulleted_list_item", stripped[2:].strip()))
            continue

        num_dot = stripped.split(" ", 1)[0]
        if num_dot.endswith(".") and num_dot[:-1].isdigit():
            content = stripped[len(num_dot):].strip()
            if content:
                _flush_para()
                blocks.extend(_mk_blocks("numbered_list_item", content))
                continue

        para_buf.append(line)

    if in_code:
        _flush_code()
    _flush_para()
    return blocks


async def _sync_report_to_notion(report_text: str) -> None:
    if not NOTION_ENABLED:
        return
    if not NOTION_TOKEN or not NOTION_PAGE_ID:
        client.logger.warning("[notion] enabled but NOTION_TOKEN/NOTION_PAGE_ID missing")
        return

    blocks = _notion_blocks_from_text(report_text)
    if not blocks:
        client.logger.debug("[notion] skip empty report")
        return

    block_ids: list[str] = []
    try:
        resp = await notion_list_blocks.call()
        data = resp.response_json or {}
        block_ids.extend([b.get("id") for b in data.get("results", []) if isinstance(b, dict) and b.get("id")])
        while data.get("has_more") and data.get("next_cursor"):
            cursor = data.get("next_cursor")
            resp = await notion_list_blocks_with_cursor.call({"start_cursor": cursor})
            data = resp.response_json or {}
            block_ids.extend([b.get("id") for b in data.get("results", []) if isinstance(b, dict) and b.get("id")])
    except Exception as e:
        client.logger.warning(f"[notion] list blocks failed: {e}")
        return

    sem = asyncio.Semaphore(3)

    async def _delete_block(block_id: str) -> None:
        async with sem:
            try:
                resp = await notion_delete_block.call({"block_id": block_id})
                if resp.status_code == 429:
                    retry_after = 1
                    try:
                        retry_after = int(resp.response_json.get("retry_after", 1)) if isinstance(resp.response_json, dict) else 1
                    except Exception:
                        retry_after = 1
                    await asyncio.sleep(max(1, retry_after))
                    await notion_delete_block.call({"block_id": block_id})
            except Exception as e:
                client.logger.warning(f"[notion] delete block failed id={block_id}: {e}")

    await asyncio.gather(*[_delete_block(bid) for bid in block_ids])

    payload = json.dumps({"children": blocks}, ensure_ascii=False)
    try:
        resp = await notion_append_blocks.call({"payload": payload})
        if not resp.ok:
            client.logger.warning(f"[notion] append failed status={resp.status_code}")
    except Exception as e:
        client.logger.warning(f"[notion] append blocks failed: {e}")


async def _update_plan_memory(report: str, lt_memory: str, st_memory: str) -> None:
    if report:
        await _append_or_compact(WORKSPACE_DIR / "REPORT.md", report, token_limit=600)
        _refresh_report_snapshot()
        _refresh_report_snapshot()
    if lt_memory:
        await _append_or_compact(WORKSPACE_DIR / "MEMORY.md", lt_memory, token_limit=1200)
    if st_memory:
        date_str = datetime.now(timezone.utc).date().isoformat()
        await _append_or_compact_json(MEMORY_DIR / f"{date_str}.json", st_memory, token_limit=600)


async def _compact_markdown(path: Path, existing: str, incoming: str) -> str:
    system = (
        "You are a memory compaction module. "
        "Rewrite the content as a polished Markdown document with clear sections. "
        "Remove duplicates, keep factual accuracy, and preserve all important details. "
        "Be concise. Output Markdown only. "
        "Do not use fenced code blocks (```) and do not include the word 'markdown'."
    )
    user = (
        f"Target file: {path.name}\n\n"
        "Existing content:\n"
        "-----\n"
        f"{existing}\n"
        "-----\n\n"
        "New content to integrate:\n"
        "-----\n"
        f"{incoming}\n"
        "-----\n\n"
        "Return a compacted Markdown document."
    )
    try:
        s = await openai_text_client.call({
            "model_id": MODEL_ID,
            "system": system,
            "user": user,
        })
        text = _extract_text_from_openai_response(s.response_json)
        return text.strip()
    except Exception as e:
        client.logger.warning(f"[llm:openai] compaction error: {e}")
        return ""


async def _append_or_compact(path: Path, new_text: str, token_limit: int) -> None:
    if not new_text:
        return
    existing = _read_text(path)
    combined = (existing + "\n\n" + new_text).strip() if existing else new_text.strip()
    if _count_tokens(combined) <= token_limit:
        _append_md(path, new_text)
        return

    compacted = await _compact_markdown(path, existing, new_text)
    if compacted:
        _write_md(path, compacted)
        if path.name == "REPORT.md":
            await _sync_report_to_notion(compacted)
    else:
        _append_md(path, new_text)


async def extract_memory(msg: dict) -> dict:
    today = datetime.now(timezone.utc).date()
    yesterday = today.fromordinal(today.toordinal() - 1)

    soul_ctx = _read_text(WORKSPACE_DIR / "SOUL.md")
    user_ctx = _read_text(WORKSPACE_DIR / "USER.md")
    lt_ctx = _read_text(WORKSPACE_DIR / "MEMORY.md")
    st_today = _read_text(DAYBYDAY_DIR / f"{today.isoformat()}.md")
    st_yesterday = _read_text(DAYBYDAY_DIR / f"{yesterday.isoformat()}.md")
    goals_ctx = _read_text(WORKSPACE_DIR / "GOALS.md")
    self_ctx = _read_text(WORKSPACE_DIR / "SELF.md")

    system = (
        "You are a memory extraction module for a digital twin. "
        "IMPORTANT: The assistant IS the user's digital twin. The 'self' field describes what the digital twin should mirror. "
        "The 'user' field describes the human user. "
        "Given the user's message, extract only information that should be saved. "
        "Return a JSON object with keys: goals, heartbeat, lt_memory, st_memory, report, user, self, soul, follow_up. "
        "Use empty strings for unknown fields. Keep values concise but complete. "
        "Use full sentences with clear subjects (e.g., \"The user's name is Remy.\"). "
        "If a field refers to the user, state it explicitly. "
        "If the message is the user's self-description, treat it as reliable. "
        "Only include NEW information that is not already present in the provided context. "
        "Do not repeat known facts unless they are updated or contradicted. "
        "Prefer specific details from the latest message over generic paraphrases. "
        "follow_up should be a single concise question or confirmation that explicitly references "
        "a specific detail from the latest message (not a generic script), and reflects the SOUL/USER/MEMORY context."
    )

    user = (
        "Context files (Markdown, may contain headings and lists; treat them as structured notes):\n"
        f"## SOUL.md\n{soul_ctx}\n\n"
        f"## USER.md\n{user_ctx}\n\n"
        f"## MEMORY.md\n{lt_ctx}\n\n"
        f"## daybyday/{today.isoformat()}.md\n{st_today}\n\n"
        f"## daybyday/{yesterday.isoformat()}.md\n{st_yesterday}\n\n"
        f"## GOALS.md\n{goals_ctx}\n\n"
        f"## SELF.md\n{self_ctx}\n\n"
        "New message:\n"
        f"{_message_text(msg)}\n\n"
        f"Owner (if known): {OWNER_ID}\n\n"
        "Output field guide:\n"
        "- goals: Assistant goals for helping the user; concrete objectives implied by the latest message. "
        "Do not repeat goals already present in GOALS.md.\n"
        "- heartbeat: Number of seconds for future sleeps if a cadence is explicitly requested; otherwise empty. "
        "If present, include a brief rationale.\n"
        "- lt_memory: Durable, long-term facts about the user, project, or environment. "
        "Only include NEW facts not already in MEMORY.md; avoid transient details.\n"
        "- st_memory: New, short-term details for today that were said or clarified in the latest message. "
        "Do not restate stable facts from MEMORY.md.\n"
        "- report: What changed or was clarified in this message, focusing on the delta from prior context. "
        "Avoid generic summaries.\n"
        "- user: NEW facts about the human user (identity, preferences, roles). "
        "Only add if the latest message reveals something new.\n"
        "- self: Instructions for how YOU, the digital twin, should mirror the user. "
        "Write as behavioral and voice guidance for the digital twin; only include NEW adjustments.\n"
        "- soul: NEW interaction style, tone, and sensitivities. "
        "Only include changes or refinements; avoid vague platitudes.\n"
        "- follow_up: This will be sent directly to the user as the assistant's response. "
        "If the user asked a direct or implicit question, answer it with concrete, helpful guidance first. "
        "Then optionally add one clarifying question to advance the discussion. "
        "If no question was asked, provide a brief helpful suggestion and a specific, detail-grounded question. "
        "Avoid generic questions or one-line scripts.\n"
    )

    try:
        s = await openai_client.call({
            "model_id": MODEL_ID,
            "system": system,
            "user": user,
        })
        text = _extract_text_from_openai_response(s.response_json)
        if not text:
            client.logger.warning(f"[llm:openai] empty response payload: {s.response_json}")
            raise ValueError("empty openai response text")
        return json.loads(text)
    except Exception as e:
        client.logger.warning(f"[llm:openai] extraction error: {e}")
        try:
            client.logger.debug(f"[llm:openai] raw response: {s.response_json}")
        except Exception:
            pass
        return {
            "goals": "",
            "heartbeat": "",
            "lt_memory": "",
            "st_memory": "",
            "report": "",
            "user": "",
            "self": "",
            "soul": "",
            "follow_up": "",
        }


@client.hook(direction=Direction.RECEIVE, priority=0)
async def validate(msg: Any) -> Optional[dict]:
    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg):
        return
    content: Any = msg["content"]
    if not isinstance(content, dict):
        return

    to = content.get("to")
    if to not in (None, AGENT_ID_OBJ):
        return

    if "from" not in content:
        client.logger.warning("[hook:recv] missing content.from")
        return

    return content


states: list[str] = ["learn"]


@client.upload_states()
async def upload_states(m: Any) -> list[str]:
    viz.push_states(states)
    return states


@client.download_states()
async def download_states(possible_states: list[Node]) -> None:
    global states
    incoming = [str(s) for s in (possible_states or [])]
    new_states = [s for s in incoming if s not in states]
    if not new_states:
        viz.push_states(states)
        client.logger.debug(f"[states] incoming={incoming}")
        client.logger.debug(f"[states] updated={states} hello_in={ 'hello' in states }")
        return states
    states = new_states
    viz.push_states(states)
    client.logger.debug(f"[states] incoming={incoming}")
    client.logger.debug(f"[states] updated={states} hello_in={ 'hello' in states }")
    return states


@client.receive(route="learn --> hello")
async def on_learn_to_hello(msg: Any) -> Event:
    global OWNER_ID
    if isinstance(msg, dict) and msg.get("to") != AGENT_ID_OBJ:
        return Stay(Trigger.ok)
    if OWNER_ID is None:
        OWNER_ID = msg.get("from")
        if OWNER_ID is not None:
            _save_owner_id(OWNER_ID)

    msg_text = _message_text(msg)
    if "/goplan" in msg_text:
        client.logger.info("[travel] start to 187.77.102.80:8888")
        t0 = asyncio.get_event_loop().time()
        await client.travel_to(host="187.77.102.80", port=8888)
        client.logger.info(f"[travel] done in {asyncio.get_event_loop().time()-t0:.2f}s")
        return Move(Trigger.ok)

    client.logger.debug(msg)

    result = await extract_memory(msg)
    # print("[digital_twin] extraction result:")
    # print(json.dumps(result, indent=2, ensure_ascii=False))

    goals = _normalize_value(result.get("goals"))
    heartbeat = _normalize_value(result.get("heartbeat"))
    lt_memory = _normalize_value(result.get("lt_memory"))
    st_memory = _normalize_value(result.get("st_memory"))
    report = _normalize_value(result.get("report"))
    user = _normalize_value(result.get("user"))
    self_ = _normalize_value(result.get("self"))
    soul = _normalize_value(result.get("soul"))
    follow_up = _normalize_value(result.get("follow_up"))

    if goals:
        await _append_or_compact(WORKSPACE_DIR / "GOALS.md", goals, token_limit=400)
    if lt_memory:
        await _append_or_compact(WORKSPACE_DIR / "MEMORY.md", lt_memory, token_limit=1200)
    if user:
        await _append_or_compact(WORKSPACE_DIR / "USER.md", user, token_limit=400)
    if self_:
        await _append_or_compact(WORKSPACE_DIR / "SELF.md", self_, token_limit=400)
    if soul:
        await _append_or_compact(WORKSPACE_DIR / "SOUL.md", soul, token_limit=400)
    if report:
        await _append_or_compact(WORKSPACE_DIR / "REPORT.md", report, token_limit=600)
    if st_memory:
        date_str = datetime.now(timezone.utc).date().isoformat()
        await _append_or_compact(DAYBYDAY_DIR / f"{date_str}.md", st_memory, token_limit=600)

    if heartbeat:
        client.logger.debug(f"[heartbeat] {heartbeat}")

    if follow_up:
        async with follow_up_lock:
            FOLLOW_UP_QUEUE.append({"payload": follow_up, "to": OWNER_ID})
        date_str = datetime.now(timezone.utc).date().isoformat()
        await _append_or_compact(DAYBYDAY_DIR / f"{date_str}.md", f"Follow-up: {follow_up}", token_limit=600)

    return Stay(Trigger.ok)


@client.receive(route="hello --> plan")
async def on_hello_to_plan(msg: Any) -> Event:
    global LAST_REQUEST_TS, LAST_REQUEST_ID, WAITING_FOR_RESPONSE, ACTIVE_CHAIN_NONCE, EXPECTED_CHAIN_NONCE
    client.logger.debug("[recv hello->plan] handler invoked")

    if not isinstance(msg, dict):
        return Stay(Trigger.ok)

    sender = msg.get("from")
    if not (isinstance(sender, dict) and sender.get("type") == "digital_twin"):
        return Stay(Trigger.ok)

    intent = msg.get("intent")
    if intent == "request":
        async with request_arbitration_lock:
            their_ts = msg.get("req_ts")
            their_id = msg.get("req_id")
            their_next_nonce = msg.get("next_nonce")
            try:
                their_ts_val = float(their_ts) if their_ts is not None else None
            except Exception:
                their_ts_val = None
            client.logger.info(f"[recv hello:req] from={sender} req_id={their_id} ts={their_ts_val} ours={LAST_REQUEST_TS} next_nonce={their_next_nonce}")

            # If we already locked onto a chain, ignore other requests.
            if (ACTIVE_CHAIN_NONCE or EXPECTED_CHAIN_NONCE) and their_next_nonce not in (ACTIVE_CHAIN_NONCE, EXPECTED_CHAIN_NONCE):
                client.logger.info(f"[nonce] ignore request next={their_next_nonce} active={ACTIVE_CHAIN_NONCE} expected={EXPECTED_CHAIN_NONCE}")
                return Stay(Trigger.ok)

            # If their request is after ours, we yield: move to plan and wait for response.
            if LAST_REQUEST_TS is not None and their_ts_val is not None:
                if their_ts_val > LAST_REQUEST_TS:
                    WAITING_FOR_RESPONSE = True
                    EXPECTED_CHAIN_NONCE = str(their_next_nonce) if their_next_nonce else None
                    client.logger.info(f"[arb] yield (their_ts > our_ts) their={their_ts_val} ours={LAST_REQUEST_TS}")
                    return Move(Trigger.ok)
                if their_ts_val == LAST_REQUEST_TS and their_id and LAST_REQUEST_ID and their_id > LAST_REQUEST_ID:
                    WAITING_FOR_RESPONSE = True
                    EXPECTED_CHAIN_NONCE = str(their_next_nonce) if their_next_nonce else None
                    client.logger.info(f"[arb] yield (tie-break) their_id>{LAST_REQUEST_ID}")
                    return Move(Trigger.ok)

            their_goals = msg.get("goals") or _message_text(msg)
            our_goals = _read_text(WORKSPACE_DIR / "GOALS.md")
            align, rationale = await _assess_goal_alignment(str(their_goals), our_goals)
            client.logger.info(f"[arb] alignment={align} rationale_len={len(rationale)}")
            if align:
                WAITING_FOR_RESPONSE = True
                ACTIVE_CHAIN_NONCE = str(their_next_nonce) if their_next_nonce else None
                plan = await _plan_discussion(msg)
                await _update_plan_memory(plan.get("report", ""), plan.get("lt_memory", ""), plan.get("st_memory", ""))
                decision = plan.get("decision", "")
                reply = plan.get("reply", "")
                client.logger.debug(f"[plan] decision_len={len(decision)} reply_len={len(reply)}")
                msg_text = "I think our goals align and I'd like to discuss."
                if rationale:
                    msg_text = f"{msg_text} {rationale}"
                if decision:
                    msg_text = f"{msg_text} Initial strategy: {decision}"
                if reply:
                    msg_text = f"{msg_text}\n\n{reply}"
                current_nonce = ACTIVE_CHAIN_NONCE or _new_nonce()
                next_nonce = _new_nonce()
                ACTIVE_CHAIN_NONCE = next_nonce
                async with negotiation_lock:
                    NEGOTIATION_QUEUE.append({
                        "to": sender,
                        "assessment": rationale,
                        "their_goals": str(their_goals),
                        "message": msg_text,
                        "current_nonce": current_nonce,
                        "next_nonce": next_nonce,
                    })
                return Move(Trigger.ok)
            return Stay(Trigger.ok)

    if intent == "response":
        to = msg.get("to")
        if to not in (AGENT_ID_OBJ, AGENT_ID, AGENT_ID_OBJ.get("name")):
            return Stay(Trigger.ok)
        current_nonce = msg.get("current_nonce")
        next_nonce = msg.get("next_nonce")
        client.logger.info(f"[recv hello:resp] from={sender} to={to} waiting={WAITING_FOR_RESPONSE} cur={current_nonce} next={next_nonce}")
        if EXPECTED_CHAIN_NONCE and current_nonce != EXPECTED_CHAIN_NONCE:
            client.logger.info(f"[nonce] ignore response cur={current_nonce} expected={EXPECTED_CHAIN_NONCE}")
            return Stay(Trigger.ok)
        ACTIVE_CHAIN_NONCE = str(next_nonce) if next_nonce else None
        EXPECTED_CHAIN_NONCE = None
        WAITING_FOR_RESPONSE = False
        willing = await _assess_response_interest(_message_text(msg))
        if willing:
            plan = await _plan_discussion(msg)
            await _update_plan_memory(plan.get("report", ""), plan.get("lt_memory", ""), plan.get("st_memory", ""))
            reply = plan.get("reply", "")
            decision = plan.get("decision", "")
            if decision:
                reply = f"{reply}\n\nCurrent best strategy: {decision}".strip()
            if not reply:
                reply = _plan_fallback_prompt()
            current_nonce = ACTIVE_CHAIN_NONCE or _new_nonce()
            next_nonce = _new_nonce()
            ACTIVE_CHAIN_NONCE = next_nonce
            async with plan_reply_lock:
                PLAN_REPLY_QUEUE.append({
                    "intent": "plan",
                    "to": sender,
                    "message": reply,
                    "current_nonce": current_nonce,
                    "next_nonce": next_nonce,
                })
            client.logger.debug(f"[plan] queued response plan reply_len={len(reply)}")
        return Move(Trigger.ok)

    if intent == "plan":
        if not isinstance(sender, dict) or sender.get("type") != "digital_twin":
            return Stay(Trigger.ok)
        current_nonce = msg.get("current_nonce")
        next_nonce = msg.get("next_nonce")
        client.logger.info(f"[recv hello:plan] from={sender} cur={current_nonce} next={next_nonce} active={ACTIVE_CHAIN_NONCE}")
        if ACTIVE_CHAIN_NONCE and current_nonce and current_nonce != ACTIVE_CHAIN_NONCE:
            client.logger.info(f"[nonce] ignore plan cur={current_nonce} active={ACTIVE_CHAIN_NONCE}")
            return Stay(Trigger.ok)
        if next_nonce:
            ACTIVE_CHAIN_NONCE = str(next_nonce)
        plan = await _plan_discussion(msg)
        await _update_plan_memory(plan.get("report", ""), plan.get("lt_memory", ""), plan.get("st_memory", ""))
        reply = plan.get("reply", "")
        decision = plan.get("decision", "")
        if decision:
            reply = f"{reply}\n\nCurrent best strategy: {decision}".strip()
        if not reply:
            reply = _plan_fallback_prompt()
        if reply:
            current_nonce = ACTIVE_CHAIN_NONCE or _new_nonce()
            next_nonce = _new_nonce()
            ACTIVE_CHAIN_NONCE = next_nonce
            async with plan_reply_lock:
                PLAN_REPLY_QUEUE.append({
                    "intent": "plan",
                    "to": sender,
                    "message": reply,
                    "current_nonce": current_nonce,
                    "next_nonce": next_nonce,
                })
        client.logger.debug(f"[plan] queued reply_len={len(reply)} queue_size={len(PLAN_REPLY_QUEUE)}")
        return Move(Trigger.ok)

    return Stay(Trigger.ok)


@client.receive(route="plan")
async def on_plan(msg: Any) -> Event:
    if not isinstance(msg, dict):
        return Stay(Trigger.ok)
    global ACTIVE_CHAIN_NONCE
    intent = msg.get("intent")
    if intent not in ("plan", "response"):
        client.logger.debug(f"[plan] ignore intent={intent}")
        return Stay(Trigger.ok)
    current_nonce = msg.get("current_nonce")
    next_nonce = msg.get("next_nonce")
    client.logger.info(f"[recv plan] from={msg.get('from')} text_len={len(_message_text(msg))} cur={current_nonce} next={next_nonce} active={ACTIVE_CHAIN_NONCE}")
    if ACTIVE_CHAIN_NONCE and current_nonce and current_nonce != ACTIVE_CHAIN_NONCE:
        client.logger.info(f"[nonce] ignore plan cur={current_nonce} active={ACTIVE_CHAIN_NONCE}")
        return Stay(Trigger.ok)
    if next_nonce:
        ACTIVE_CHAIN_NONCE = str(next_nonce)
    plan = await _plan_discussion(msg)
    await _update_plan_memory(plan.get("report", ""), plan.get("lt_memory", ""), plan.get("st_memory", ""))
    reply = plan.get("reply", "")
    decision = plan.get("decision", "")
    if decision:
        reply = f"{reply}\n\nCurrent best strategy: {decision}".strip()
    if not reply:
        reply = _plan_fallback_prompt()
    if reply:
        current_nonce = ACTIVE_CHAIN_NONCE or _new_nonce()
        next_nonce = _new_nonce()
        ACTIVE_CHAIN_NONCE = next_nonce
        async with plan_reply_lock:
            PLAN_REPLY_QUEUE.append({
                "intent": "plan",
                "to": msg.get("from"),
                "message": reply,
                "current_nonce": current_nonce,
                "next_nonce": next_nonce,
            })
    client.logger.debug(f"[plan] queued reply_len={len(reply)} queue_size={len(PLAN_REPLY_QUEUE)}")
    return Stay(Trigger.ok)

@client.send(route="learn --> hello", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_follow_up() -> Optional[dict]:
    async with follow_up_lock:
        if not FOLLOW_UP_QUEUE:
            return None
        item = FOLLOW_UP_QUEUE.pop(0)
    if not item or not item.get("payload") or not item.get("to"):
        return None
    return item

async def _build_plan_request() -> Optional[dict]:
    global LAST_REQUEST_TS, LAST_REQUEST_ID, ACTIVE_CHAIN_NONCE, EXPECTED_CHAIN_NONCE
    client.logger.debug("[send plan-req] build request")

    now = asyncio.get_event_loop().time()
    goals_path = WORKSPACE_DIR / "GOALS.md"
    goals_text = _read_text(goals_path)
    summary = await _summarize_goals_text(goals_text)
    client.logger.debug(f"[plan req] now={now:.3f}")
    client.logger.debug(f"[plan req] goals_text_len={len(goals_text)}")
    client.logger.debug(f"[plan req] summary_len={len(summary) if summary else 0}")
    if not summary:
        if not goals_text.strip():
            summary = "No goals provided yet."
        else:
            summary = "Goals were provided but summarization returned empty."
    req_ts = time.time()
    req_id = f"{AGENT_ID}-{int(req_ts * 1000)}-{random.randint(1000, 9999)}"
    LAST_REQUEST_TS = req_ts
    LAST_REQUEST_ID = req_id
    current_nonce = _new_nonce()
    next_nonce = _new_nonce()
    # Reset chain on new outbound request
    ACTIVE_CHAIN_NONCE = None
    EXPECTED_CHAIN_NONCE = None
    client.logger.info(f"[req] build req_id={req_id} ts={req_ts:.3f}")
    return {
        "intent": "request",
        "to": None,
        "message": f"Is any agent available to help with these goals? {summary}",
        "goals": summary,
        "req_ts": req_ts,
        "req_id": req_id,
        "current_nonce": current_nonce,
        "next_nonce": next_nonce,
    }


@client.send(route="learn --> hello", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_plan_request_on_hello() -> Optional[dict]:
    return await _build_plan_request()


@client.send(route="hello")
async def send_plan_request_while_idle() -> Optional[dict]:
    client.logger.debug(f"[send idle] states={states} hello_in={'hello' in states}")
    await asyncio.sleep(1.0)
    if "hello" not in states:
        return None
    if WAITING_FOR_RESPONSE or ACTIVE_CHAIN_NONCE or EXPECTED_CHAIN_NONCE:
        return None
    return await _build_plan_request()


@client.send(route="hello --> plan", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_plan_response() -> Optional[dict]:
    async with negotiation_lock:
        if not NEGOTIATION_QUEUE:
            return None
        item = NEGOTIATION_QUEUE.pop(0)
    if not item or not item.get("to"):
        return None
    assessment = item.get("assessment", "")
    msg = item.get("message") or "I think our goals align and I'd like to discuss."
    if assessment and msg == "I think our goals align and I'd like to discuss.":
        msg = f"{msg} {assessment}"
    return {
        "intent": "response",
        "to": item["to"],
        "message": msg,
        "current_nonce": item.get("current_nonce"),
        "next_nonce": item.get("next_nonce"),
    }


@client.send(route="plan", on_actions={Action.STAY, Action.MOVE}, on_triggers={Trigger.ok})
async def send_plan_message() -> Optional[dict]:
    async with plan_reply_lock:
        if not PLAN_REPLY_QUEUE:
            return None
        item = PLAN_REPLY_QUEUE.pop(0)
    if not item or not item.get("message"):
        return None
    client.logger.debug(f"[send plan] to={item.get('to')} len={len(item.get('message',''))} cur={item.get('current_nonce')} next={item.get('next_nonce')}")
    return {
        "intent": "plan",
        "to": item.get("to"),
        "message": item["message"],
        "current_nonce": item.get("current_nonce"),
        "next_nonce": item.get("next_nonce"),
    }


@client.send(route="hello --> plan", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_plan_message_on_transition() -> Optional[dict]:
    async with plan_reply_lock:
        if not PLAN_REPLY_QUEUE:
            return None
        item = PLAN_REPLY_QUEUE.pop(0)
    if not item or not item.get("message"):
        return None
    client.logger.debug(f"[send plan:transition] to={item.get('to')} len={len(item.get('message',''))} cur={item.get('current_nonce')} next={item.get('next_nonce')}")
    return {
        "intent": "plan",
        "to": item.get("to"),
        "message": item["message"],
        "current_nonce": item.get("current_nonce"),
        "next_nonce": item.get("next_nonce"),
    }


@client.hook(direction=Direction.SEND)
async def sign(msg: Any) -> Optional[dict]:
    client.logger.info(f"[hook:send] sign {AGENT_ID}")
    if isinstance(msg, str):
        msg = {"message": msg}
    if not isinstance(msg, dict):
        return
    msg.update({"from": AGENT_ID_OBJ})
    return msg


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    parser.add_argument("--model-id", default=None, help="Model id to use for OpenAI. If omitted, defaults to gpt-4o-mini.")
    args = parser.parse_args()

    if args.model_id:
        MODEL_ID = args.model_id

    _ensure_workspace()
    _refresh_report_snapshot()
    _start_report_refresher()
    report_server, report_port = start_report_dashboard()
    webbrowser.open(f"http://127.0.0.1:{report_port}")

    # Start visual window (browser) and build graph from dna
    viz.attach_logger(client.logger)
    viz.start(open_browser=True)
    viz.set_graph_from_dna(json.loads(client.dna()), parse_route=client_flow.parse_route)
    viz.push_states(["learn"])

    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
