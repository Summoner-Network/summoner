import argparse
import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

from summoner.client import SummonerClient
from summoner.curl_tools import CurlToolCompiler, SecretResolver
from summoner.protocol import Direction

# WEBINAR STEP 5 (NEW vs example_4):
# - Adds id.json-based identity.
# - Adds SEND hook to sign every outgoing message with {"from": my_id}.
# - Keeps provider switching from step 4.

load_dotenv()

client = SummonerClient(name="Webinar02_Signed_5")

base_dir = Path(__file__).resolve().parent
id_path = base_dir / "id.json"
my_id = json.loads(id_path.read_text(encoding="utf-8"))
# NEW: identity is loaded from local id.json for stable sender identity.

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
    "max_tokens": 200,
    "messages":[{"role":"user","content":"{{prompt}}"}]
  }'
""")

LLM_MODEL = "openai"
MODEL_ID = "gpt-4o-mini"
OPENCLAW_AGENT = ""
latest_message: Optional[Any] = None
latest_sender: Optional[Any] = None


def _openclaw(agent: str, prompt: str) -> str:
    cmd = ["openclaw", "agent", "--agent", agent, "--message", prompt]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return result.stderr.strip()


async def llm_text(prompt: str) -> str:
    if LLM_MODEL == "openai":
        s = await gpt_client.call({"model_id": MODEL_ID, "prompt": prompt})
        return s.response_json["output"][0]["content"][0]["text"].strip()
    if LLM_MODEL == "claude":
        s = await claude_client.call({"model_id": MODEL_ID, "prompt": prompt})
        return s.response_json["content"][0]["text"].strip()
    if LLM_MODEL == "openclaw":
        if not OPENCLAW_AGENT:
            return "Missing --openclaw-agent."
        return await asyncio.to_thread(_openclaw, OPENCLAW_AGENT, prompt)
    return "Unsupported model backend."


@client.hook(direction=Direction.SEND)
async def sign(msg: Any) -> Optional[dict]:
    # NEW: centralized signing point; handlers do not need to repeat this logic.
    if isinstance(msg, str):
        msg = {"message": msg}
    if not isinstance(msg, dict):
        return None
    msg["from"] = my_id
    return msg


@client.receive(route="")
async def receive_message(msg: Any) -> None:
    global latest_message, latest_sender
    if not (isinstance(msg, dict) and "content" in msg):
        return

    content = msg["content"]
    if isinstance(content, dict):
        latest_message = content.get("message", content)
        latest_sender = content.get("from")
    else:
        latest_message = content
        latest_sender = None


@client.send(route="")
async def respond_with_signature() -> Optional[dict]:
    global latest_message, latest_sender
    await asyncio.sleep(2)
    if latest_message is None:
        return None

    incoming = latest_message
    sender = latest_sender
    latest_message = None
    latest_sender = None

    prompt = (
        "Reply in one short sentence to this message.\n"
        f"Message: {json.dumps(incoming, ensure_ascii=False)}"
    )
    try:
        text = await llm_text(prompt)
    except Exception as exc:
        text = f"LLM error: {exc}"

    out = {"message": text}
    if sender:
        out["to"] = sender
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Webinar 02 - example 5")
    parser.add_argument("--model", choices=["openai", "claude", "openclaw"], default="openai")
    parser.add_argument("--model-id", default=None, help="Model id for OpenAI/Claude")
    parser.add_argument("--openclaw-agent", default="", help="OpenClaw agent name")
    parser.add_argument("--config", dest="config_path", required=False, help="Client config path (JSON)")
    args = parser.parse_args()

    LLM_MODEL = args.model
    if args.model_id:
        MODEL_ID = args.model_id
    elif LLM_MODEL == "claude":
        MODEL_ID = "claude-3-haiku-20240307"
    elif LLM_MODEL == "openclaw":
        MODEL_ID = ""
    OPENCLAW_AGENT = args.openclaw_agent

    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
