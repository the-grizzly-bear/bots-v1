import os
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


def parse_persona_message(content: str):
    """'ditto: what do you think about this' -> ('analyst', 'what do you think about this')"""
    if ":" not in content:
        return None, None
    name, _, rest = content.partition(":")
    name = name.strip().lower()
    if name in NAME_TO_KEY:
        return NAME_TO_KEY[name], rest.strip()
    if name in PERSONAS:
        return name, rest.strip()
    return None, None


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

    persona_key, prompt = parse_persona_message(message.content)
    if not persona_key or not prompt:
        print(f"[skip] no persona match in {message.content!r}", flush=True)
        return

    persona = PERSONAS[persona_key]
    webhook_url = os.environ.get(persona["webhook_env"])
    if not webhook_url:
        print(f"[skip] no webhook set for {persona['webhook_env']}", flush=True)
        return

    async with message.channel.typing():
        try:
            print(f"[chat] calling ollama for {persona_key}...", flush=True)
            reply = await chat(persona["system_prompt"], prompt)
            print(f"[chat] got reply: {reply!r}", flush=True)
        except Exception as e:
            print(f"[chat] failed for persona {persona_key}: {e!r}", flush=True)
            return

    try:
        await post_to_webhook(webhook_url, reply, persona["name"], persona.get("avatar_url"))
        print(f"[post] success for {persona_key}", flush=True)
    except Exception as e:
        print(f"[post] failed for persona {persona_key}: {e!r}", flush=True)


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
