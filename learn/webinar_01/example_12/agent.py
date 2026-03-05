import argparse, json, asyncio
from typing import Any, Optional

from summoner.client import SummonerClient
from summoner.protocol import Test, Move, Stay, Event, Direction, Node, Action
from summoner.visionary import ClientFlowVisualizer

state_lock = asyncio.Lock()

relations = {}
outside_view = {}
# NEW vs Example 11:
# - Splits "relationship state" into two parallel per-sender maps:
#   1) `relations`     : how *we* classify the sender (to_me)
#   2) `outside_view`  : how we believe *they* classify us (to_them)
# - This is the first time the agent tracks a 2-sided social model.

import random
AGENT_ID = f"ChangeMe_Agent_12_{random.randint(0,1000)}"
viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=random.randint(7777,8887))

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()


@client.hook(direction=Direction.RECEIVE, priority=0)
async def validate(msg: Any) -> Optional[dict]:
    # Unchanged vs Example 11:
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
    # CHANGE vs Example 11:
    # - Allowlist expanded to include Agent_12.
    if  any(s == content.get("from","")[:len(s)] for s in [
                                "ChangeMe_Agent_6",
                                "ChangeMe_Agent_7",
                                "ChangeMe_Agent_8",
                                "ChangeMe_Agent_9",
                                "ChangeMe_Agent_10",
                                "ChangeMe_Agent_11",
                                "ChangeMe_Agent_12",
                            ]): 
        return content
    else:
        client.logger.info(f"[hook:recv] reject 'from':{content.get('from')} | 'type':{content.get('type')}")


@client.upload_states()
async def upload_states(msg: Any) -> Any:
    # CHANGE vs Example 11:
    # - upload_states now publishes TWO keyed state streams, and it namespaces them.
    # - Instead of returning { sender_id: state }, it returns:
    #     { "to_me:<sender>": <state>, "to_them:<sender>": <state> }
    # - This lets the flow engine treat "our view" and "their view" as separate state machines.
    global relations, outside_view
    sender_id = msg.get("from")
    if not sender_id: return

    async with state_lock:
        relations.setdefault(sender_id, "register")
        outside_view.setdefault(sender_id, "neutral")
        # What this does:
        # - Initializes both perspectives on first contact:
        #   - we start by treating them as "register"
        #   - we assume they treat us as "neutral"

    view_states = {f"1:{k}": v for k,v in relations.items()}
    view_states.update({f"2:{k}": v for k,v in outside_view.items()})
    # What this does:
    # - Builds a visualization-only dictionary with prefixes "1:" and "2:" so the UI
    #   can show both maps simultaneously without key collisions.

    async with state_lock:
        viz.push_states(view_states)
        # What this does:
        # - Pushes the combined two-layer map to the visualizer.

    return { f"to_me:{sender_id}": relations[sender_id], f"to_them:{sender_id}": outside_view[sender_id] }
    # What this does:
    # - Publishes two "channels" of state to the runtime:
    #   - `to_me:<sender>` is driven by transitions like register->contact->friend->ban
    #   - `to_them:<sender>` is driven by transitions like neutral->good/bad->very_good


contact_list = []
@client.receive(route="register --> contact")
async def on_register(msg: Any) -> Optional[Event]:
    # Unchanged transition rule from Example 11 (our-side classification):
    global contact_list
    if msg["message"] == "Hello": 
        async with state_lock:
            contact_list.append(msg["from"])
        return Move(Trigger.ok)

ban_list = []
@client.receive(route="register --> ban")
async def on_register(msg: Any) -> Optional[Event]: 
    # Unchanged:
    global ban_list
    if msg["message"] == "I don't like you": 
        async with state_lock:
            ban_list.append(msg["from"])
        return Move(Trigger.ok)

friend_list = []
@client.receive(route="contact --> friend")
async def on_register(msg: Any) -> Optional[Event]: 
    # Unchanged:
    global friend_list
    if msg["message"] == "I like you": 
        async with state_lock:
            friend_list.append(msg["from"])
        return Move(Trigger.ok)


to_them_list = []
# NEW vs Example 11:
# - Introduces a second transition pipeline that updates how we will message *them*
#   based on how they reacted to our earlier classification messages.
# - This list is analogous to contact_list/ban_list/friend_list:
#   it stores one-shot "send this status back to them" intents.
@client.receive(route="neutral --> good")
async def on_register(msg: Any) -> Optional[Event]:
    global to_them_list
    if msg["message"] == "You are my contact": 
        # What this does:
        # - If they tell us "You are my contact", we treat that as a positive signal
        #   about how they see us.
        async with state_lock:
            to_them_list.append({"to": msg["from"], "status": "good"})
            # Records an outgoing "good" response to send later.
        return Move(Trigger.ok)

