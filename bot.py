import asyncio
import os
import re
from dotenv import load_dotenv

load_dotenv()

import discord

from personas import PERSONAS
from ollama_chat import chat
from poster import post_to_webhook
from memory import remember, recent_context, seconds_since_last_ping, record_ping

DISCORD_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
INTERACTIVE_CHANNEL_ID = int(os.environ["INTERACTIVE_CHANNEL_ID"])
WATCHED_CHANNEL_NAMES = {
    c.strip().lower() for c in os.environ.get("WATCHED_CHANNELS", "").split(",") if c.strip()
}
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


NAME_TO_KEY = {p["name"].lower(): key for key, p in PERSONAS.items()}

NAME_RE = re.compile(r"[A-Za-z0-9_]+")

CHANNEL_HISTORY_LIMIT = 15
channel_name_to_obj = {}  # populated on_ready: 'unu_other' -> discord.TextChannel
watched_channel_ids = set()  # populated on_ready
interactive_channel = None  # populated on_ready


def format_message_content(msg: discord.Message) -> str:
    parts = []
    if msg.content:
        parts.append(msg.content)
    for embed in msg.embeds:
        if embed.title:
            parts.append(f"[{embed.title}]")
        if embed.description:
            parts.append(embed.description)
        for field in embed.fields:
            parts.append(f"{field.name}: {field.value}")
    return " | ".join(parts)


def find_mentioned_personas(text: str, exclude: set):
    found = []
    for word in NAME_RE.findall(text.lower()):
        key = NAME_TO_KEY.get(word)
        if key and key not in exclude and key not in found:
            found.append(key)
    return found


async def fetch_channel_context(channel: discord.TextChannel) -> str:
    lines = []
    async for msg in channel.history(limit=CHANNEL_HISTORY_LIMIT):
        text = format_message_content(msg) or "[empty message]"
        lines.append(f"{msg.author.name}: {text}")
    lines.reverse()  # oldest first
    return f"--- recent messages in #{channel.name} ---\n" + "\n".join(lines)


READ_CHANNEL_TOOL = {
    "type": "function",
    "function": {
        "name": "read_channel",
        "description": (
            "Read the most recent real messages from a Discord channel in this "
            "server. Use this whenever a channel is referenced by name instead "
            "of guessing or saying you can't see it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "channel_name": {
                    "type": "string",
                    "description": "Channel name without the '#' prefix, e.g. 'unu_other'",
                }
            },
            "required": ["channel_name"],
        },
    },
}


async def execute_tool(name: str, args: dict) -> str:
    if name != "read_channel":
        return f"Unknown tool: {name}"
    channel_name = str(args.get("channel_name", "")).lstrip("#").lower()
    ch = channel_name_to_obj.get(channel_name)
    if not ch:
        return f"No channel named '{channel_name}' found in this server."
    try:
        return await fetch_channel_context(ch)
    except discord.Forbidden:
        return f"No permission to read #{channel_name}."
    except Exception as e:
        return f"Error reading #{channel_name}: {e}"


def resolve_channel_mentions(message: discord.Message) -> str:
    """Turn a real Discord channel mention (<#id>) into readable #name text so
    the model can actually use the name when deciding whether to call the
    read_channel tool."""
    content = message.content
    for ch in message.channel_mentions:
        content = content.replace(f"<#{ch.id}>", f"#{ch.name}")
    return content


@client.event
async def on_ready():
    global interactive_channel
    for guild in client.guilds:
        for ch in guild.text_channels:
            channel_name_to_obj[ch.name.lower()] = ch
            if ch.name.lower() in WATCHED_CHANNEL_NAMES:
                watched_channel_ids.add(ch.id)
    interactive_channel = client.get_channel(INTERACTIVE_CHANNEL_ID)
    print(f"Logged in as {client.user} (bots-v1), watching channel {INTERACTIVE_CHANNEL_ID}, "
          f"can read {len(channel_name_to_obj)} channels, monitoring "
          f"{len(watched_channel_ids)} news channels for auto-reactions", flush=True)


async def handle_watched_post(message: discord.Message):
    if message.author == client.user:
        return  # never react to our own posts
    text = format_message_content(message)
    if not text:
        return
    remember(message.channel.name, text)
    context = recent_context()
    prompt = f"New post just now in #{message.channel.name} from {message.author.name}: {text}"
    if context:
        prompt += f"\n\n{context}"
    print(f"[news-watch] #{message.channel.name}: {text[:150]!r}", flush=True)
    await run_discussion([], prompt, interactive_channel, passive_note=NEWS_NOTE, should_escalate=True)


