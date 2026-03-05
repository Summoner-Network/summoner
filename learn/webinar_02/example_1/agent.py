import argparse
import asyncio

from summoner.client import SummonerClient

# WEBINAR STEP 1 (baseline):
# - Minimal send-only agent.
# - No receive handler, no hooks, no IDs, no LLM.
# - Sends one message every 2 seconds.

client = SummonerClient(name="Webinar02_Send_1")


@client.send(route="")
async def send_every_2s() -> str:
    await asyncio.sleep(2)
    return "Hello from webinar_02 example_1"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Webinar 02 - example 1")
    parser.add_argument("--config", dest="config_path", required=False, help="Client config path (JSON)")
    args = parser.parse_args()

    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
