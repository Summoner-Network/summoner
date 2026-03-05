import argparse
import asyncio
import json
from typing import Any, Optional

from summoner.client import SummonerClient

# WEBINAR STEP 2 (NEW vs example_1):
# - Adds a receive handler.
# - Adds a global variable that bridges receive -> send.
# - Send handler now echoes what was received (still on a 2-second loop).

client = SummonerClient(name="Webinar02_Echo_2")

# Global variable used by receive -> send.
latest_message: Optional[Any] = None


@client.receive(route="")
async def receive_message(msg: Any) -> None:
    global latest_message
    # NEW: basic envelope check before extracting the actual payload.
    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg):
        return
    # NEW: remember the latest inbound payload for the send loop to echo.
    latest_message = msg["content"]
    client.logger.info(f"Buffered from {msg['remote_addr']}: {json.dumps(latest_message)}")


@client.send(route="")
async def echo_message() -> Optional[Any]:
    global latest_message
    await asyncio.sleep(2)
    if latest_message is None:
        return None
    outgoing = latest_message
    latest_message = None
    return outgoing


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Webinar 02 - example 2")
    parser.add_argument("--config", dest="config_path", required=False, help="Client config path (JSON)")
    args = parser.parse_args()

    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")

