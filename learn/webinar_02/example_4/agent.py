import argparse
import asyncio
import json
import subprocess
from typing import Any, Optional

from dotenv import load_dotenv

from summoner.client import SummonerClient
from summoner.curl_tools import CurlToolCompiler, SecretResolver

# WEBINAR STEP 4 (NEW vs example_3):
# - Same GPT flow, but backend is now selectable:
#   OpenAI, Claude, or OpenClaw.
# - Core teaching point: keep one llm_text(...) interface while swapping providers.

load_dotenv()

client = SummonerClient(name="Webinar02_LLMChoice_4")

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
# NEW: second curl client (Anthropic) to demonstrate provider swapping.

LLM_MODEL = "openai"
MODEL_ID = "gpt-4o-mini"
OPENCLAW_AGENT = ""
latest_message: Optional[Any] = None


def _openclaw(agent: str, prompt: str) -> str:
    # NEW: OpenClaw path uses local CLI instead of HTTP API.
    cmd = ["openclaw", "agent", "--agent", agent, "--message", prompt]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return result.stderr.strip()


async def llm_text(prompt: str) -> str:
    # NEW: single dispatch point that hides provider-specific details.
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


@client.receive(route="")
async def receive_message(msg: Any) -> None:
    global latest_message
    if isinstance(msg, dict) and "content" in msg:
        latest_message = msg["content"]


@client.send(route="")
async def respond_with_selected_model() -> Optional[dict]:
    global latest_message
    await asyncio.sleep(2)
    if latest_message is None:
        return None

    incoming = latest_message
    latest_message = None

    prompt = (
        "Reply in one short sentence to this message.\n"
        f"Message: {json.dumps(incoming, ensure_ascii=False)}"
    )
    try:
        text = await llm_text(prompt)
    except Exception as exc:
        text = f"LLM error: {exc}"

    return {"message": text}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Webinar 02 - example 4")
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
