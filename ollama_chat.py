import os
import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")


async def chat(system_prompt: str, user_message: str) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "messages": messages, "stream": False},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "").strip()
