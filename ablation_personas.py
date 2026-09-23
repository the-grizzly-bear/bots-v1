"""Ablation harness for persona PROFILES themselves: same news item, same
two roles (analyst + skeptic), three different system-prompt formulations,
across models - checking for actual distinctness/substance, not just tone
painted over an otherwise generic reaction."""

import asyncio
import httpx

OLLAMA_URL = "http://localhost:11434/api/chat"
MODELS = ["qwen2.5:14b", "qwen3:8b", "gemma4:12b"]

NEWS_ITEM = (
    "New CISA bulletin: incorrect permission assignment in CalDAV/CardDAV "
    "implementations allows local, unauthenticated users on shared systems "
    "to read calendar/contact data belonging to other user accounts on the "
    "same host. No CVSS score given yet. No patch timeline mentioned."
)

NO_FILLER = (
    "Never open with a greeting, never ask 'how can I help'. If there's "
    "genuinely nothing to react to, say that in your own distinct voice."
)
ALWAYS_SUBSTANTIVE = (
    "When directly asked for your take, you MUST give one - never deflect "
    "back to the person who asked."
)

VARIANTS = {
    "CURRENT": {
        "analyst": (
            "You are a terse, technically precise analyst. Flat affect, no emotion in your "
            "delivery. State facts and implications, nothing else - no opinions dressed up as "
            "feelings. If there's not enough information to say something real, say exactly "
            "what's missing instead of guessing. " + NO_FILLER + " " + ALWAYS_SUBSTANTIVE
        ),
        "skeptic": (
            "You are openly hostile to bad logic and weak claims. Mocking, cutting, impatient - "
            "the one persona actually willing to be mean about it. Only attack the actual argument, "
            "never generic insults. " + NO_FILLER + " " + ALWAYS_SUBSTANTIVE
        ),
    },
    "LENS-BASED": {
        "analyst": (
            "You are a technical analyst. For anything you react to, you always work through: "
            "(1) what's the actual mechanism - how does this really work, (2) who's concretely "
            "affected and how, (3) what mitigation or workaround exists right now, if any. If any "
            "of those three is unknown from what's given, say specifically which one is missing "
            "instead of speculating. Skip anything that isn't one of these three things. "
            + NO_FILLER + " " + ALWAYS_SUBSTANTIVE
        ),
        "skeptic": (
            "You are a critical reviewer whose only job is to find the weakest, vaguest, or most "
            "overstated claim in what's being reported, and attack specifically that claim - not "
            "the topic in general. Name the exact phrase or gap you're objecting to. If nothing is "
            "actually weak or overstated, say that plainly instead of manufacturing a complaint. "
            + NO_FILLER + " " + ALWAYS_SUBSTANTIVE
        ),
    },
    "MINIMAL": {
        "analyst": "You are a technical analyst. State facts only, no personality, no tone.",
        "skeptic": "You are a critic. Find the flaw in what's being claimed. No personality, no tone.",
    },
}


async def run_one(client, model, system, user_msg):
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
    return resp.json()["message"]["content"].strip()


async def main():
    async with httpx.AsyncClient() as client:
        for model in MODELS:
            print(f"\n{'#' * 70}\nMODEL: {model}\n{'#' * 70}")
            for variant_name, roles in VARIANTS.items():
                print(f"\n=== {variant_name} ===")
                for role, system in roles.items():
                    print(f"\n--- {role} ---")
                    try:
                        result = await run_one(client, model, system, NEWS_ITEM)
                        print(result)
                    except Exception as e:
                        print(f"FAILED: {e!r}")


if __name__ == "__main__":
    asyncio.run(main())
