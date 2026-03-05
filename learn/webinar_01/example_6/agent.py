import argparse, json, asyncio
from typing import Any, Optional

from summoner.client import SummonerClient
from summoner.protocol import Test, Event, Direction
from summoner.visionary import ClientFlowVisualizer


AGENT_ID = "ChangeMe_Agent_6"
viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=8710)

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()


@client.hook(direction=Direction.RECEIVE, priority=0)
async def validate(msg: Any) -> Optional[dict]:
    # CHANGE vs Example 5:
    # - Same validation logic as before, but now explicitly sets `priority=0`.
    # - This matters because we are introducing a *second* RECEIVE hook below.
    # - Priority defines execution order: lower numbers run earlier.
    # - So `validate()` runs first and normalizes the input for later hooks.
    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg): return
    address: str = msg["remote_addr"]
    content: Any = msg["content"]
    if not isinstance(content, dict): return
    if "from" not in content:
        client.logger.info(f"[hook:recv] missing content.from")
        return
    return content
    # Effect:
    # - Downstream hooks and handlers now receive `content` (the dict payload),
    #   not the transport envelope.

@client.hook(direction=Direction.RECEIVE, priority=1)
async def check_sender(content: dict) -> Optional[dict]:
    # NEW vs Example 5:
    # - Adds a second stage in the inbound pipeline: sender filtering.
    # - Because it has `priority=1`, it runs *after* validate() (priority=0).
    # - Input type is now `content: dict` (the normalized payload returned by validate()).
    if content.get("from") in ["ChangeMe_Agent_6"]: return content
    # What this does:
    # - Implements an allowlist of senders.
    # - In this example, it only accepts messages where content["from"] is exactly
    #   "ChangeMe_Agent_6".
    # - This is effectively "accept only messages from myself" (useful as a minimal
    #   test that filtering works).
    else:
        client.logger.info(f"[hook:recv] reject 'from':{content.get('from')} | 'type':{content.get('type')}")
        # What this does:
        # - Logs a rejection reason when a message is not from an allowed sender.
        # - Returns None implicitly, which drops the message and prevents it from
        #   reaching any @client.receive(route=...) handler.


state="register"

@client.upload_states()
async def upload_states(_: Any) -> list[str]:
    # Unchanged:
    viz.push_states([state])
    return state

@client.receive(route="register")
async def on_register(msg: Any) -> Event: 
    # Behavior change (pipeline effect):
    # - This handler now only sees messages that passed BOTH hooks:
    #   1) validate() shape checks
    #   2) check_sender() allowlist
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="contact")
async def on_contact(msg: Any) -> Event: 
    # Same pipeline constraints as above.
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="friend")
async def on_friend(msg: Any) -> Event: 
    # Same pipeline constraints as above.
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="ban")
async def on_ban(msg: Any) -> Event: 
    # Same pipeline constraints as above.
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.send(route="clock")
async def send_on_clock() -> str:
    # Unchanged vs Example 5:
    viz.push_states(["clock"]) 
    await asyncio.sleep(3)
    return "hello"

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
    # - The inbound path now has a two-stage filter:
    #   (priority 0) validate: enforces envelope + content dict + "from"
    #   (priority 1) check_sender: enforces an allowlist on content["from"]
    # - Only messages from "ChangeMe_Agent_6" reach the receive handlers.
    # - This is the first step introducing "policy" on inbound messages, not just shape.
