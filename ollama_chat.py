import asyncio
import os
import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")

MAX_TOOL_ROUNDS = 3

# Ollama serves one local model - it can't actually run dozens of requests in
# parallel no matter how many we throw at it. Without a limit, a burst (e.g.
# many news items arriving close together) fires off every persona's call at
# once; most of them sit queued on Ollama's side while their own client-side
# timeout clock is already running, so they time out before ever really being
# worked on. Capping client-side concurrency means a request's timeout only
# starts once it's actually likely to be serviced soon - a burst then queues
# gracefully instead of mass-timing-out.
_OLLAMA_CONCURRENCY = asyncio.Semaphore(2)


async def chat(system_prompt: str, user_message: str, tools=None, tool_executor=None) -> str:
    """If `tools` (Ollama/OpenAI-style tool schemas) and `tool_executor`
    (async fn(name, args) -> str) are given, the model can call tools and get
    real results fed back before giving its final answer - instead of us
    guessing intent from the raw text."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    async with _OLLAMA_CONCURRENCY:
        async with httpx.AsyncClient(timeout=180) as client:
            for _ in range(MAX_TOOL_ROUNDS):
                payload = {"model": OLLAMA_MODEL, "messages": messages, "stream": False}
                if tools:
                    payload["tools"] = tools
                resp = await client.post(OLLAMA_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()
                message = data.get("message", {})

                tool_calls = message.get("tool_calls")
                if tool_calls and tool_executor:
                    messages.append(message)
                    for call in tool_calls:
                        fn = call["function"]["name"]
                        args = call["function"].get("arguments", {})
                        result = await tool_executor(fn, args)
                        messages.append({"role": "tool", "content": str(result)})
                    continue

                return (message.get("content") or "").strip()
    return ""
