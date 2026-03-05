import argparse, json, asyncio
from typing import Any, Optional

from summoner.client import SummonerClient
from summoner.protocol import Test, Move, Stay, Event, Direction, Node, Action
# CHANGE vs Example 10:
# - Imports `Action`.
# - `Action` is used to conditionally run send handlers depending on what just happened
#   in the state machine (ex: only send when a MOVE occurred).
from summoner.visionary import ClientFlowVisualizer

state_lock = asyncio.Lock()

relations = {}

import random
AGENT_ID = f"ChangeMe_Agent_11_{random.randint(0,1000)}"
viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=random.randint(7777,8887))

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()


@client.hook(direction=Direction.RECEIVE, priority=0)
async def validate(msg: Any) -> Optional[dict]:
    # Unchanged vs Example 10:
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
    # CHANGE vs Example 10:
    # - Allowlist prefix list expanded to include Agent_11.
    if  any(s == content.get("from","")[:len(s)] for s in [
                                "ChangeMe_Agent_6",
                                "ChangeMe_Agent_7",
                                "ChangeMe_Agent_8",
                                "ChangeMe_Agent_9",
                                "ChangeMe_Agent_10",
                                "ChangeMe_Agent_11",
                            ]): 
        return content
    else:
        client.logger.info(f"[hook:recv] reject 'from':{content.get('from')} | 'type':{content.get('type')}")


@client.upload_states()
async def upload_states(msg: Any) -> Any:
    # Unchanged vs Example 10:
    # - Initialize per-sender relation state and push the relations map to the UI.
    global relations
    sender_id = msg.get("from")
    if not sender_id: return
    async with state_lock:
        relations.setdefault(sender_id, "register")
        viz.push_states(relations)
    return { sender_id: relations[sender_id] }

contact_list = []
# NEW vs Example 10:
# - Introduces an explicit side-channel list for "who just became a contact".
# - This is not the same as `relations`: relations stores the current state;
#   this list stores *recent transition recipients* so we can send them follow-up messages.
@client.receive(route="register --> contact")
async def on_register(msg: Any) -> Optional[Event]:
    global contact_list
    if msg["message"] == "Hello": 
        async with state_lock:
            contact_list.append(msg["from"])
            # What this does:
            # - Records that this sender triggered the register->contact move.
            # - We will later use this to send them a direct follow-up message.
        return Move(Trigger.ok)

ban_list = []
# NEW vs Example 10:
# - Same pattern as contact_list, but for "who just got banned".
@client.receive(route="register --> ban")
async def on_register(msg: Any) -> Optional[Event]: 
    global ban_list
    if msg["message"] == "I don't like you": 
        async with state_lock:
            ban_list.append(msg["from"])
        return Move(Trigger.ok)

friend_list = []
# NEW vs Example 10:
# - Same pattern again, for "who just became a friend".
@client.receive(route="contact --> friend")
async def on_register(msg: Any) -> Optional[Event]: 
    global friend_list
    if msg["message"] == "I like you": 
        async with state_lock:
            friend_list.append(msg["from"])
        return Move(Trigger.ok)


@client.download_states()
async def download_states(possible_states: dict[str, list[Node]]) -> None:
    # Unchanged vs Example 10:
    # - Keeps relations[sender_id] synchronized with engine view.
    global relations
    for sender_id, sender_states in possible_states.items():
        states = [s for s in sender_states if str(s) != str(relations[sender_id])]
        if states:
            async with state_lock:
                relations[sender_id] = states[0]
        viz.push_states(relations)


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

@client.send(route="register --> contact", multi=True, on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_from_register_to_contact():
    # NEW vs Example 10:
    # - Introduces the first *transition-triggered send*.
    # - This send handler is attached to the same edge "register --> contact".
    # - It is additionally gated by:
    #   - on_actions={Action.MOVE}  (only run when the edge actually moved)
    #   - on_triggers={Trigger.ok}  (only run when the triggering outcome was ok)
    # - `multi=True` means it can return multiple outbound messages in one call.
    global contact_list
    try:
        return [{"to": contact_id, "message": "You are my contact"} for contact_id in contact_list]
        # What this does:
        # - Emits a direct message to each sender that we just added to contact_list.
        # - This is the first explicit "reaction message" tied to a state transition.
    finally:
        async with state_lock:
            contact_list = []
            # What this does:
            # - Clears the list after sending, so we don't resend on future cycles.
            # - The try/finally guarantees cleanup even if sending fails mid-way.

@client.send(route="register --> ban", multi=True, on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_from_register_to_contact():
    # NEW vs Example 10:
    # - Same pattern, but for register --> ban.
    global ban_list
    try:
        return [{"to": banned_id, "message": "You are banned"} for banned_id in ban_list]
    finally:
        async with state_lock:
            ban_list = []

@client.send(route="contact --> friend", multi=True, on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_from_register_to_contact():
    # NEW vs Example 10:
    # - Same pattern, but for contact --> friend.
    global friend_list
    try:
        return [{"to": friend_id, "message": "You are my friend"} for friend_id in friend_list]
    finally:
        async with state_lock:
            friend_list = []

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
    # - This step adds "reaction messaging" tied to transitions:
    #   - When a sender causes register->contact, we DM them "You are my contact".
    #   - When a sender causes register->ban, we DM them "You are banned".
    #   - When a sender causes contact->friend, we DM them "You are my friend".
    # - The key new concept is separating:
    #   (a) steady state tracking (relations dict)
    #   (b) one-shot transition side effects (contact_list/ban_list/friend_list)
    # - The `on_actions` + `on_triggers` gating makes these sends fire only when a real move happened.
