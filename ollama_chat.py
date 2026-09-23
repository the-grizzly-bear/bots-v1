import os
import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")

MAX_TOOL_ROUNDS = 3


async def chat(system_prompt: str, user_message: str, tools=None, tool_executor=None) -> str:
    """If `tools` (Ollama/OpenAI-style tool schemas) and `tool_executor`
    (async fn(name, args) -> str) are given, the model can call tools and get
    real results fed back before giving its final answer - instead of us
    guessing intent from the raw text."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
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
