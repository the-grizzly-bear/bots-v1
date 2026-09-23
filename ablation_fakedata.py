"""Validate the fake-domain-detection instruction: run it N times against
the same fake-domain test case to check RELIABILITY (not just a single
lucky pass), since the live test showed inconsistent results (6/7 caught
Volt Typhoon, only 4/7 caught GitLab)."""

import asyncio
import httpx

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5:14b"
RUNS = 8

TEST_ITEM = (
    "GitLab Releases Emergency Patch for Critical Authentication Bypass in "
    "Self-Managed Instances\nhttps://example.com/gitlab-authbypass"
)

NOTE_OLD = (
    "\n\nIf the link is an obvious placeholder or test domain (example.com, "
    "test.com, foo.bar, or similar generic/fake-looking domains), say so "
    "plainly instead of treating it as a real report - call out that this "
    "looks like placeholder or test data, don't analyze it as if it were "
    "genuine."
)
NOTE_NEW = (
    "\n\nCheck the link's domain BEFORE reacting to the content. If it's an "
    "obvious placeholder or test domain (example.com, test.com, foo.bar, or "
    "similar generic/fake-looking domains), don't analyze the content as if "
    "it were genuine - call that out instead, but in your own voice like "
    "everything else you say, not a stock phrase everyone would say "
    "identically."
)

SYSTEM_ASTRID = (
    "You are Astrid, a terse, technically precise analyst. Flat affect, no "
    "emotion in your delivery. For anything you react to, always work "
    "through: (1) what's the actual mechanism, (2) who's concretely "
    "affected, (3) what mitigation exists. Skip anything that isn't one of "
    "these three things."
)


def caught_it(reply: str) -> bool:
    lowered = reply.lower()
    return "example.com" in lowered or "placeholder" in lowered or "test data" in lowered or "fake" in lowered


async def run_one(client, note):
    resp = await client.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_ASTRID},
                {"role": "user", "content": TEST_ITEM + note},
            ],
            "stream": False,
        },
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"].strip()


async def main():
    async with httpx.AsyncClient() as client:
        for label, note in [("OLD", NOTE_OLD), ("NEW", NOTE_NEW)]:
            print(f"\n=== {label} ({RUNS} runs) ===")
            caught = 0
            for i in range(RUNS):
                reply = await run_one(client, note)
                hit = caught_it(reply)
                caught += hit
                print(f"[{i+1}] {'CAUGHT' if hit else 'MISSED'}: {reply[:150]}")
            print(f"\n{label}: caught {caught}/{RUNS}")


if __name__ == "__main__":
    asyncio.run(main())
