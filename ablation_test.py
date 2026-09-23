"""Ablation harness: run the same synthesis prompt against fixed real
transcript data, across multiple models and with/without a persona voice
tied to it, so we can compare actual output quality instead of guessing via
live Discord trial and error."""

import asyncio
import httpx

OLLAMA_URL = "http://localhost:11434/api/chat"
MODELS = ["qwen2.5:14b", "gemma4:12b", "deepseek-r1:8b", "qwen3:8b"]

# Real transcript pulled from an actual run (RabbitMQ/CalDAV/SharpHound batch)
TRANSCRIPT = """User: New post just now in #news from CISA Bulletins: High vulnerabilities identified this week: incorrect permission assignment in CalDAV/CardDAV allowing local users to access sensitive information from other accounts.
Astrid: Incorrect permission assignment in CalDAV/CardDAV systems can lead to local users accessing sensitive information from other accounts, highlighting the importance of proper access control mechanisms.
Haley: Incorrect permission assignment? Sounds like someone's been playing fast and loose with security settings. Time to tighten up those controls, folks.
Sophia: This vulnerability underscores the need for robust security configurations to prevent sensitive data leaks.
Sara: How common are these types of vulnerabilities in calendar and contact management systems?
Aurora: Incorrect permission assignments are always low-hanging fruit for attackers. Expect more exploits leveraging this flaw.
Luna: Sounds like a potential privacy risk if not patched promptly.
Astrid: RabbitMQ administrators should be aware of the potential for atom exhaustion due to improper handling of metadata, which could be exploited to cause denial of service.
Haley: "Admin-only atom exhaustion" sounds like something from a sci-fi novel. RabbitMQ admins should keep an eye on this.
Sara: The RabbitMQ vulnerability seems serious given it involves admin-only functions, but the term 'atom exhaustion' is vague. How exactly does 'atomize_keys' on vhost metadata lead to security issues?
Haley: Sara, you're spot-on with your skepticism about the term 'atom exhaustion.' It's like they're throwing around fancy terminology without explaining the actual mechanics.
Astrid: The term 'atomize_keys' refers to converting keys into atoms within RabbitMQ's vhost metadata, which can lead to resource exhaustion if not properly managed - a finite Erlang resource.
Sara: A commit fixing a typo in SharpHound docs changed "CollectionMethod" to "CollectionMethods" - why would a typo fix change singular to plural? Something seems off here.
Haley: Sara raises a valid point. A singular typo correction usually wouldn't change parameter names from singular to plural.
Astrid: Sara raises a valid point. The change from singular to plural suggests a more substantial update than a mere typo fix would imply.
Sophia: Sara raises a valid point. This implies a broader functional change rather than a simple correction."""

PASS_WORD = "PASS"

SYNTHESIS_NOTE_TIED = (
    "\n\nYou are Luna. The discussion above covered some ground, possibly "
    "including disagreement between people. Distill it into a short bulleted "
    "wrap-up - one bullet per distinct point or unresolved disagreement, "
    "substance only, not a recap of who said what. This is the natural close "
    "of the thread, so actually land on where things ended up if there's a "
    f"clear answer. If a wrap-up would add nothing, reply exactly {PASS_WORD}."
)

SYNTHESIS_NOTE_NEUTRAL = (
    "\n\nThe discussion above covered some ground, possibly including "
    "disagreement between people. Distill it into a short bulleted wrap-up - "
    "one bullet per distinct point or unresolved disagreement, substance "
    "only, not a recap of who said what. This is the natural close of the "
    "thread, so actually land on where things ended up if there's a clear "
    f"answer. If a wrap-up would add nothing, reply exactly {PASS_WORD}."
)

SYSTEM_TIED = (
    "You're a beat behind everyone else, and when you catch up you cut "
    "straight to the actual bottom line - TL;DR, BLUF, one or two sentences, "
    "no restating what was already said."
)
SYSTEM_NEUTRAL = (
    "You are a neutral summarizer. Terse, objective, no personality, no "
    "opinion of your own - just distill."
)


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
            print(f"\n{'=' * 70}\nMODEL: {model}\n{'=' * 70}")
            for label, system, note in [
                ("PERSONA-TIED (Luna/scribe)", SYSTEM_TIED, SYNTHESIS_NOTE_TIED),
                ("NEUTRAL (unvoiced)", SYSTEM_NEUTRAL, SYNTHESIS_NOTE_NEUTRAL),
            ]:
                print(f"\n--- {label} ---")
                try:
                    result = await run_one(client, model, system, TRANSCRIPT + note)
                    print(result)
                except Exception as e:
                    print(f"FAILED: {e!r}")


if __name__ == "__main__":
    asyncio.run(main())
