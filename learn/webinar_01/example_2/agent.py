import argparse, json
from typing import Any

from summoner.client import SummonerClient
from summoner.protocol import Test, Event
from summoner.visionary import ClientFlowVisualizer


AGENT_ID = "ChangeMe_Agent_2"
viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=8710)

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()

state="register"
# NEW vs Example 1:
# - Introduces a global `state` variable representing "my current state".
# - In this step it is static ("register"), but it establishes the pattern that
#   state can become dynamic later (and can be shared with the visualizer).

@client.upload_states()
async def upload_states(_: Any) -> list[str]:
    # NEW vs Example 1:
    # - Adds an `upload_states` callback.
    # - The Summoner runtime calls this when it wants the client to publish
    #   its local view of state so the visualizer (and possibly others) can update.
    # - In later steps, this is typically how you expose per-peer states, relations,
    #   or any derived state to the outside world.
    viz.push_states([state])
    # What this does:
    # - Pushes the current local state string into the flow visualizer, so you can
    #   see "where the agent is" in the graph UI.
    return state
    # NOTE:
    # - The return type annotation says `list[str]` but we return `state` (a `str`).
    # - Behavior-wise, the important part is that some representation of state is
    #   returned to the framework. This mismatch is likely cleaned up in later files.

@client.receive(route="register")
async def on_register(msg: Any) -> Event: 
    # Unchanged vs Example 1:
    # - Receives messages while in "register".
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="contact")
async def on_contact(msg: Any) -> Event: 
    # Unchanged vs Example 1:
    # - Receives messages while in "contact".
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="friend")
async def on_friend(msg: Any) -> Event: 
    # Unchanged vs Example 1:
    # - Receives messages while in "friend".
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="ban")
async def on_ban(msg: Any) -> Event: 
    # Unchanged vs Example 1:
    # - Receives messages while in "ban".
    client.logger.info(msg)
    return Test(Trigger.ok)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    # Start visual window (browser) and build graph from dna
    viz.attach_logger(client.logger)
    viz.start(open_browser=True)
    viz.set_graph_from_dna(json.loads(client.dna()), parse_route=client_flow.parse_route)
    viz.push_states(["register"])
    # Now there are TWO ways the visualizer can be updated:
    # 1) This initial push in main (bootstraps the UI)
    # 2) The `upload_states` callback (keeps the UI refreshed during runtime)

    client.run(host = "187.77.102.80", port = 8888, config_path=args.config_path or "configs/client_config.json")
    # RUNTIME BEHAVIOR (new overall effect in this step):
    # - The agent still only logs messages and returns Test(ok).
    # - But now it participates in the "state publishing" loop, so the visual UI can
    #   reflect `state` continuously (or whenever the framework asks for state uploads).
