import argparse, json, asyncio, random
from typing import Any, Optional
from pathlib import Path
from datetime import datetime, timezone

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

MODEL_ID = "gpt-4o"

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
_last_plan_request_ts: float = 0.0
PLAN_REQUEST_COOLDOWN_SEC = 30.0
_cached_plan_request: Optional[dict] = None
_cached_goals_mtime: Optional[float] = None

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


def _file_mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except Exception:
        return None


def _save_owner_id(owner: Any) -> None:
    OWNER_ID_PATH.write_text(json.dumps({"owner_id": owner}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_workspace() -> None:
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    DAYBYDAY_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    for name in ["SOUL.md", "USER.md", "MEMORY.md", "GOALS.md", "SELF.md", "REPORT.md", "HEARTBEAT.md"]:
        path = WORKSPACE_DIR / name
        if not path.exists():
            path.write_text("", encoding="utf-8")


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
        client.logger.info(f"[llm:openai] goals summary error: {e}")
        return goals_text


async def _assess_goal_alignment(their_goals: str, our_goals: str) -> tuple[bool, str]:
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
        s = await openai_client.call({
            "model_id": MODEL_ID,
            "system": system,
            "user": user,
        })
        text = _extract_text_from_openai_response(s.response_json)
        data = json.loads(text)
        return bool(data.get("align")), str(data.get("rationale", "")).strip()
    except Exception as e:
        client.logger.info(f"[llm:openai] alignment error: {e}")
        return False, ""


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
        return bool(data.get("willing"))
    except Exception as e:
        client.logger.info(f"[llm:openai] response interest error: {e}")
        return False


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
        "You are a digital twin collaborating with another agent to brainstorm strategies. "
        "Use the context to propose options, converge on the best strategy, and respond clearly. "
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
        client.logger.info(f"[llm:openai] plan discussion error: {e}")
        return {"reply": "", "report": "", "lt_memory": "", "st_memory": "", "decision": ""}


async def _update_plan_memory(report: str, lt_memory: str, st_memory: str) -> None:
    if report:
        await _append_or_compact(WORKSPACE_DIR / "REPORT.md", report, token_limit=600)
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
        "Be concise. Output Markdown only."
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
        client.logger.info(f"[llm:openai] compaction error: {e}")
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
            client.logger.info(f"[llm:openai] empty response payload: {s.response_json}")
            raise ValueError("empty openai response text")
        return json.loads(text)
    except Exception as e:
        client.logger.info(f"[llm:openai] extraction error: {e}")
        try:
            client.logger.info(f"[llm:openai] raw response: {s.response_json}")
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
        client.logger.info("[hook:recv] missing content.from")
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
        client.logger.info(f"[states] incoming={incoming}")
        client.logger.info(f"[states] updated={states} hello_in={ 'hello' in states }")
        return states
    states = new_states
    viz.push_states(states)
    client.logger.info(f"[states] incoming={incoming}")
    client.logger.info(f"[states] updated={states} hello_in={ 'hello' in states }")
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

    client.logger.info(msg)

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
        client.logger.info(f"[heartbeat] {heartbeat}")

    if follow_up:
        async with follow_up_lock:
            FOLLOW_UP_QUEUE.append({"payload": follow_up, "to": OWNER_ID})
        date_str = datetime.now(timezone.utc).date().isoformat()
        await _append_or_compact(DAYBYDAY_DIR / f"{date_str}.md", f"Follow-up: {follow_up}", token_limit=600)

    return Stay(Trigger.ok)


@client.receive(route="hello --> plan")
async def on_hello_to_plan(msg: Any) -> Event:
    client.logger.info("[send on hello] triggered; building plan request")

    if not isinstance(msg, dict):
        return Stay(Trigger.ok)

    sender = msg.get("from")
    if not (isinstance(sender, dict) and sender.get("type") == "digital_twin"):
        return Stay(Trigger.ok)

    intent = msg.get("intent")
    if intent == "request":
        their_goals = msg.get("goals") or _message_text(msg)
        our_goals = _read_text(WORKSPACE_DIR / "GOALS.md")
        align, rationale = await _assess_goal_alignment(str(their_goals), our_goals)
        if align:
            plan = await _plan_discussion(msg)
            await _update_plan_memory(plan.get("report", ""), plan.get("lt_memory", ""), plan.get("st_memory", ""))
            decision = plan.get("decision", "")
            reply = plan.get("reply", "")
            msg_text = "I think our goals align and I'd like to discuss."
            if rationale:
                msg_text = f"{msg_text} {rationale}"
            if decision:
                msg_text = f"{msg_text} Initial strategy: {decision}"
            if reply:
                msg_text = f"{msg_text}\n\n{reply}"
            async with negotiation_lock:
                NEGOTIATION_QUEUE.append({
                    "to": sender,
                    "assessment": rationale,
                    "their_goals": str(their_goals),
                    "message": msg_text,
                })
            return Move(Trigger.ok)
        return Stay(Trigger.ok)

    if intent == "response":
        if msg.get("to") != AGENT_ID_OBJ:
            return Stay(Trigger.ok)
        willing = await _assess_response_interest(_message_text(msg))
        return Move(Trigger.ok) if willing else Stay(Trigger.ok)

    return Stay(Trigger.ok)


@client.receive(route="plan")
async def on_plan(msg: Any) -> Event:
    if not isinstance(msg, dict):
        return Stay(Trigger.ok)
    plan = await _plan_discussion(msg)
    await _update_plan_memory(plan.get("report", ""), plan.get("lt_memory", ""), plan.get("st_memory", ""))
    reply = plan.get("reply", "")
    decision = plan.get("decision", "")
    if decision:
        reply = f"{reply}\n\nCurrent best strategy: {decision}".strip()
    if reply:
        async with plan_reply_lock:
            PLAN_REPLY_QUEUE.append({"to": msg.get("from"), "message": reply})
    return Stay(Trigger.ok)

@client.send(route="learn --> hello", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_follow_up() -> Optional[dict]:
    await asyncio.sleep(0.5)
    async with follow_up_lock:
        if not FOLLOW_UP_QUEUE:
            return None
        item = FOLLOW_UP_QUEUE.pop(0)
    if not item or not item.get("payload") or not item.get("to"):
        return None
    return item

async def _build_plan_request() -> Optional[dict]:
    global _last_plan_request_ts, _cached_plan_request, _cached_goals_mtime
    client.logger.info("[send on hello] triggered; building plan request")

    now = asyncio.get_event_loop().time()
    if now - _last_plan_request_ts < PLAN_REQUEST_COOLDOWN_SEC:
        return None
    await asyncio.sleep(1.0)
    goals_path = WORKSPACE_DIR / "GOALS.md"
    goals_text = _read_text(goals_path)
    summary = await _summarize_goals_text(goals_text)
    client.logger.info(f"[plan req] now={now:.3f} last={_last_plan_request_ts:.3f} cooldown={PLAN_REQUEST_COOLDOWN_SEC}")
    client.logger.info(f"[plan req] goals_text_len={len(goals_text)}")
    client.logger.info(f"[plan req] summary_len={len(summary) if summary else 0}")
    if not summary:
        if not goals_text.strip():
            summary = "No goals provided yet."
        else:
            summary = "Goals were provided but summarization returned empty."
    _last_plan_request_ts = now
    payload = {
        "intent": "request",
        "to": None,
        "message": f"Is any agent available to help with these goals? {summary}",
        "goals": summary,
    }
    _cached_plan_request = payload
    _cached_goals_mtime = _file_mtime(goals_path)
    return payload


@client.send(route="learn --> hello", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_plan_request_on_hello() -> Optional[dict]:
    await asyncio.sleep(0.1)
    return await _build_plan_request()


@client.send(route="hello")
async def send_plan_request_while_idle() -> Optional[dict]:
    global _last_plan_request_ts, _cached_plan_request, _cached_goals_mtime
    client.logger.info(f"[send idle] states={states} hello_in={'hello' in states}")
    await asyncio.sleep(0.5)
    if "hello" not in states:
        return None
    now = asyncio.get_event_loop().time()
    client.logger.info(f"[send idle] now={now:.3f} last={_last_plan_request_ts:.3f} cooldown={PLAN_REQUEST_COOLDOWN_SEC}")
    if now - _last_plan_request_ts < PLAN_REQUEST_COOLDOWN_SEC:
        return None
    goals_path = WORKSPACE_DIR / "GOALS.md"
    current_mtime = _file_mtime(goals_path)
    if _cached_plan_request and _cached_goals_mtime == current_mtime:
        _last_plan_request_ts = now
        return _cached_plan_request
    _cached_plan_request = None
    _cached_goals_mtime = None
    return await _build_plan_request()


@client.send(route="hello --> plan", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_plan_response() -> Optional[dict]:
    await asyncio.sleep(0.5)
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
    }


@client.send(route="plan", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_plan_message() -> Optional[dict]:
    await asyncio.sleep(0.5)
    async with plan_reply_lock:
        if not PLAN_REPLY_QUEUE:
            return None
        item = PLAN_REPLY_QUEUE.pop(0)
    if not item or not item.get("message"):
        return None
    return {
        "to": item.get("to"),
        "message": item["message"],
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

    # Start visual window (browser) and build graph from dna
    viz.attach_logger(client.logger)
    viz.start(open_browser=True)
    viz.set_graph_from_dna(json.loads(client.dna()), parse_route=client_flow.parse_route)
    viz.push_states(["learn"])

    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
