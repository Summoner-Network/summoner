import argparse, json, asyncio
from typing import Any, Optional

from summoner.client import SummonerClient
from summoner.protocol import Test, Move, Stay, Event, Direction, Node, Action
from summoner.visionary import ClientFlowVisualizer

state_lock = asyncio.Lock()

relations = {}
outside_view = {}
listening = True
# NEW vs Example 12:
# - Adds a global "mode switch" flag: `listening`.
# - While listening=True, the agent is not participating in the social state machine yet.
# - It sits in a special "listen" state and waits for an explicit instruction to start.

import random
AGENT_ID = f"ChangeMe_Agent_13_{random.randint(0,1000)}"
viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=random.randint(7777,8887))

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()


@client.hook(direction=Direction.RECEIVE, priority=0)
async def validate(msg: Any) -> Optional[dict]:
    # CHANGE vs Example 12:
    # - Extends validation to support an out-of-band control command: "/travel".
    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg): return
    address: str = msg["remote_addr"]
    content: Any = msg["content"]

    if content == "/travel" and listening:
        client.logger.info(f"[hook:recv] /travel instruction received")
        return content
    # NEW:
    # - If we're in listening mode and we receive the literal string "/travel",
    #   we allow it through even though it is not a dict payload.
    # - This is a deliberate exception to the normal "content must be dict" protocol.

    if not isinstance(content, dict): return
    # Same as earlier protocol for normal messages: require dict payload.

    if content.get("to", "") not in [None, AGENT_ID]: return
    # Same addressing filter.

    if "from" not in content:
        client.logger.info(f"[hook:recv] missing content.from")
        return
    return content

@client.hook(direction=Direction.RECEIVE, priority=1)
async def check_sender(content: dict) -> Optional[dict]:
    # CHANGE vs Example 12:
    # - Must also pass through the "/travel" control command during listening.
    if content == "/travel" and listening:
        client.logger.info(f"[hook:recv] /travel instruction received")
        return content
    # NEW:
    # - Because validate() can return the literal string "/travel",
    #   this second hook must also accept that special case, or it would reject it.
    elif  any(s == content.get("from","")[:len(s)] for s in [
                                "ChangeMe_Agent_6",
                                "ChangeMe_Agent_7",
                                "ChangeMe_Agent_8",
                                "ChangeMe_Agent_9",
                                "ChangeMe_Agent_10",
                                "ChangeMe_Agent_11",
                                "ChangeMe_Agent_12",
                                "ChangeMe_Agent_13",
                            ]): 
        return content
    else:
        client.logger.info(f"[hook:recv] reject 'from':{content.get('from')} | 'type':{content.get('type')}")


@client.upload_states()
async def upload_states(msg: Any) -> Any:
    # CHANGE vs Example 12:
    # - upload_states now supports the "listen" mode and returns a special state.
    global relations, outside_view
    print(msg)
    # NEW:
    # - Debug print of incoming messages (useful while developing the control flow).

    if listening:
        viz.push_states(["listen"])
        return "listen"
        # NEW behavior:
        # - While listening, the agent publishes only a single state "listen"
        #   instead of per-sender `to_me:` / `to_them:` states.
        # - This effectively pauses the relation machinery until activation.

    sender_id = msg.get("from")
    if not sender_id: return

    async with state_lock:
        relations.setdefault(sender_id, "register")
        outside_view.setdefault(sender_id, "neutral")

    view_states = {f"1:{k}": v for k,v in relations.items()}
    view_states.update({f"2:{k}": v for k,v in outside_view.items()})
    async with state_lock:
        viz.push_states(view_states)

    return { f"to_me:{sender_id}": relations[sender_id], f"to_them:{sender_id}": outside_view[sender_id] }
    # Same as Example 12 once listening is disabled.


@client.receive(route="listen --> register")
async def on_register(msg: Any) -> Optional[Event]:
    # NEW vs Example 12:
    # - Introduces an activation edge: listen -> register.
    # - The trigger is the control command "/travel".
    global listening
    print("listening!")
    # NEW:
    # - Debug print showing that this handler executed.

    if listening and msg ==  "/travel":
        await client.travel_to(host="187.77.102.80", port=8888)
        # What this does:
        # - Instructs the client to "travel" (reconnect/move) to the specified host/port.
        # - This is the external orchestration step: leave the local context and join the remote one.

        async with state_lock:
            listening = False
            # What this does:
            # - Switches the agent into active participation mode.

        return Move(Trigger.ok)
        # What this does:
        # - Requests the state transition listen -> register in the flow engine.


contact_list = []
@client.receive(route="register --> contact")
async def on_register(msg: Any) -> Optional[Event]:
    # Same as Example 12 (our-side classification):
    global contact_list
    if msg["message"] == "Hello": 
        async with state_lock:
            contact_list.append(msg["from"])
        return Move(Trigger.ok)

