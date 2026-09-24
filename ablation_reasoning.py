"""Ablation: does adding a reasoning step improve response quality?
Compares (1) current baseline single-shot, (2) prompted chain-of-thought on
the SAME reliable model, (3) and (4) models with native 'thinking' support
(Ollama returns it as a separate field, already ignored by our chat()).
Reuses the CalDAV test case from the earlier persona ablation, since qwen3
already caught a real logical inconsistency in it there - good signal for
whether reasoning depth actually changes the outcome."""

import asyncio
import time
import httpx

OLLAMA_URL = "http://localhost:11434/api/chat"
RUNS = 4

NEWS_ITEM = (
    "New CISA bulletin: incorrect permission assignment in CalDAV/CardDAV "
    "implementations allows local, unauthenticated users on shared systems "
    "to read calendar/contact data belonging to other user accounts on the "
    "same host. No CVSS score given yet. No patch timeline mentioned."
)

SYSTEM_SKEPTIC = (
    "You are openly hostile to bad logic and weak claims. Mocking, cutting, "
    "impatient. But your mockery always lands on one specific thing: find "
    "the weakest, vaguest, or most overstated claim in what's being "
    "reported, and attack exactly that - name the specific phrase or gap "
    "you're objecting to, don't just mock the situation in general."
)

COT_SUFFIX = (
    "\n\nBefore answering, briefly think through what's actually being "
    "claimed and whether it holds up logically. Then give ONLY your final "
    "reaction as your answer - don't show the thinking itself."
)


async def call(client, model, system, user_msg):
    t0 = time.time()
    resp = await client.post(
        OLLAMA_URL,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            "stream": False,
        },
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()
    elapsed = time.time() - t0
    content = data["message"].get("content", "").strip()
    thinking = data["message"].get("thinking")
    return content, thinking, elapsed


async def main():
    async with httpx.AsyncClient() as client:
        configs = [
            ("BASELINE qwen2.5:14b (no reasoning)", "qwen2.5:14b", NEWS_ITEM),
            ("PROMPTED-COT qwen2.5:14b", "qwen2.5:14b", NEWS_ITEM + COT_SUFFIX),
            ("NATIVE-THINKING qwen3:8b", "qwen3:8b", NEWS_ITEM),
            ("NATIVE-THINKING deepseek-r1:8b", "deepseek-r1:8b", NEWS_ITEM),
        ]
        for label, model, prompt in configs:
            print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
            for i in range(RUNS):
                content, thinking, elapsed = await call(client, model, SYSTEM_SKEPTIC, prompt)
                print(f"\n[run {i+1}] ({elapsed:.1f}s{'  +thinking' if thinking else ''})")
                print(content)


if __name__ == "__main__":
    asyncio.run(main())
