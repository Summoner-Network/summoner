import argparse
import asyncio
import json
from typing import Any, Optional

from dotenv import load_dotenv

from summoner.client import SummonerClient
from summoner.curl_tools import CurlToolCompiler, SecretResolver

# WEBINAR STEP 3 (NEW vs example_2):
# - Keeps the same receive -> send pattern.
# - Replaces plain echo with a GPT call using curl tools only.
# - Uses OpenAI backend only in this step (simple first LLM responder).

load_dotenv()

client = SummonerClient(name="Webinar02_GPT_3")

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
# NEW: curl template compiled into an async callable client.

MODEL_ID = "gpt-4o-mini"
latest_message: Optional[Any] = None


def _extract_text(response_json: dict) -> str:
    # NEW: tiny parser so the send handler stays easy to read.
    try:
        return response_json["output"][0]["content"][0]["text"].strip()
    except Exception:
        return json.dumps(response_json, ensure_ascii=False)


@client.receive(route="")
async def receive_message(msg: Any) -> None:
    global latest_message
    if not (isinstance(msg, dict) and "content" in msg):
        return
    latest_message = msg["content"]


@client.send(route="")
async def respond_with_gpt() -> Optional[dict]:
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
        result = await gpt_client.call({"model_id": MODEL_ID, "prompt": prompt})
        text = _extract_text(result.response_json)
    except Exception as exc:
        text = f"LLM error: {exc}"

    return {"message": text}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Webinar 02 - example 3")
    parser.add_argument("--model-id", default=MODEL_ID, help="OpenAI model id")
    parser.add_argument("--config", dest="config_path", required=False, help="Client config path (JSON)")
    args = parser.parse_args()

    MODEL_ID = args.model_id
    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")