ban_list = []
@client.receive(route="register --> ban")
async def on_register(msg: Any) -> Optional[Event]:
    # Same as Example 12:
    global ban_list
    if msg["message"] == "I don't like you": 
        async with state_lock:
            ban_list.append(msg["from"])
        return Move(Trigger.ok)

friend_list = []
@client.receive(route="contact --> friend")
async def on_register(msg: Any) -> Optional[Event]:
    # Same as Example 12:
    global friend_list
    if msg["message"] == "I like you": 
        async with state_lock:
            friend_list.append(msg["from"])
        return Move(Trigger.ok)


to_them_list = []
@client.receive(route="neutral --> good")
async def on_register(msg: Any) -> Optional[Event]:
    # Same as Example 12 (their-side inference):
    global to_them_list
    if msg["message"] == "You are my contact": 
        async with state_lock:
            to_them_list.append({"to": msg["from"], "status": "good"})
        return Move(Trigger.ok)

@client.receive(route="neutral --> bad")
async def on_register(msg: Any) -> Optional[Event]: 
    global to_them_list
    if msg["message"] == "You are banned": 
        async with state_lock:
            to_them_list.append({"to": msg["from"], "status": "bad"})
        return Move(Trigger.ok)

@client.receive(route="good --> very_good")
async def on_register(msg: Any) -> Optional[Event]:
    global to_them_list
    if msg["message"] == "You are my friend": 
        async with state_lock:
            to_them_list.append({"to": msg["from"], "status": "good"})
        return Move(Trigger.ok)


@client.download_states()
async def download_states(possible_states: dict[str, list[Node]]) -> None:
    # CHANGE vs Example 12:
    # - download_states now respects listening mode and can accept list inputs.
    if listening:
        viz.push_states(["listen"])
        return
        # New behavior:
        # - When listening, ignore engine updates about relation state and keep UI in "listen".

    if isinstance(possible_states, list): possible_states = {"default": possible_states}
    # NEW:
    # - Defensive normalization: if the runtime sometimes sends a list instead of a dict,
    #   we wrap it so downstream code can still iterate a dict-like structure.
    # - This is a compatibility shim for mixed engine payload formats.

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
async def send_on_clock() -> Optional[str]:
    # CHANGE vs Example 12:
    # - Clock sender is now disabled while listening.
    async with state_lock:
        if listening:
            await asyncio.sleep(0.1)
            return
            # What this does:
            # - Prevents the agent from broadcasting "Hello" while it is still in listen mode.
            # - Also avoids a busy loop by sleeping briefly.
        viz.push_states(["clock"])
    await asyncio.sleep(3)
    return {"message": "Hello", "to": None}

@client.send(route="reputation", multi=True)
async def send_on_clock() -> list[str]:
    # CHANGE vs Example 12:
    # - Reputation sender is also disabled while listening.
    async with state_lock:
        if listening:
            await asyncio.sleep(0.1)
            return []
    await asyncio.sleep(3)
    async with state_lock:
        viz.push_states(["reputation"])
    def msg(string):
        if string == "good": return "I like you"
        if string == "bad": return "I don't like you"
    return [{"to": d["to"], "message": msg(d["status"])} for d in to_them_list]

@client.send(route="register --> contact", multi=True, on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_from_register_to_contact():
    # Unchanged vs Example 12:
    global contact_list
    try:
        return [{"to": contact_id, "message": "You are my contact"} for contact_id in contact_list]
    finally:
        async with state_lock:
            contact_list = []

@client.send(route="register --> ban", multi=True, on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_from_register_to_contact():
    # Unchanged vs Example 12:
    global ban_list
    try:
        return [{"to": banned_id, "message": "You are banned"} for banned_id in ban_list]
    finally:
        async with state_lock:
            ban_list = []

@client.send(route="contact --> friend", multi=True, on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_from_register_to_contact():
    # Unchanged vs Example 12:
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
    viz.push_states(["listen"])
    # CHANGE vs Example 12:
    # - Initial visualization state is now "listen", not "register".

    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")
    # CHANGE vs Example 12:
    # - Runs locally (127.0.0.1) at first, and only moves to the remote host/port
    #   after receiving the "/travel" command.
    #
    # RUNTIME BEHAVIOR (new overall effect in this step):
    # - The agent boots in a passive "listen" mode:
    #   - It does not broadcast.
    #   - It does not run reputation messaging.
    #   - It does not publish per-sender relation state.
    # - Receiving "/travel" (while listening) triggers:
    #   - client.travel_to(...) to join the remote session
    #   - listening=False
    #   - a state machine transition listen -> register
    # - After that, the full Example 12 social logic becomes active.
