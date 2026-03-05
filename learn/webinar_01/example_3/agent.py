import argparse, json, asyncio
# NEW vs Example 2:
# - Adds `asyncio` because we introduce an async "send loop" that sleeps between sends.
from typing import Any

from summoner.client import SummonerClient
from summoner.protocol import Test, Event
from summoner.visionary import ClientFlowVisualizer


AGENT_ID = "ChangeMe_Agent_3"
viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=8710)

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()

state="register"

@client.upload_states()
async def upload_states(_: Any) -> list[str]:
    # Unchanged vs Example 2:
    # - Publishes a single local state string to the visualizer/runtime.
    viz.push_states([state])
    return state

@client.receive(route="register")
async def on_register(msg: Any) -> Event: 
    # Unchanged vs Example 2:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="contact")
async def on_contact(msg: Any) -> Event: 
    # Unchanged vs Example 2:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="friend")
async def on_friend(msg: Any) -> Event: 
    # Unchanged vs Example 2:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="ban")
async def on_ban(msg: Any) -> Event: 
    # Unchanged vs Example 2:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.send(route="clock")
async def send_on_clock() -> str:
    # NEW vs Example 2:
    # - Introduces the first "send route" handler.
    # - The decorator binds this coroutine to the flow route/state named "clock".
    # - The framework calls this periodically (depending on how the flow defines "clock"),
    #   to produce outbound content.
    viz.push_states(["clock"])
    # What this does:
    # - Updates the visualizer so you can see when the send loop is active.
    await asyncio.sleep(3)
    # What this does:
    # - Adds a deliberate delay between outbound messages.
    # - This is important in simulations to avoid tight loops / spam, and it models
    #   time passing between broadcasts.
    return "hello"
    # What this does:
    # - Returns a plain string as the outgoing payload.
    # - In later steps, this often becomes a dict like {"to": ..., "message": ...},
    #   but here it is a minimal "broadcast text" placeholder.

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
    # - The agent now has both:
    #   1) receive handlers (still logging + Test(ok))
    #   2) a periodic sender on route "clock" that emits "hello" every ~3 seconds
    # - This is the first step where the agent actively generates traffic, not just reacts.