@client.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    # Discord attaches link-preview embeds via an edit, not the original
    # message. Only react here if the original post had nothing usable on
    # its own (e.g. a bare link) - otherwise this double-reacts to every
    # single post once the embed lands a moment after the real trigger.
    if after.channel.id not in watched_channel_ids or before.embeds or not after.embeds:
        return
    if format_message_content(before):
        return  # original already had real content, already handled
    print(f"[news-watch] embed arrived via edit in #{after.channel.name}", flush=True)
    await handle_watched_post(after)


@client.event
async def on_message(message: discord.Message):
    print(f"[on_message] channel={message.channel.id} author={message.author} bot={message.author.bot} content={message.content!r}", flush=True)

    if message.channel.id in watched_channel_ids:
        await handle_watched_post(message)
        return

    if message.author.bot:
        return
    if message.channel.id != INTERACTIVE_CHANNEL_ID:
        print(f"[skip] wrong channel: got {message.channel.id}, want {INTERACTIVE_CHANNEL_ID}", flush=True)
        return

    # resolve real Discord channel mentions (<#id>) to readable #name text so
    # the model can actually use the name if it decides to call read_channel
    content = resolve_channel_mentions(message).strip()
    if not content:
        return

    # anyone named ANYWHERE in the message (not just at the start) is forced
    # to respond - covers "ditto: x", "hi meowth", "what does mew think", etc.
    forced_keys = find_mentioned_personas(content, exclude=set())
    await run_discussion(forced_keys, content, interactive_channel)


PASS_WORD = "PASS"
WATCH_NOTE = (
    "\n\nThis is an open group chat and nobody addressed you specifically. "
    "Only respond if you have a real, specific reaction - most messages are "
    f"NOT worth a response. Default to passing. Reply with exactly the "
    f"single word {PASS_WORD} unless you genuinely have something to add."
)
NEWS_NOTE = (
    "\n\nThis is a real item from a news/intel feed you're built to react to "
    "- it's already been curated as worth seeing, not random chatter. Give "
    "your genuine take from your own angle if you have one. Only reply with "
    f"exactly the single word {PASS_WORD} if this specific item is truly "
    "routine noise with nothing to say about it (e.g. an unremarkable "
    "corporate filing) - real news, market moves, and notable events deserve "
    "a reaction.\n\n"
    "If a 'recently covered' list is included below and this item (or your "
    f"point about it) is already in there, reply {PASS_WORD} instead of "
    "repeating a fresh take on the same story. That list is for your own "
    "internal reference only - never mention it, quote it, tag it, or cite "
    "it in your actual reply. Your reply should read like natural speech: "
    "no headers, no tags, no bullet-point source lists, no markdown "
    f"formatting around {PASS_WORD} itself."
)
REACT_NOTE = (
    "\n\nEveryone above just answered independently - none of them had seen "
    "each other's replies yet. Now that you can see the full picture, do you "
    "actually agree? If you genuinely disagree with someone, or want to build "
    "on a specific point they made, say so in your own voice - name them if "
    f"you're responding to something specific they said. If you have nothing "
    f"real to add, reply with exactly the single word {PASS_WORD} - don't "
    "force a reaction just to react."
)


def is_pass(reply: str) -> bool:
    if not reply:
        return True
    stripped = re.sub(r"[^A-Za-z]", "", reply).upper()
    return stripped == PASS_WORD or len(stripped) == 0


SYNTHESIS_SYSTEM_PROMPT = (
    "You are a neutral summarizer. Terse, objective, no personality, no "
    "opinion of your own - just distill. Ablation-tested: an unvoiced, "
    "personality-free summary stays more faithful to the actual discussion "
    "than one filtered through a persona's voice, which tends to drift "
    "toward reframing or editorializing instead of reporting."
)
SYNTHESIS_NOTE = (
    "\n\nThe discussion above covered some ground, possibly including "
    "disagreement between people. Distill it into a short bulleted wrap-up - "
    "one bullet per distinct point or unresolved disagreement, substance "
    "only, not a recap of who said what. This is the natural close of the "
    "thread, so actually land on where things ended up if there's a clear "
    f"answer. If the discussion was already simple and a wrap-up would add "
    f"nothing beyond what's already obvious, reply with exactly the single "
    f"word {PASS_WORD} instead of forcing a summary."
)


async def maybe_synthesize(transcript_lines: list, typing_channel: discord.TextChannel):
    """Natural conclusion instead of a hard turn cap: once a discussion has
    actually happened, close it out with a neutral bulleted wrap-up - or skip
    entirely if there's nothing to distill. Deliberately NOT voiced through a
    persona (ablation-tested: personality injection drifts away from
    faithfully reporting what was actually said)."""
    transcript = "\n".join(transcript_lines) + SYNTHESIS_NOTE
    try:
        reply = await chat(SYNTHESIS_SYSTEM_PROMPT, transcript)
    except Exception as e:
        print(f"[synthesize] failed: {e!r}", flush=True)
        return
    reply = clean_reply(reply)
    if is_pass(reply):
        print("[synthesize] nothing to wrap up", flush=True)
        return
    try:
        await typing_channel.send(reply)
    except Exception as e:
        print(f"[synthesize] failed to post: {e!r}", flush=True)


