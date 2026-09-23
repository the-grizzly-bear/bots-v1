import os
import discord
from dotenv import load_dotenv

from personas import PERSONAS
from ollama_chat import chat
from poster import post_to_webhook

load_dotenv()

DISCORD_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
INTERACTIVE_CHANNEL_ID = int(os.environ["INTERACTIVE_CHANNEL_ID"])

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


def parse_persona_message(content: str):
    """'analyst: what do you think about this' -> ('analyst', 'what do you think about this')"""
    if ":" not in content:
        return None, None
    key, _, rest = content.partition(":")
    key = key.strip().lower()
    if key in PERSONAS:
        return key, rest.strip()
    return None, None


@client.event
async def on_ready():
    print(f"Logged in as {client.user} (bots-v1)")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.id != INTERACTIVE_CHANNEL_ID:
        return

    persona_key, prompt = parse_persona_message(message.content)
    if not persona_key or not prompt:
        return

    persona = PERSONAS[persona_key]
    webhook_url = os.environ.get(persona["webhook_env"])
    if not webhook_url:
        print(f"[skip] no webhook set for {persona['webhook_env']}")
        return

    async with message.channel.typing():
        try:
            reply = await chat(persona["system_prompt"], prompt)
        except Exception as e:
            print(f"[chat] failed for persona {persona_key}: {e}")
            return

    try:
        await post_to_webhook(webhook_url, reply, persona["name"], persona.get("avatar_url"))
    except Exception as e:
        print(f"[post] failed for persona {persona_key}: {e}")


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