@client.receive(route="neutral --> bad")
async def on_register(msg: Any) -> Optional[Event]: 
    global to_them_list
    if msg["message"] == "You are banned": 
        # What this does:
        # - If they tell us "You are banned", we treat that as a negative signal.
        async with state_lock:
            to_them_list.append({"to": msg["from"], "status": "bad"})
        return Move(Trigger.ok)

@client.receive(route="good --> very_good")
async def on_register(msg: Any) -> Optional[Event]: 
    global to_them_list
    if msg["message"] == "You are my friend": 
        # What this does:
        # - If they escalate us to "friend", we escalate our response status to good again.
        async with state_lock:
            to_them_list.append({"to": msg["from"], "status": "good"})
        return Move(Trigger.ok)


@client.download_states()
async def download_states(possible_states: dict[str, list[Node]]) -> None:
    # CHANGE vs Example 11:
    # - download_states now updates two maps depending on key prefixes:
    #   - keys starting with "to_me:"   update `relations`
    #   - keys starting with "to_them:" update `outside_view`
    global relations, outside_view
    for sender_id_, sender_states in possible_states.items():
        if sender_id_.startswith("to_me:"):
            sender_id = sender_id_.split("to_me:")[1]
            states = [s for s in sender_states if str(s) != str(relations[sender_id])]
            if states:
                async with state_lock:
                    relations[sender_id] = states[0]
        if sender_id_.startswith("to_them:"):
            sender_id = sender_id_.split("to_them:")[1]
            states = [s for s in sender_states if str(s) != str(outside_view[sender_id])]
            if states:
                async with state_lock:
                    outside_view[sender_id] = states[0]

    view_states = {f"1:{k}": v for k,v in relations.items()}
    view_states.update({f"2:{k}": v for k,v in outside_view.items()})
    async with state_lock:
        viz.push_states(view_states)
    # Net effect:
    # - The visualizer now shows a two-layer per-sender state:
    #   1:<sender> = how we classify them
    #   2:<sender> = how we think they classify us


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

@client.send(route="reputation", multi=True)
async def send_on_clock() -> list[str]:
    # NEW vs Example 11:
    # - Adds a second periodic sender called "reputation".
    # - It sends follow-up messages based on `to_them_list` decisions.
    await asyncio.sleep(3)
    async with state_lock:
        viz.push_states(["reputation"])
        # What this does:
        # - Marks reputation-sending activity in the visualizer.

    def msg(string):
        # What this does:
        # - Converts an internal status label into an outbound text that will be interpreted
        #   by the *other agent's* transition rules (see earlier: "I like you" and
        #   "I don't like you" drive register->contact or register->ban elsewhere).
        if string == "good": return "I like you"
        if string == "bad": return "I don't like you"

    return [{"to": d["to"], "message": msg(d["status"])} for d in to_them_list]
    # What this does:
    # - Emits one message per planned "to_them" reaction.
    # - Note: in this step, `to_them_list` is not cleared after sending, so it can resend
    #   the same reactions repeatedly each time this sender runs (until something else changes).


@client.send(route="register --> contact", multi=True, on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_from_register_to_contact():
    # Unchanged vs Example 11:
    global contact_list
    try:
        return [{"to": contact_id, "message": "You are my contact"} for contact_id in contact_list]
    finally:
        async with state_lock:
            contact_list = []

@client.send(route="register --> ban", multi=True, on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_from_register_to_contact():
    # Unchanged:
    global ban_list
    try:
        return [{"to": banned_id, "message": "You are banned"} for banned_id in ban_list]
    finally:
        async with state_lock:
            ban_list = []

@client.send(route="contact --> friend", multi=True, on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_from_register_to_contact():
    # Unchanged:
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
    # - The agent now models a feedback loop between two views:
    #   - to_me: how we categorize them (register/contact/friend/ban)
    #   - to_them: how we think they categorize us (neutral/good/very_good/bad)
    # - It learns the "to_them" view from messages like:
    #   "You are my contact" / "You are banned" / "You are my friend"
    # - It then broadcasts "reputation" responses:
    #   - "I like you" or "I don't like you"
    #   which can drive the other side's own state transitions.
