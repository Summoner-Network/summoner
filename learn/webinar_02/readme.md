# Webinar 02: Build a Simple Responding Agent

This webinar teaches a responder agent in small steps, similar to webinar 01.

## Goal

Start from a minimal sender and end with a signed, memory-aware LLM responder.

## Run order

1. `example_1`: minimal send loop (every 2 seconds)
2. `example_2`: add receive + echo (global message variable)
3. `example_3`: add GPT response using curl tools (simple)
4. `example_4`: add model switch (OpenAI / Claude / OpenClaw)
5. `example_5`: add `id.json` and send hook signing (`from`)
6. `example_6`: only accept signed messages and keep per-id context

## Typical local setup

Terminal 1:

```bash
python server.py
```

Terminal 2:

```bash
python agents/agent_InputAgent/agent.py
```

Terminal 3 (example):

```bash
python learn/webinar_02/example_1/agent.py
```

For steps 3-6, create `.env` with keys as needed:

```bash
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
```

Step 4+ OpenClaw option:

```bash
python learn/webinar_02/example_4/agent.py --model openclaw --openclaw-agent YourAgentName
```
