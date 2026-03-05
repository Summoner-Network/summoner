import argparse, json
from typing import Any

from summoner.client import SummonerClient
from summoner.protocol import Test, Event
# NEW vs empty agent:
# - We now import protocol primitives that define "what a handler returns".
# - `Event` is the return type expected by state handlers.
# - `Test(...)` is a simple event used for "do nothing except signal a trigger".
from summoner.visionary import ClientFlowVisualizer


AGENT_ID = "ChangeMe_Agent_1"
viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=8710)

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()
# NEW vs empty agent:
# - We obtain `Trigger` from the activated flow. This object contains the named triggers
#   defined by the client flow graph (ex: `Trigger.ok`).
# - Handlers will now return events that reference these triggers, so the flow engine
#   can interpret outcomes consistently.

@client.receive(route="register")
async def on_register(msg: Any) -> Event:
    # NEW vs empty agent:
    # - This is the first concrete "receive route" handler.
    # - The decorator binds this coroutine to the flow state named "register".
    # - When a message arrives *and the sender is currently in state 'register'*,
    #   this function is invoked.
    client.logger.info(msg)
    # NEW logic:
    # - We emit a `Test(Trigger.ok)` event.
    # - `Test(...)` is a minimal "ack-like" event that tells the state machine:
    #   "I handled the message, produce trigger 'ok'".
    # - This does not itself cause a state change here (that would require transitions
    #   defined in the flow and an action like Move/Stay). It mainly drives downstream
    #   trigger-based logic.
    return Test(Trigger.ok)

@client.receive(route="contact")
async def on_contact(msg: Any) -> Event:
    # NEW vs empty agent:
    # - Same pattern as `register`, but for state "contact".
    # - This creates a distinct behavior hook per state: you can later evolve each
    #   state's logic independently while keeping signatures stable.
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="friend")
async def on_friend(msg: Any) -> Event:
    # NEW vs empty agent:
    # - Same as above, for state "friend".
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="ban")
async def on_ban(msg: Any) -> Event:
    # NEW vs empty agent:
    # - Same as above, for state "ban".
    # - Even banned agents are still "handled" here (logged + Test(ok)),
    #   which is useful as a placeholder before implementing real filtering.
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
    # NOTE (unchanged but now meaningful with new handlers):
    # - We push the visual state to "register" so the visualization starts there.
    # - This matches the fact we now have a real handler for "register".

    client.run(host = "187.77.102.80", port = 8888, config_path=args.config_path or "configs/client_config.json")
    # RUNTIME BEHAVIOR (new overall effect in this step):
    # - The agent can now receive messages in four states (register/contact/friend/ban).
    # - In all states, it logs the incoming message and returns Test(ok),
    #   which produces a trigger outcome but does not yet implement transitions,
    #   relationship tracking, sending, or hooks.
