import argparse, json, asyncio
from typing import Any, Optional

from summoner.client import SummonerClient
from summoner.protocol import Test, Move, Stay, Event, Direction, Node
from summoner.visionary import ClientFlowVisualizer

state_lock = asyncio.Lock()
# NEW vs Example 8:
# - Introduces an asyncio lock used to protect reads/writes to shared state.
# - This matters because multiple async callbacks can run concurrently:
#   - send loop ("clock")
#   - download_states callback (updates state)
#   - receive handlers
# Without a lock, state could be read mid-update or overwritten in inconsistent ways.

AGENT_ID = "ChangeMe_Agent_9"
viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=8710)

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()


@client.hook(direction=Direction.RECEIVE, priority=0)
async def validate(msg: Any) -> Optional[dict]:
    # Unchanged vs Example 8:
    # - Envelope validation, addressing filter, "from" required.
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
    # CHANGE vs Example 8:
    # - Expands allowlist again: now includes Agent_9.
    if content.get("from") in [
                                "ChangeMe_Agent_6",
                                "ChangeMe_Agent_7",
                                "ChangeMe_Agent_8",
                                "ChangeMe_Agent_9",
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
    # Unchanged edge rule vs Example 8:
    if msg["message"] == "Hello": 
        return Move(Trigger.ok)

@client.receive(route="register --> ban")
async def on_register(msg: Any) -> Optional[Event]: 
    # Unchanged edge rule vs Example 8:
    if msg["message"] == "I don't like you": 
        return Move(Trigger.ok)

@client.receive(route="contact --> friend")
async def on_register(msg: Any) -> Optional[Event]: 
    # Unchanged edge rule vs Example 8:
    if msg["message"] == "I like you": 
        return Move(Trigger.ok)


@client.download_states()
async def download_states(possible_states: list[Node]) -> None:
    # CHANGE vs Example 8:
    # - This callback now *updates the agent's local `state` variable* based on the
    #   engine's reported possible/active states.
    # - In Example 8, it only forwarded possible_states to the visualizer.
    global state
    states = [s for s in possible_states if str(s) != str(state)]
    # What this does:
    # - Filters the incoming list to states that are different from the current state.
    # - `str(...)` comparisons suggest that Node might not be directly comparable,
    #   so the code uses string representations as a stable comparison key.
    if states:
        async with state_lock:
            state = states[0]
        # What this does:
        # - If there is at least one different state, we adopt the first one.
        # - We use the lock so that any concurrent sender or visual update doesn't
        #   see a half-updated value.
    viz.push_states([state])
    # What this does:
    # - The visualizer now reflects the *current chosen state* (single value),
    #   rather than the whole "possible states" set.

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
    # CHANGE vs Example 8:
    # - Adds locking around viz updates.
    # - This coordinates with download_states which also touches state/viz.
    async with state_lock:
        viz.push_states(["clock"])
        # What this does:
        # - Ensures we don't interleave a "clock" visual update with a concurrent
        #   state update in download_states.
    await asyncio.sleep(3)
    return {"message": "Hello", "to": None}
    # Same outbound message as Example 8.

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
    # - The agent now maintains a local notion of "current state" that can be updated
    #   by the engine via download_states().
    # - This makes `state` more than a constant: it becomes a synchronized mirror of
    #   the flow engine's belief about the agent.
    # - The lock is the first concurrency-control mechanism introduced, preparing for
    #   more complex shared structures later (relations, lists, dashboards, etc.).
