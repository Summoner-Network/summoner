import argparse, json, asyncio
from typing import Any, Optional

from summoner.client import SummonerClient
from summoner.protocol import Test, Move, Stay, Event, Direction, Node
from summoner.visionary import ClientFlowVisualizer

state_lock = asyncio.Lock()
# Unchanged vs Example 9:
# - Still the concurrency guard for shared mutable state.

relations = {}
# NEW vs Example 9:
# - Introduces a dictionary that tracks *per-sender* state, rather than a single global state.
# - Conceptually: relations[sender_id] = our current state relative to that sender.
#   (in later versions this becomes "to_me" relations, etc.)

import random
AGENT_ID = f"ChangeMe_Agent_10_{random.randint(0,1000)}"
# CHANGE vs Example 9:
# - AGENT_ID is now randomized with a numeric suffix.
# - This lets you launch many copies of the same file without ID collisions.
# - It also aligns with the later allowlist logic that matches by prefix.
viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=random.randint(7777,8887))
# CHANGE vs Example 9:
# - Visualizer port is now randomized too, avoiding port conflicts when many agents run.

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()


@client.hook(direction=Direction.RECEIVE, priority=0)
async def validate(msg: Any) -> Optional[dict]:
    # Mostly unchanged:
    # - Envelope validation, addressing filter, "from" required.
    # Subtle consequence of randomized AGENT_ID:
    # - Direct messages now require `content["to"] == AGENT_ID` (with the random suffix),
    #   so peers must know the full ID to DM; broadcasts still work via to=None.
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
    # CHANGE vs Example 9:
    # - Sender allowlist now uses *prefix matching* instead of exact string equality.
    # - This is necessary because AGENT_IDs now have random suffixes.
    if  any(s == content.get("from","")[:len(s)] for s in [
                                "ChangeMe_Agent_6",
                                "ChangeMe_Agent_7",
                                "ChangeMe_Agent_8",
                                "ChangeMe_Agent_9",
                                "ChangeMe_Agent_10",
                            ]): 
        return content
    else:
        client.logger.info(f"[hook:recv] reject 'from':{content.get('from')} | 'type':{content.get('type')}")
        # Same behavior:
        # - If sender not allowed, message is dropped (None implicitly returned).


@client.upload_states()
async def upload_states(msg: Any) -> Any:
    # CHANGE vs Example 9:
    # - upload_states no longer publishes a single global `state`.
    # - It now reacts to the incoming message (`msg`) and maintains per-sender entries
    #   in `relations`.
    global relations
    sender_id = msg.get("from")
    # What this does:
    # - Extracts the sender identity from the *already-validated* inbound content dict.
    # - If `sender_id` is missing, it cannot assign a per-sender relation state.
    if not sender_id: return

    async with state_lock:
        relations.setdefault(sender_id, "register")
        # What this does:
        # - Initializes a new sender with default state "register" the first time we see them.
        # - `setdefault` means we do not overwrite existing state for known senders.

        viz.push_states(relations)
        # What this does:
        # - Pushes the entire per-sender mapping into the visualizer.
        # - This is a major conceptual shift: the UI is now showing a "map of peers" rather
        #   than "my single current state".

    return { sender_id: relations[sender_id] }
    # What this does:
    # - Returns a minimal state snapshot keyed by sender.
    # - This sets up the next step: download_states will feed back per-sender state updates
    #   using the same sender_id keys.


@client.receive(route="register --> contact")
async def on_register(msg: Any) -> Optional[Event]:
    # Same edge rule as Example 9:
    if msg["message"] == "Hello": 
        return Move(Trigger.ok)

@client.receive(route="register --> ban")
async def on_register(msg: Any) -> Optional[Event]:
    # Same edge rule:
    if msg["message"] == "I don't like you": 
        return Move(Trigger.ok)

@client.receive(route="contact --> friend")
async def on_register(msg: Any) -> Optional[Event]:
    # Same edge rule:
    if msg["message"] == "I like you": 
        return Move(Trigger.ok)


@client.download_states()
async def download_states(possible_states: dict[str, list[Node]]) -> None:
    # CHANGE vs Example 9:
    # - download_states now receives a dict keyed by sender_id, not a flat list.
    # - This matches the per-sender upload_states return shape.
    global relations
    for sender_id, sender_states in possible_states.items():
        # What this does:
        # - Iterates over each peer and the list of states the engine says are possible/active
        #   for that peer relationship.
        states = [s for s in sender_states if str(s) != str(relations[sender_id])]
        # What this does:
        # - Filters out the state we already think we have for that sender,
        #   leaving only "new" states.
        if states:
            async with state_lock:
                relations[sender_id] = states[0]
                # What this does:
                # - Updates our stored per-sender state to the first new state.
        viz.push_states(relations)
        # What this does:
        # - Updates the visualizer after each sender update.
        # - This makes the UI reflect a continuously updated per-sender map.


@client.receive(route="register")
async def on_register(msg: Any) -> Event: 
    # Unchanged:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="contact")
async def on_contact(msg: Any) -> Event: 
    # Unchanged:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="friend")
async def on_friend(msg: Any) -> Event: 
    # Unchanged:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="ban")
async def on_ban(msg: Any) -> Event: 
    # Unchanged:
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.send(route="clock")
async def send_on_clock() -> str: 
    # Unchanged:
    async with state_lock:
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
    # - The agent shifts from "single-state UI" to "per-sender relation tracking":
    #   - Seeing a new sender creates relations[sender_id] = "register".
    #   - download_states updates each sender's state as transitions occur.
    # - IDs and ports are randomized, enabling many instances.
    # - Allowlist adapts by matching ID prefixes.
