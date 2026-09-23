import asyncio
import os
import re
from dotenv import load_dotenv

load_dotenv()

import discord

from personas import PERSONAS
from ollama_chat import chat
from poster import post_to_webhook

DISCORD_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
INTERACTIVE_CHANNEL_ID = int(os.environ["INTERACTIVE_CHANNEL_ID"])

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


NAME_TO_KEY = {p["name"].lower(): key for key, p in PERSONAS.items()}


def _lookup(name: str):
    name = name.strip().lower()
    if name in NAME_TO_KEY:
        return NAME_TO_KEY[name]
    if name in PERSONAS:
        return name
    return None


def parse_persona_message(content: str):
    """If the message starts with one or more known persona names (comma
    and/or space separated, '@' optional), those become FORCED responders
    and the rest is the prompt. Otherwise nobody is forced - the whole
    message is the prompt, and any persona can still choose to chime in.

    'ditto, meowth: hello' -> (['analyst', 'maverick'], 'hello')
    'hello everyone'       -> ([], 'hello everyone')
    """
    content = content.strip()
    remaining = content
    keys = []

    while remaining:
        token = remaining
        if token.startswith("@"):
            token = token[1:]

        # peel off one name, separated by ':', ',', or whitespace
        m = re.match(r"\s*([A-Za-z0-9_]+)\s*[:,]?\s*", token)
        if not m:
            break
        key = _lookup(m.group(1))
        if not key:
            break
        keys.append(key)
        remaining = token[m.end():]

    if keys and remaining.strip():
        return keys, remaining.strip()
    return [], content


@client.event
async def on_ready():
    print(f"Logged in as {client.user} (bots-v1), watching channel {INTERACTIVE_CHANNEL_ID}", flush=True)


@client.event
async def on_message(message: discord.Message):
    print(f"[on_message] channel={message.channel.id} author={message.author} bot={message.author.bot} content={message.content!r}", flush=True)
    if message.author.bot:
        return
    if message.channel.id != INTERACTIVE_CHANNEL_ID:
        print(f"[skip] wrong channel: got {message.channel.id}, want {INTERACTIVE_CHANNEL_ID}", flush=True)
        return

    if not message.content.strip():
        return

    forced_keys, prompt = parse_persona_message(message.content)
    await run_discussion(forced_keys, prompt, message)


MAX_TURNS = 6
NAME_RE = re.compile(r"[A-Za-z0-9_]+")


def find_mentioned_personas(text: str, exclude: set):
    found = []
    for word in NAME_RE.findall(text.lower()):
        key = NAME_TO_KEY.get(word)
        if key and key not in exclude and key not in found:
            found.append(key)
    return found


PASS_WORD = "PASS"
WATCH_NOTE = (
    "\n\nYou're watching this Discord channel, not directly addressed. Only "
    "reply if you genuinely have something worth adding from your own "
    f"perspective. If this isn't relevant to you, reply with exactly the "
    f"single word {PASS_WORD} and nothing else - don't force it."
)
MENTIONED_NOTE = (
    "\n\nYou were just mentioned by name in this conversation. If you have "
    "a real reaction, say so in your own voice. If not, reply with exactly "
    f"the single word {PASS_WORD} and nothing else."
)


async def run_discussion(forced_keys: list, prompt: str, message: discord.Message):
    """Forced (explicitly-named) personas always answer the prompt. Every
    other persona independently decides whether to chime in (PASS_WORD lets
    them opt out) - nobody needs to be asked by name to participate. If any
    reply mentions another known persona, that persona gets a chance to react
    too. Capped at MAX_TURNS total replies so nothing runs forever."""
    forced_keys = list(dict.fromkeys(forced_keys))  # de-dup, keep order
    optional_keys = [k for k in PERSONAS if k not in forced_keys]
    spoken = set(forced_keys)

    forced_replies, optional_replies = await asyncio.gather(
        asyncio.gather(*(respond_as(k, prompt, message) for k in forced_keys)),
        asyncio.gather(*(get_reply(k, prompt + WATCH_NOTE) for k in optional_keys)),
    )

    transcript_lines = [f"User: {prompt}"]
    queue = []

    for key, reply in zip(forced_keys, forced_replies):
        if reply is None:
            continue
        transcript_lines.append(f"{PERSONAS[key]['name']}: {reply}")
        for mentioned_key in find_mentioned_personas(reply, exclude=spoken):
            if mentioned_key not in queue:
                queue.append(mentioned_key)

    for key, reply in zip(optional_keys, optional_replies):
        if not reply or reply.strip().upper() == PASS_WORD:
            continue
        spoken.add(key)
        if not await post_reply(key, reply, message):
            continue
        transcript_lines.append(f"{PERSONAS[key]['name']}: {reply}")
        for mentioned_key in find_mentioned_personas(reply, exclude=spoken):
            if mentioned_key not in queue:
                queue.append(mentioned_key)

    while queue and len(spoken) < MAX_TURNS:
        persona_key = queue.pop(0)
        if persona_key in spoken:
            continue
        spoken.add(persona_key)

        transcript = "\n".join(transcript_lines) + MENTIONED_NOTE
        reply = await get_reply(persona_key, transcript)
        if not reply or reply.strip().upper() == PASS_WORD:
            continue
        if not await post_reply(persona_key, reply, message):
            continue

        transcript_lines.append(f"{PERSONAS[persona_key]['name']}: {reply}")
        for mentioned_key in find_mentioned_personas(reply, exclude=spoken):
            if mentioned_key not in queue:
                queue.append(mentioned_key)


OTHER_PERSONAS_NOTE = (
    "You're in a group chat alongside other personas ({names}). "
    "You can address them by name if you're reacting to something one of them said. "
    "Stay in your own voice - don't speak for them."
)


def system_prompt_for(persona_key: str) -> str:
    persona = PERSONAS[persona_key]
    others = ", ".join(p["name"] for k, p in PERSONAS.items() if k != persona_key)
    return persona["system_prompt"] + "\n\n" + OTHER_PERSONAS_NOTE.format(names=others)


async def get_reply(persona_key: str, prompt: str):
    try:
        print(f"[chat] calling ollama for {persona_key}...", flush=True)
        reply = await chat(system_prompt_for(persona_key), prompt)
        print(f"[chat] got reply: {reply!r}", flush=True)
        return reply
    except Exception as e:
        print(f"[chat] failed for persona {persona_key}: {e!r}", flush=True)
        return None


async def post_reply(persona_key: str, reply: str, message: discord.Message):
    persona = PERSONAS[persona_key]
    webhook_url = os.environ.get(persona["webhook_env"])
    if not webhook_url:
        print(f"[skip] no webhook set for {persona['webhook_env']}", flush=True)
        return False
    try:
        await post_to_webhook(webhook_url, reply, persona["name"], persona.get("avatar_url"))
        print(f"[post] success for {persona_key}", flush=True)
        return True
    except Exception as e:
        print(f"[post] failed for persona {persona_key}: {e!r}", flush=True)
        return False


async def respond_as(persona_key: str, prompt: str, message: discord.Message):
    async with message.channel.typing():
        reply = await get_reply(persona_key, prompt)
    if reply is None:
        return None
    ok = await post_reply(persona_key, reply, message)
    return reply if ok else None


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
