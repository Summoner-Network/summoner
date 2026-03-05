import argparse, json, asyncio
from typing import Any, Optional

from summoner.client import SummonerClient
from summoner.protocol import Test, Move, Stay, Event, Direction, Node
# CHANGE vs Example 7:
# - Introduces state-transition event types:
#   - `Move` (explicitly request a transition along the current route)
#   - `Stay` (explicitly request no transition, if used)
# - Introduces `Node`, which is used by the state download callback.
# In Examples 1-7, handlers returned `Test(Trigger.ok)` which produces a trigger,
# but does not explicitly move the state machine along an edge.

from summoner.visionary import ClientFlowVisualizer


AGENT_ID = "ChangeMe_Agent_8"
viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=8710)

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()


@client.hook(direction=Direction.RECEIVE, priority=0)
async def validate(msg: Any) -> Optional[dict]:
    # Unchanged vs Example 7:
    # - Envelope validation
    # - Addressing filter via `to` (None or AGENT_ID)
    # - Requires `from`
    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg): return
    address: str = msg["remote_addr"]
    content: Any = msg["content"]
    if not isinstance(content, dict): return
    if content.get("to", "") not in [None, AGENT_ID]: return
    if "from" not in content:
        client.logger.info(f"[hook:recv] missing content.from")
        return
    return content

@client.hook(direction=Direction.RECEIVE, priority=1)
async def check_sender(content: dict) -> Optional[dict]:
    # CHANGE vs Example 7:
    # - Expands allowlist again: now includes Agent_8 as well.
    if content.get("from") in [
                                "ChangeMe_Agent_6",
                                "ChangeMe_Agent_7",
                                "ChangeMe_Agent_8",
                            ]: 
        return content
    else:
        client.logger.info(f"[hook:recv] reject 'from':{content.get('from')} | 'type':{content.get('type')}")


state="register"

@client.upload_states()
async def upload_states(_: Any) -> list[str]:
    # Unchanged:
    viz.push_states([state])
    return state


@client.receive(route="register --> contact")
async def on_register(msg: Any) -> Optional[Event]:
    # NEW vs Example 7:
    # - Introduces the first *edge-specific* receive handler.
    # - Route syntax "A --> B" means this handler is associated with the transition edge
    #   from state A to state B.
    # - It can decide whether to perform the transition by returning Move(...) or not.
    if msg["message"] == "Hello":
        # What this does:
        # - If the incoming message content matches exactly "Hello",
        #   we request a state transition along this edge by returning Move(Trigger.ok).
        # - `Trigger.ok` is still attached, but now it accompanies an explicit "move".
        return Move(Trigger.ok)
    # If the condition does not match:
    # - Returns None implicitly, meaning "no event emitted from this edge handler".
    # - The framework can then fall back to other matching handlers (for example,
    #   the plain "register" handler below), depending on its dispatch rules.

@client.receive(route="register --> ban")
async def on_register(msg: Any) -> Optional[Event]:
    # NEW vs Example 7:
    # - Another edge-specific handler, now for register -> ban.
    # - Implements a "negative phrase triggers ban" rule.
    if msg["message"] == "I don't like you":
        return Move(Trigger.ok)

@client.receive(route="contact --> friend")
async def on_register(msg: Any) -> Optional[Event]:
    # NEW vs Example 7:
    # - Edge-specific handler for contact -> friend.
    # - Implements a "positive phrase escalates relationship" rule.
    if msg["message"] == "I like you":
        return Move(Trigger.ok)


@client.download_states()
async def download_states(possible_states: list[Node]) -> None:
    # NEW vs Example 7:
    # - Adds the download_states callback, which is the inverse of upload_states.
    # - The framework can call this to tell the client what state(s) it believes are active
    #   or possible (depending on the engine semantics).
    # - Here the agent uses it purely for visualization: it pushes the received nodes
    #   into the visualizer so the UI reflects the engine's view.
    viz.push_states(possible_states)


@client.receive(route="register")
async def on_register(msg: Any) -> Event:
    # Still present:
    # - This is the generic handler for the "register" node/state.
    # - In this step it acts as a fallback: if no edge rule above matches,
    #   this handler still logs and returns Test(ok).
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="contact")
async def on_contact(msg: Any) -> Event: 
    # Same role as before:
    # - Logs any message received while in "contact".
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="friend")
async def on_friend(msg: Any) -> Event: 
    # Same role as before.
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="ban")
async def on_ban(msg: Any) -> Event: 
    # Same role as before.
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.send(route="clock")
async def send_on_clock() -> str:
    # Unchanged vs Example 7:
    # - Periodic broadcast of "Hello" (now crucial, because "Hello" is used as a trigger
    #   for register --> contact).
    viz.push_states(["clock"])
    await asyncio.sleep(3)
    return {"message": "Hello", "to": None}

@client.hook(direction=Direction.SEND)
async def sign(msg: Any) -> Optional[dict]:
    # Unchanged:
    client.logger.info(f"[hook:send] sign {AGENT_ID}")
    if isinstance(msg, str): msg = {"message": msg}
    if not isinstance(msg, dict): return
    msg.update({"from": AGENT_ID})
    return msg

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
    # - This is the first version that can *change states* based on message content.
    #   Rules introduced:
    #   - In "register": receiving "Hello" can move to "contact"
    #   - In "register": receiving "I don't like you" can move to "ban"
    #   - In "contact": receiving "I like you" can move to "friend"
    # - The clock broadcaster emits "Hello", which can cause other agents to transition
    #   if their current state is "register" and they implement the same edge rule.
    # - `download_states` provides feedback from the engine into the UI, so the visualization
    #   can show transitions as they occur.
