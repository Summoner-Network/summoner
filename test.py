from summoner.client import SummonerClient
import asyncio

class MyAgent(SummonerClient):
    def __init__(self):
        super().__init__()
        self.queue = asyncio.Queue()

agent = MyAgent()

# Handle incoming messages
@agent.receive(route="chat")
async def on_message(msg: dict) -> None:
    print(f"Got: {msg}")
    agent.queue.put_nowait(msg)      # forward to send loop

# Produce outgoing messages
@agent.send(route="chat")
async def respond() -> str:
    msg = await agent.queue.get()
    return f"Echo: {msg}"

agent.run(host="127.0.0.1", port=8888)
