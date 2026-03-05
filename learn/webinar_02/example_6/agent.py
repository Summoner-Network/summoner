import argparse
import asyncio
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

from summoner.client import SummonerClient
from summoner.curl_tools import CurlToolCompiler, SecretResolver
from summoner.protocol import Direction

# WEBINAR STEP 6 (NEW vs example_5):
# - Adds RECEIVE hook that accepts only signed messages (must include content.from).
# - Stores conversation history per sender id.
# - Injects per-id history as explicit "past context" for GPT/Claude calls.
# - OpenClaw path sends only current message (it already remembers context).

load_dotenv()

client = SummonerClient(name="Webinar02_Context_6")

base_dir = Path(__file__).resolve().parent
id_path = base_dir / "id.json"
my_id = json.loads(id_path.read_text(encoding="utf-8"))

secrets = SecretResolver()
compiler = CurlToolCompiler(secrets=secrets)

gpt_client = compiler.parse(r"""
curl https://api.openai.com/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model":"{{model_id}}",
    "input":"{{prompt}}"
  }'
""")

claude_client = compiler.parse(r"""
curl https://api.anthropic.com/v1/messages \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -H "X-Api-Key: $ANTHROPIC_API_KEY" \
  -d '{
    "model":"{{model_id}}",
    "max_tokens": 250,
    "messages":[{"role":"user","content":"{{prompt}}"}]
  }'
""")

LLM_MODEL = "openai"
MODEL_ID = "gpt-4o-mini"
OPENCLAW_AGENT = ""
MAX_CONTEXT_ITEMS = 12

pending_message: Optional[str] = None
pending_sender: Optional[Any] = None

# Stores interactions by sender id.
conversation_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)


def _sender_key(sender: Any) -> str:
    # Sender can be JSON (dict/list/scalar). Convert to stable hashable key.
    if isinstance(sender, str):
        return sender
    try:
        return json.dumps(sender, sort_keys=True, ensure_ascii=False)
    except Exception:
        return str(sender)


def _openclaw(agent: str, prompt: str) -> str:
    cmd = ["openclaw", "agent", "--agent", agent, "--message", prompt]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return result.stderr.strip()


def _format_context(sender: Any) -> str:
    # NEW: build an explicit context block so LLM sees history as past-only.
    history = conversation_by_id.get(_sender_key(sender), [])
    if not history:
        return "No past context."

    lines = ["Past context only (do not answer these past lines):"]
    for item in history[-MAX_CONTEXT_ITEMS:]:
        role = item.get("role", "unknown")
        text = item.get("text", "")
        lines.append(f"- {role}: {text}")
    return "\n".join(lines)


async def llm_text(sender: Any, user_text: str) -> str:
    # NEW: prompt explicitly separates historical context from current message.
    past_context = _format_context(sender)
    prompt = (
        "You are an assistant in a message loop.\n"
        "Use the past context only as background memory.\n"
        "Do not answer the past context directly.\n\n"
        f"{past_context}\n\n"
        f"Current message (respond only to this): {user_text}"
    )

    if LLM_MODEL == "openai":
        s = await gpt_client.call({"model_id": MODEL_ID, "prompt": prompt})
        return s.response_json["output"][0]["content"][0]["text"].strip()
    if LLM_MODEL == "claude":
        s = await claude_client.call({"model_id": MODEL_ID, "prompt": prompt})
        return s.response_json["content"][0]["text"].strip()
    if LLM_MODEL == "openclaw":
        if not OPENCLAW_AGENT:
            return "Missing --openclaw-agent."
        # OpenClaw already remembers context, so send only current message.
        return await asyncio.to_thread(_openclaw, OPENCLAW_AGENT, user_text)
    return "Unsupported model backend."


@client.hook(direction=Direction.RECEIVE)
async def signed_only(msg: Any) -> Optional[dict]:
    # NEW: hard gate - drop unsigned payloads before receive handlers.
    if not (isinstance(msg, dict) and "content" in msg):
        return None
    content = msg["content"]
    if not isinstance(content, dict):
        client.logger.info("[hook:recv] dropped unsigned (content not dict)")
        return None
    if "from" not in content:
        client.logger.info("[hook:recv] dropped unsigned (missing content.from)")
        return None

    return msg


@client.hook(direction=Direction.SEND)
async def sign(msg: Any) -> Optional[dict]:
    if isinstance(msg, str):
        msg = {"message": msg}
    if not isinstance(msg, dict):
        return None
    msg["from"] = my_id
    return msg


@client.receive(route="")
async def receive_message(msg: dict) -> None:
    global pending_message, pending_sender
    content = msg["content"]
    sender = content["from"]
    message = content.get("message")
    if message is None:
        message = json.dumps(content, ensure_ascii=False)
    else:
        message = str(message)

    # NEW: memory write for this sender id.
    conversation_by_id[_sender_key(sender)].append({"role": "user", "text": message})
    pending_sender = sender
    pending_message = message


@client.send(route="")
async def respond_with_context() -> Optional[dict]:
    global pending_message, pending_sender
    await asyncio.sleep(2)
    if pending_message is None or pending_sender is None:
        return None

    sender = pending_sender
    message = pending_message
    pending_sender = None
    pending_message = None

    try:
        text = await llm_text(sender, message)
    except Exception as exc:
        text = f"LLM error: {exc}"

    # NEW: memory write for assistant turn so next response has local history.
    conversation_by_id[_sender_key(sender)].append({"role": "assistant", "text": text})
    return {"to": sender, "message": text}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Webinar 02 - example 6")
    parser.add_argument("--model", choices=["openai", "claude", "openclaw"], default="openai")
    parser.add_argument("--model-id", default=None, help="Model id for OpenAI/Claude")
    parser.add_argument("--openclaw-agent", default="", help="OpenClaw agent name")
    parser.add_argument("--max-context-items", type=int, default=12)
    parser.add_argument("--config", dest="config_path", required=False, help="Client config path (JSON)")
    args = parser.parse_args()

    LLM_MODEL = args.model
    MAX_CONTEXT_ITEMS = max(1, args.max_context_items)
    if args.model_id:
        MODEL_ID = args.model_id
    elif LLM_MODEL == "claude":
        MODEL_ID = "claude-3-haiku-20240307"
    elif LLM_MODEL == "openclaw":
        MODEL_ID = ""
    OPENCLAW_AGENT = args.openclaw_agent

    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
