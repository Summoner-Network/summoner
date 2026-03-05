import argparse, json, asyncio
from typing import Any, Optional

from summoner.client import SummonerClient
from summoner.protocol import Test, Event, Direction
from summoner.visionary import ClientFlowVisualizer


AGENT_ID = "ChangeMe_Agent_7"
viz = ClientFlowVisualizer(title=f"{AGENT_ID} Graph", port=8710)

client = SummonerClient(name=AGENT_ID)

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()


@client.hook(direction=Direction.RECEIVE, priority=0)
async def validate(msg: Any) -> Optional[dict]:
    # CHANGE vs Example 6:
    # - Same overall role (shape validation + normalization), but adds one new rule:
    #   recipient filtering via the "to" field.
    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg): return
    address: str = msg["remote_addr"]
    content: Any = msg["content"]
    if not isinstance(content, dict): return

    if content.get("to", "") not in [None, AGENT_ID]: return
    # NEW rule:
    # - Enforces simple addressing semantics:
    #   - accept broadcasts where content["to"] is None
    #   - accept direct messages where content["to"] equals this agent's AGENT_ID
    # - Any message addressed to some other agent is dropped here, before any state handler.

    if "from" not in content:
        client.logger.info(f"[hook:recv] missing content.from")
        return
    return content
    # Same effect as before:
    # - Downstream hooks/handlers receive only `content` (the application dict).

@client.hook(direction=Direction.RECEIVE, priority=1)
async def check_sender(content: dict) -> Optional[dict]:
    # CHANGE vs Example 6:
    # - Expands the allowlist to multiple agents.
    if content.get("from") in [
                                "ChangeMe_Agent_6",
                                "ChangeMe_Agent_7",
                            ]: 
        return content
    # What this does:
    # - Allows inbound messages from Agent_6 and Agent_7.
    # - This is now a minimal "two-agent world" allowlist, instead of only self.
    else:
        client.logger.info(f"[hook:recv] reject 'from':{content.get('from')} | 'type':{content.get('type')}")
        # Same as before:
        # - Non-allowlisted senders are logged and dropped.


state="register"

@client.upload_states()
async def upload_states(_: Any) -> list[str]:
    # Unchanged:
    viz.push_states([state])
    return state

@client.receive(route="register")
async def on_register(msg: Any) -> Event: 
    # Pipeline effect (now stricter than Example 6):
    # - A message reaches this handler only if:
    #   1) envelope shape is valid
    #   2) content is a dict
    #   3) content["to"] is None or equals AGENT_ID
    #   4) content["from"] is present
    #   5) content["from"] is in the allowlist [Agent_6, Agent_7]
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="contact")
async def on_contact(msg: Any) -> Event: 
    # Same pipeline constraints.
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="friend")
async def on_friend(msg: Any) -> Event: 
    # Same pipeline constraints.
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.receive(route="ban")
async def on_ban(msg: Any) -> Event: 
    # Same pipeline constraints.
    client.logger.info(msg)
    return Test(Trigger.ok)

@client.send(route="clock")
async def send_on_clock() -> str:
    # CHANGE vs Example 6:
    # - Outbound payload is now a structured dict instead of a bare string.
    viz.push_states(["clock"]) 
    await asyncio.sleep(3)
    return {"message": "Hello", "to": None}
    # What this does:
    # - "message": the text content to send
    # - "to": None indicates broadcast (compatible with the new receive-side "to" filter)
    # This makes addressing explicit and matches the validate() rule above.

@client.hook(direction=Direction.SEND)
async def sign(msg: Any) -> Optional[dict]:
    # Unchanged logic, but its effect is now more clearly visible:
    # - It will add "from": AGENT_ID to the outgoing dict.
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
    # - The agent now speaks a clearer "message protocol":
    #   outbound: {"message": "...", "to": None} then hook adds {"from": AGENT_ID}
    #   inbound: validate() enforces that "to" is either None (broadcast) or this agent
    # - Sender allowlist is expanded to two agents.
    # - Together, these changes establish the first coherent addressing + membership model.
