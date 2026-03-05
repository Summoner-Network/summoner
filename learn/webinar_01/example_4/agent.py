import argparse, json, asyncio
from typing import Any, Optional
# NEW vs Example 3:
# - Adds `Optional` because hooks can "drop" a message by returning None.

from summoner.client import SummonerClient
from summoner.protocol import Test, Event, Direction
# NEW vs Example 3:
# - Imports `Direction` to declare whether a hook runs on SEND or RECEIVE.
from summoner.visionary import ClientFlowVisualizer


AGENT_ID = "ChangeMe_Agent_4"
viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=8710)

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()

state="register"

@client.upload_states()
async def upload_states(_: Any) -> list[str]:
    # Unchanged vs Example 3:
    viz.push_states([state])
    return state

@client.receive(route="register")
async def on_register(msg: Any) -> Event: 
    # Unchanged vs Example 3:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="contact")
async def on_contact(msg: Any) -> Event: 
    # Unchanged vs Example 3:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="friend")
async def on_friend(msg: Any) -> Event: 
    # Unchanged vs Example 3:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="ban")
async def on_ban(msg: Any) -> Event: 
    # Unchanged vs Example 3:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.send(route="clock")
async def send_on_clock() -> str: 
    # Unchanged vs Example 3:
    viz.push_states(["clock"])
    await asyncio.sleep(3)
    return "hello"

@client.hook(direction=Direction.SEND)
async def sign(msg: Any) -> Optional[dict]:
    # NEW vs Example 3:
    # - Introduces the first SEND hook, which intercepts outbound messages.
    # - Hooks run "around" the normal send/receive pipeline and can transform or reject messages.
    client.logger.info(f"[hook:send] sign {AGENT_ID}")
    # What this does:
    # - Logs that we are applying an outbound transformation (named "sign" here).
    # - In later steps, this is commonly where crypto signing/enveloping happens.
    if isinstance(msg, str): msg = {"message": msg}
    # What this does:
    # - Normalizes a raw string into a structured dict payload.
    # - This makes downstream routing consistent: everything becomes a dict with "message".
    if not isinstance(msg, dict): return
    # What this does:
    # - If the outbound payload isn't a string or dict, we drop it by returning None.
    # - Returning None from a hook is a filtering mechanism: message will not be sent.
    msg.update({"from": AGENT_ID})
    # What this does:
    # - Adds the sender identity into the outbound payload.
    # - This is a core step for multi-agent simulations: receivers can attribute messages to agents.
    return msg
    # What this does:
    # - Returns the modified dict to the framework, which then continues sending it.

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    # Start visual window (browser) and build graph from dna
    viz.attach_logger(client.logger)
    viz.start(open_browser=True)
    viz.set_graph_from_dna(json.loads(client.dna()), parse_route=client_flow.parse_route)
    viz.push_states(["register"])

    client.run(host = "187.77.102.80", port = 8888, config_path=args.config_path or "configs/client_config.json")
    # RUNTIME BEHAVIOR (new overall effect in this step):
    # - The clock sender still produces "hello" every ~3 seconds.
    # - But now every outbound message passes through `sign`:
    #   - "hello" becomes {"message": "hello", "from": "ChangeMe_Agent_4"}
    # - This is the first place where the agent asserts identity on the wire.