ESCALATION_SYSTEM_PROMPT = (
    "You are a neutral filter deciding whether a user should be personally "
    "notified about something. You are not a persona - be terse and objective."
)
ESCALATION_PROMPT_TEMPLATE = (
    "Your group of personas just had this discussion reacting to something:\n\n"
    "{transcript}\n\n"
    "Judge ONLY the real-world importance of the underlying content - never "
    "how much discussion or disagreement it generated. A heated debate over "
    "a trivial detail (a typo, a doc wording change, routine maintenance) is "
    "NOT a reason to escalate. Only escalate for something genuinely urgent "
    "or high-stakes: an active exploit, a major breach, something requiring "
    "action soon. When in doubt, don't escalate - the user can always ask.\n\n"
    "Reply with exactly 'NO' if not worth pinging, or 'YES: <BLUF/TL;DR - the "
    "bottom line first, one tight sentence, no preamble, no hedging, just the "
    "single most important fact and why it matters right now>' if it is."
)
ESCALATION_COOLDOWN_SECONDS = 600  # don't ping more than once per ~10 minutes
ESCALATION_PERSONA_KEY = "analyst"  # ping goes out in this persona's voice


async def maybe_escalate(transcript_lines: list, typing_channel: discord.TextChannel):
    """One consolidated judgment call over the WHOLE discussion, made after
    everyone's reacted - not left to any single persona to decide on its own
    mid-reaction, which was pinging way too eagerly and too often. Also
    cooldown-limited so a burst of separate stories can't fire off several
    pings back to back, and delivered through a persona's own voice/webhook
    rather than the bare bot account."""
    if not DISCORD_USER_ID or len(transcript_lines) <= 1:
        return
    if seconds_since_last_ping() < ESCALATION_COOLDOWN_SECONDS:
        print("[escalate] skipped, still in cooldown", flush=True)
        return
    try:
        verdict = await chat(ESCALATION_SYSTEM_PROMPT, ESCALATION_PROMPT_TEMPLATE.format(transcript="\n".join(transcript_lines)))
    except Exception as e:
        print(f"[escalate] failed: {e!r}", flush=True)
        return
    verdict = (verdict or "").strip()
    if not verdict.upper().startswith("YES"):
        print(f"[escalate] no ping warranted: {verdict!r}", flush=True)
        return
    reason = verdict.split(":", 1)[1].strip() if ":" in verdict else verdict
    print(f"[escalate] pinging user via {ESCALATION_PERSONA_KEY}: {reason}", flush=True)
    ok = await post_reply(ESCALATION_PERSONA_KEY, f"<@{DISCORD_USER_ID}> {reason}")
    if ok:
        record_ping()
    else:
        print("[escalate] failed to send ping", flush=True)


async def run_discussion(forced_keys: list, prompt: str, typing_channel: discord.TextChannel, passive_note: str = WATCH_NOTE, should_escalate: bool = False):
    """If anyone is named, ONLY they respond - no pile-on from everyone else.
    If nobody is named, every persona gets a chance to chime in but defaults
    to passing (PASS_WORD) unless they genuinely have something to add. If a
    reply mentions another known persona, that persona gets pulled in too.
    No hard cap on rounds - naturally bounded since only len(PERSONAS)
    distinct voices can ever be pulled in, and each speaks at most once
    per phase. Ends with a synthesis wrap-up instead of an arbitrary cutoff."""
    forced_keys = list(dict.fromkeys(forced_keys))  # de-dup, keep order
    spoken = set(forced_keys)

    if forced_keys:
        replies = await asyncio.gather(*(respond_as(k, prompt, typing_channel) for k in forced_keys))
        candidate_keys = forced_keys
    else:
        candidate_keys = list(PERSONAS.keys())
        raw_replies = await asyncio.gather(*(get_reply(k, prompt + passive_note) for k in candidate_keys))
        replies = []
        for key, reply in zip(candidate_keys, raw_replies):
            if is_pass(reply):
                replies.append(None)
                continue
            spoken.add(key)
            replies.append(reply if await post_reply(key, reply) else None)

    transcript_lines = [f"User: {prompt}"]
    queue = []
    for key, reply in zip(candidate_keys, replies):
        if not reply:
            continue
        transcript_lines.append(f"{PERSONAS[key]['name']}: {reply}")
        for mentioned_key in find_mentioned_personas(reply, exclude=spoken):
            if mentioned_key not in queue:
                queue.append(mentioned_key)

    # reaction pass: if 2+ people already spoke, give each of them a real
    # look at what the others said and a chance to push back or build on it -
    # without this, independent replies never actually disagree with anyone,
    # since the first round is parallel and nobody's seen anyone else's take.
    reactors = [k for k in spoken if k in candidate_keys]
    if len(transcript_lines) > 2 and reactors:
        reaction_transcript = "\n".join(transcript_lines) + REACT_NOTE
        raw_reactions = await asyncio.gather(*(get_reply(k, reaction_transcript) for k in reactors))
        for key, reaction in zip(reactors, raw_reactions):
            if is_pass(reaction):
                continue
            if not await post_reply(key, reaction):
                continue
            transcript_lines.append(f"{PERSONAS[key]['name']}: {reaction}")
            for mentioned_key in find_mentioned_personas(reaction, exclude=spoken):
                if mentioned_key not in queue:
                    queue.append(mentioned_key)

    # No numeric cap here on purpose: `spoken` already makes this finite on
    # its own (there are only len(PERSONAS) people who could ever be pulled
    # in, and each can only enter this queue once), so an arbitrary turn
    # limit isn't needed to prevent it running away - and an arbitrary limit
    # was cutting off real discussions before they'd actually resolved.
    while queue:
        persona_key = queue.pop(0)
        if persona_key in spoken:
            continue
        spoken.add(persona_key)

        transcript = "\n".join(transcript_lines) + passive_note
        reply = await get_reply(persona_key, transcript)
        if is_pass(reply):
            continue
        if not await post_reply(persona_key, reply):
            continue

        transcript_lines.append(f"{PERSONAS[persona_key]['name']}: {reply}")
        for mentioned_key in find_mentioned_personas(reply, exclude=spoken):
            if mentioned_key not in queue:
                queue.append(mentioned_key)

    if len(transcript_lines) > 3:
        await maybe_synthesize(transcript_lines, typing_channel)

    if should_escalate:
        await maybe_escalate(transcript_lines, typing_channel)


