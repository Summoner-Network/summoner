import argparse, json, asyncio
from typing import Any, Optional

from summoner.client import SummonerClient
from summoner.protocol import Test, Event, Direction
from summoner.visionary import ClientFlowVisualizer


AGENT_ID = "ChangeMe_Agent_5"
viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=8710)

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()


@client.hook(direction=Direction.RECEIVE)
async def validate(msg: Any) -> Optional[dict]:
    # NEW vs Example 4:
    # - Introduces the first RECEIVE hook, which intercepts inbound messages
    #   before they reach any @client.receive(route=...) handler.
    # - The point is to validate / normalize inbound payload shape, and optionally
    #   reject messages early by returning None.
    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg): return
    # What this does:
    # - Requires the framework-level envelope to be a dict that contains:
    #   - "remote_addr": where it came from (transport-level metadata)
    #   - "content": the application-level payload
    # - If the envelope does not match this shape, the hook drops it.

    address: str = msg["remote_addr"]
    # What this does (currently):
    # - Extracts the sender address from the transport envelope.
    # - In this step it is not used, but it establishes a pattern:
    #   later steps can use it for filtering, logging, reputation, etc.

    content: Any = msg["content"]
    if not isinstance(content, dict): return
    # What this does:
    # - Requires the application payload to be a dict.
    # - This enforces a structured message format on receive.

    if "from" not in content:
        client.logger.info(f"[hook:recv] missing content.from")
        return
    # What this does:
    # - Enforces that inbound content identifies a sender (the "from" field).
    # - If missing, it logs a reason and rejects the message.

    return content
    # What this does:
    # - Returns the normalized "content" dict.
    # - IMPORTANT: by returning `content` (instead of the original envelope),
    #   all downstream receive handlers (`on_register`, etc.) now see only the
    #   application payload dict, not the transport wrapper.


state="register"

@client.upload_states()
async def upload_states(_: Any) -> list[str]:
    # Unchanged vs Example 4:
    viz.push_states([state])
    return state

@client.receive(route="register")
async def on_register(msg: Any) -> Event: 
    # Behavior change vs Example 4 (even though code is unchanged):
    # - Previously, `msg` could be whatever the runtime delivered.
    # - Now, because of the RECEIVE hook, `msg` here will typically be the validated
    #   `content` dict (with a guaranteed "from" field).
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="contact")
async def on_contact(msg: Any) -> Event: 
    # Same as above:
    # - Receives normalized dict payloads (assuming validate() passed).
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="friend")
async def on_friend(msg: Any) -> Event: 
    # Same as above.
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="ban")
async def on_ban(msg: Any) -> Event: 
    # Same as above.
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.send(route="clock")
async def send_on_clock() -> str: 
    # Unchanged vs Example 4:
    viz.push_states(["clock"])
    await asyncio.sleep(3)
    return "hello"

@client.hook(direction=Direction.SEND)
async def sign(msg: Any) -> Optional[dict]:
    # Unchanged vs Example 4:
    # - Outbound normalization + stamping "from".
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
    # - Inbound messages are now gated by `validate()`:
    #   - they must have a transport envelope with remote_addr + content
    #   - content must be a dict
    #   - content must contain "from"
    # - Only validated content dicts reach the state handlers.
    # - This is the first step that implements "basic protocol hygiene" on receive.