OTHER_PERSONAS_NOTE = (
    "You're in a group chat alongside other personas ({names}). "
    "You can address them by name if you're reacting to something one of them said. "
    "Stay in your own voice - don't speak for them.\n\n"
    "You have a read_channel tool that fetches real, current messages from any "
    "channel in this server by name. Use it whenever someone references a "
    "specific channel - never guess or invent what a channel might contain."
)


def system_prompt_for(persona_key: str) -> str:
    persona = PERSONAS[persona_key]
    others = ", ".join(p["name"] for k, p in PERSONAS.items() if k != persona_key)
    identity = f"Your name is {persona['name']}. "
    return identity + persona["system_prompt"] + "\n\n" + OTHER_PERSONAS_NOTE.format(names=others)


async def get_reply(persona_key: str, prompt: str, use_tools: bool = False):
    try:
        print(f"[chat] calling ollama for {persona_key}...", flush=True)
        reply = await chat(
            system_prompt_for(persona_key),
            prompt,
            tools=[READ_CHANNEL_TOOL] if use_tools else None,
            tool_executor=execute_tool if use_tools else None,
        )
        print(f"[chat] got reply: {reply!r}", flush=True)
        return clean_reply(reply)
    except Exception as e:
        print(f"[chat] failed for persona {persona_key}: {e!r}", flush=True)
        return None


def _is_pass_line(line: str) -> bool:
    stripped = re.sub(r"[^A-Za-z]", "", line).upper()
    return stripped == PASS_WORD


def clean_reply(reply: str) -> str:
    """Strip a stray PASS the model sometimes tacks onto otherwise real
    content - as its own line, or glued onto the end/start of a line
    (plain or **markdown-bolded**) - out of habit from the passive-round
    instructions. Also drops any meta/citation-style block like
    '#recently_covered:' plus the bullet list that follows it."""
    if not reply:
        return reply
    reply = re.sub(r"^[\s*_]*\bPASS\b[\s*_.:]*", "", reply, flags=re.IGNORECASE)
    reply = re.sub(r"[\s*_.:]*\bPASS\b[\s*_]*$", "", reply, flags=re.IGNORECASE)
    lines = reply.split("\n")
    kept = []
    skipping_meta_list = False
    for line in lines:
        if _is_pass_line(line):
            continue
        if re.match(r"^\s*#?recently[_ ]covered", line, re.IGNORECASE):
            skipping_meta_list = True
            continue
        if skipping_meta_list:
            if re.match(r"^\s*[-*]\s", line) or not line.strip():
                continue
            skipping_meta_list = False
        kept.append(line)
    return "\n".join(kept).strip()


async def post_reply(persona_key: str, reply: str):
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


async def respond_as(persona_key: str, prompt: str, typing_channel: discord.TextChannel):
    async with typing_channel.typing():
        reply = await get_reply(persona_key, prompt, use_tools=True)
    if reply is None:
        return None
    ok = await post_reply(persona_key, reply)
    return reply if ok else None


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
