import asyncio
import os
import re
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

import discord

from personas import PERSONAS, STACK_RELEVANCE_NOTE, MY_STACK
from ollama_chat import chat
from poster import post_to_webhook
from memory import remember, recent_context, seconds_since_last_ping, record_ping
from web_fetch import fetch_url_content
from market_data import get_ticker_context
from uw_client import ticker_snapshot as get_uw_snapshot
from threat_intel import check_indicator

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


DISCORD_TIMESTAMP_RE = re.compile(r"<t:(\d+):[a-zA-Z]>")


def _replace_discord_timestamp(match: re.Match) -> str:
    try:
        dt = datetime.fromtimestamp(int(match.group(1)), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, OverflowError):
        return match.group(0)


def format_message_content(msg: discord.Message) -> str:
    # Discord's own <t:UNIX:FLAG> timestamp markup, left raw, was confusing
    # the model's generation (it garbled into corrupted fragments like
    # 'iNdEx:1790220909:t>' mid-reply) - spelling it out as plain text avoids
    # feeding the model syntax it was never meant to parse.
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
    return DISCORD_TIMESTAMP_RE.sub(_replace_discord_timestamp, " | ".join(parts))


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


FETCH_URL_TOOL = {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": (
            "Fetch and read the actual text content of a URL, such as a link "
            "attached to a news item. Use this instead of guessing what a "
            "linked article, bulletin, or commit actually says - especially "
            "when the title or summary alone is vague or uses unfamiliar "
            "terminology. The result is for YOUR understanding only - react "
            "to it in your own voice and words, never paste or quote the "
            "raw fetched content back as your reply."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The exact URL to fetch, taken from the text you were given.",
                }
            },
            "required": ["url"],
        },
    },
}


TICKER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_ticker_context",
        "description": (
            "Look up a stock ticker's real current price, % change, volume, "
            "and company name. Use this whenever a ticker symbol appears "
            "(e.g. in a market alert) instead of guessing what the company "
            "is or why it moved - tickers get reassigned to unrelated "
            "companies after mergers/delistings, so the symbol alone can be "
            "misleading."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "The ticker symbol, e.g. 'PARA' or 'GDDY'.",
                }
            },
            "required": ["ticker"],
        },
    },
}


UW_TOOL = {
    "type": "function",
    "function": {
        "name": "get_unusual_whales_data",
        "description": (
            "Look up real options flow, dark pool activity, and GEX for a "
            "ticker from the user's own Unusual Whales account - this is "
            "what actually explains WHY a stock is moving unusually, unlike "
            "get_ticker_context which only gives price/company-name. Use "
            "this when a market alert needs real 'why' context, not just "
            "identity confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "The ticker symbol, e.g. 'PARA' or 'GDDY'.",
                }
            },
            "required": ["ticker"],
        },
    },
}


THREAT_INTEL_TOOL = {
    "type": "function",
    "function": {
        "name": "check_threat_indicator",
        "description": (
            "Check an IP, domain, URL, or file hash against real "
            "threat-intel sources (Shodan, VirusTotal, urlscan.io, "
            "ThreatFox, MalwareBazaar, URLhaus, YARAify, Feodo Tracker) - "
            "the actual services this data comes from, on the user's own "
            "registered access. Use this to verify whether an indicator "
            "from an IOC dump or research post is a real, currently known "
            "threat instead of taking the claim at face value."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "indicator": {
                    "type": "string",
                    "description": "The IP, domain, URL, or file hash to check.",
                }
            },
            "required": ["indicator"],
        },
    },
}


async def execute_tool(name: str, args: dict) -> str:
    if name == "read_channel":
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
    if name == "fetch_url":
        url = str(args.get("url", "")).strip()
        if not url:
            return "No URL given."
        return await fetch_url_content(url)
    if name == "get_ticker_context":
        ticker = str(args.get("ticker", "")).strip()
        if not ticker:
            return "No ticker given."
        return await get_ticker_context(ticker)
    if name == "get_unusual_whales_data":
        ticker = str(args.get("ticker", "")).strip()
        if not ticker:
            return "No ticker given."
        return await get_uw_snapshot(ticker)
    if name == "check_threat_indicator":
        indicator = str(args.get("indicator", "")).strip()
        if not indicator:
            return "No indicator given."
        return await check_indicator(indicator)
    return f"Unknown tool: {name}"


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

    # Checking the channel's own name (always available on the message
    # itself) instead of relying solely on the watched_channel_ids cache
    # avoids a startup race: messages can be dispatched via on_message
    # before on_ready finishes populating that cache, which was silently
    # dropping the first few news items after every restart.
    channel_name = getattr(message.channel, "name", None)
    if message.channel.id in watched_channel_ids or (channel_name and channel_name.lower() in WATCHED_CHANNEL_NAMES):
        watched_channel_ids.add(message.channel.id)
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
    "corporate filing, or human-interest fluff with no real angle for you) - "
    "real news, market moves, and notable events deserve a reaction, but not "
    "everything does. If your actual reaction is some version of 'not much "
    f"to react to here', that means reply {PASS_WORD} instead - don't write "
    "that sentiment out as your reply. If the title or summary alone is vague or uses unfamiliar "
    "terminology, use your fetch_url tool on the link before reacting instead "
    "of speculating about what it probably means.\n\n"
    "A 'recently covered' list may be included below. It is DATA, not "
    f"content. Its ONLY purpose: check whether THIS SAME story already ran - "
    f"if so, reply {PASS_WORD}. You may NEVER summarize it, report on it, "
    "pull facts from it, or mention any story in it, even if the current "
    f"item is boring and the list looks more interesting. If the current "
    f"item alone has nothing worth saying, that means {PASS_WORD} - it does "
    "NOT mean 'talk about the list instead.' Stay entirely on the current "
    "item above. Never mention, quote, tag, or "
    "cite the list itself in your actual reply. Your reply should read like "
    "natural speech: no headers, no tags, no bullet-point source lists, no "
    f"markdown formatting around {PASS_WORD} itself."
    + STACK_RELEVANCE_NOTE
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


SAGE_NAME = "Sage"
SAGE_AVATAR_URL = "https://raw.githubusercontent.com/the-grizzly-bear/bots-v1/master/icons/synthesis.png?v=3"

SYNTHESIS_SYSTEM_PROMPT = (
    "You are a neutral summarizer. Terse, objective, no personality, no "
    "opinion of your own - just distill. Ablation-tested: an unvoiced, "
    "personality-free summary stays more faithful to the actual discussion "
    "than one filtered through a persona's voice, which tends to drift "
    "toward reframing or editorializing instead of reporting."
)
SYNTHESIS_NOTE = (
    "\n\nThe discussion above covered some ground, possibly including "
    "disagreement between people. Distill it into a wrap-up of AT MOST 2 "
    "bullets - only add a 2nd if there were genuinely two separate points or "
    "an unresolved disagreement, otherwise just 1. Each bullet is ONE short "
    "sentence, not a compound sentence with multiple clauses - substance "
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
    webhook_url = os.environ.get("WEBHOOK_PERSONA_ANALYST")  # shared webhook, any key works
    try:
        await post_to_webhook(webhook_url, reply, SAGE_NAME, SAGE_AVATAR_URL)
    except Exception as e:
        print(f"[synthesize] failed to post: {e!r}", flush=True)


ESCALATION_SYSTEM_PROMPT = (
    "You are a neutral filter classifying how urgently a user should be "
    "notified about something. You are not a persona - be terse and objective."
)

# Tier -> (role env var, cooldown seconds). Lower tiers are meant to fire
# more often (they're muteable per-role in Discord, unlike a direct ping),
# so they get a shorter cooldown; CRITICAL/HIGH should be rare by
# classification already, but keep a real cooldown as a burst backstop.
ESCALATION_TIERS = {
    "CRITICAL": ("ROLE_CRITICAL", 300),
    "HIGH": ("ROLE_HIGH", 600),
    "MEDIUM": ("ROLE_MEDIUM", 1200),
    "LOW": ("ROLE_LOW", 1800),
    "INFO": ("ROLE_INFO", 1800),
}

ESCALATION_PROMPT_TEMPLATE = (
    "Your group of personas just had this discussion reacting to something:\n\n"
    "{transcript}\n\n"
    "Classify how urgently this deserves a role ping, from these tiers "
    "(most to least severe): CRITICAL, HIGH, MEDIUM, LOW, INFO, or NONE if "
    "it doesn't deserve any ping at all beyond the normal discussion.\n\n"
    "- CRITICAL: an active exploit or breach against something the user "
    "actually runs - " + ", ".join(MY_STACK) + " - or an unambiguous, "
    "rare global emergency (large-scale active exploit spreading right now, "
    "critical infrastructure failure) that demands attention immediately.\n"
    "- HIGH: directly concerns the user's own stack and needs their "
    "attention soon, but isn't actively being exploited against them right now.\n"
    "- MEDIUM: genuinely significant industry news, not stack-specific, "
    "worth real attention but nothing to act on.\n"
    "- LOW: a minor, stack-unrelated heads-up - worth a passing note, "
    "nothing more.\n"
    "- INFO: mildly noteworthy at most - logged for the record, not "
    "something anyone needs to see immediately.\n"
    "- NONE: routine. This is most items. Use this whenever in doubt.\n\n"
    "Do NOT rate general financial market moves, company pricing decisions, "
    "macroeconomic commentary, geopolitical statements or diplomacy, "
    "political or legal rulings, or general AI-industry news above LOW/INFO "
    "just because they're notable - none of these need urgent attention. "
    "Journalistic phrasing like 'could significantly impact', 'raising "
    "concerns', or 'signals a shift' appears in nearly all news writing and "
    "is not itself a signal of urgency - judge the actual content, not the "
    "tone it's reported in. Also ignore how much discussion or disagreement "
    "this generated - a heated debate over a trivial detail is not a reason "
    "to rate it higher.\n\n"
    "Reply with exactly 'NONE' if nothing is warranted, or "
    "'<TIER>: <BLUF/TL;DR - the bottom line first, one tight sentence, no "
    "preamble, no hedging, just the single most important fact and why it "
    "matters>' otherwise, using one of the five tier names above."
)
ESCALATION_PERSONA_KEY = "analyst"  # ping goes out in this persona's voice

# A burst of separate stories triggers many concurrent maybe_escalate() calls.
# Without this lock, each one checks "any recent ping?" before any of them
# has recorded its own, so the cooldown check passes for all of them at once -
# this is what actually caused the "multiple pings from one burst" bug even
# after the cooldown was added. The lock makes check-then-record atomic.
_escalation_lock = asyncio.Lock()


async def maybe_escalate(transcript_lines: list, typing_channel: discord.TextChannel):
    """One consolidated judgment call over the WHOLE discussion, made after
    everyone's reacted - not left to any single persona to decide on its own
    mid-reaction, which was pinging way too eagerly and too often. Classifies
    into a severity tier (role ping) rather than a single yes/no so the user
    can mute low tiers per-role in Discord instead of getting every ping at
    the same volume. Delivered through a persona's own voice/webhook rather
    than the bare bot account."""
    if len(transcript_lines) <= 1:
        return
    async with _escalation_lock:
        try:
            verdict = await chat(ESCALATION_SYSTEM_PROMPT, ESCALATION_PROMPT_TEMPLATE.format(transcript="\n".join(transcript_lines)))
        except Exception as e:
            print(f"[escalate] failed: {e!r}", flush=True)
            return
        verdict = (verdict or "").strip()
        tier = verdict.split(":", 1)[0].strip().upper()
        if tier not in ESCALATION_TIERS:
            print(f"[escalate] no ping warranted: {verdict!r}", flush=True)
            return

        role_env, cooldown = ESCALATION_TIERS[tier]
        if seconds_since_last_ping(tier) < cooldown:
            print(f"[escalate] {tier} skipped, still in cooldown", flush=True)
            return
        role_id = os.environ.get(role_env)
        if not role_id:
            print(f"[escalate] {tier} verdict but {role_env} not set, skipping ping", flush=True)
            return

        reason = verdict.split(":", 1)[1].strip() if ":" in verdict else verdict
        print(f"[escalate] pinging {tier} via {ESCALATION_PERSONA_KEY}: {reason}", flush=True)
        ok = await post_reply(ESCALATION_PERSONA_KEY, f"<@&{role_id}> {reason}")
        if ok:
            record_ping(tier)
        else:
            print(f"[escalate] failed to send {tier} ping", flush=True)


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
    "specific channel - never guess or invent what a channel might contain. "
    "You also have a fetch_url tool that reads the actual content of a link "
    "you've been given - use it instead of guessing what an article or "
    "commit says from its title alone. You have a get_ticker_context tool "
    "that looks up a stock ticker's real current price and company name - "
    "use it whenever a ticker symbol shows up instead of guessing what the "
    "company is, since symbols get reassigned to unrelated companies over "
    "time. You also have a get_unusual_whales_data tool with real options "
    "flow, dark pool, and GEX data for a ticker from the user's own account "
    "- use it when you actually need to know WHY a stock is moving, not "
    "just what it is. You also have a check_threat_indicator tool that "
    "checks a real IP, domain, URL, or file hash against Shodan, "
    "VirusTotal, urlscan.io, ThreatFox, MalwareBazaar, URLhaus, YARAify, "
    "and Feodo Tracker - use it on an actual indicator from an IOC dump or "
    "research post instead of taking the claim at face value."
)


def system_prompt_for(persona_key: str) -> str:
    persona = PERSONAS[persona_key]
    others = ", ".join(p["name"] for k, p in PERSONAS.items() if k != persona_key)
    identity = f"Your name is {persona['name']}. "
    return identity + persona["system_prompt"] + "\n\n" + OTHER_PERSONAS_NOTE.format(names=others)


_NON_LATIN_SCRIPT_RE = re.compile(
    r"[一-鿿぀-ヿ가-힯Ѐ-ӿ؀-ۿ]"
)


def _is_mostly_non_english(text: str) -> bool:
    """Prompt-level 'always reply in English' isn't 100% reliable - qwen2.5
    occasionally drifts into Chinese (or other scripts) with no content
    trigger. Catching it here instead of trusting the instruction alone,
    same approach as the other model-degeneration fixes (PASS leaks, fake
    tool calls)."""
    letters = re.findall(r"\w", text, flags=re.UNICODE)
    if len(letters) < 10:
        return False
    non_latin = len(_NON_LATIN_SCRIPT_RE.findall(text))
    return non_latin / len(letters) > 0.2


async def get_reply(persona_key: str, prompt: str):
    # Tools are always passed, never conditional - the system prompt
    # (OTHER_PERSONAS_NOTE) unconditionally tells every persona it has these
    # tools, so leaving them unwired for some calls made the model type out
    # a fake 'fetch_url(...)' as plain text instead of a real tool call, with
    # no real result to ground the reply.
    for attempt in range(2):
        try:
            print(f"[chat] calling ollama for {persona_key}...", flush=True)
            reply = await chat(
                system_prompt_for(persona_key),
                prompt,
                tools=[READ_CHANNEL_TOOL, FETCH_URL_TOOL, TICKER_TOOL, UW_TOOL, THREAT_INTEL_TOOL],
                tool_executor=execute_tool,
            )
            print(f"[chat] got reply: {reply!r}", flush=True)
            if reply and _is_mostly_non_english(reply):
                print(f"[chat] {persona_key} replied in a non-English script, retrying" if attempt == 0 else f"[chat] {persona_key} still non-English after retry, dropping", flush=True)
                if attempt == 0:
                    continue
                return None
            return clean_reply(reply, own_name=PERSONAS[persona_key]["name"])
        except Exception as e:
            print(f"[chat] failed for persona {persona_key}: {e!r}", flush=True)
            return None


def _is_pass_line(line: str) -> bool:
    stripped = re.sub(r"[^A-Za-z]", "", line).upper()
    return stripped == PASS_WORD


def clean_reply(reply: str, own_name: str = None) -> str:
    """Strip a stray PASS the model sometimes tacks onto otherwise real
    content - as its own line, or glued onto the end/start of a line
    (plain or **markdown-bolded**) - out of habit from the passive-round
    instructions. Also drops any meta/citation-style block like
    '#recently_covered:' plus the bullet list that follows it, and a
    self-labeled 'Name: ' prefix leaked from the transcript format."""
    if not reply:
        return reply
    # Defensive backstop: the model occasionally types out a fake tool call
    # as plain text instead of a real structured tool_calls entry (garbled
    # lead-in token, a {"name": ..., "arguments": {...}} block, sometimes a
    # literal </tool_call> tag) - strip that garbage rather than post it.
    reply = re.sub(
        r'(?:(?<=\n)|^)\s*[A-Za-z]{1,15}\s*\n?\{"name":\s*"[a-zA-Z_]+",\s*"arguments":\s*\{.*?\}\}\s*(?:</?tool_call>)?',
        "", reply, flags=re.DOTALL,
    )
    reply = re.sub(r"</?tool_call>", "", reply, flags=re.IGNORECASE)
    # Same degeneration, milder form: a garbled camelCase-looking lead-in
    # token before a colon with no JSON block attached (e.g. "sourceMapping:
    # <real reply>", "iNdEx: <real reply>") - real English words never have
    # a lowercase-then-uppercase transition inside them, so this is a safe
    # tell for corrupted output rather than an actual word.
    reply = re.sub(r"^\s*[A-Za-z]*[a-z][A-Z][A-Za-z]*:\s*(-?\d+\s*)?", "", reply)
    # Same tell, but sometimes the ENTIRE reply is just the bare garbled
    # token with nothing else at all (e.g. reply == "iNdEx") - no colon to
    # anchor on, so check the whole trimmed reply rather than just a prefix.
    # Anchored to the FULL string (not just a leading word) so this can't
    # strip a real word that happens to open a real sentence.
    if re.fullmatch(r"[A-Za-z]*[a-z][A-Z][A-Za-z]*", reply.strip()):
        reply = ""
    reply = re.sub(r"\n{2,}", "\n", reply).strip()
    reply = re.sub(r"^[\s*_]*\bPASS\b[\s*_.:]*", "", reply, flags=re.IGNORECASE)
    reply = re.sub(r"[\s*_.:]*\bPASS\b[\s*_.:]*$", "", reply, flags=re.IGNORECASE)
    if own_name:
        reply = re.sub(rf"^\s*{re.escape(own_name)}\s*:\s*", "", reply, flags=re.IGNORECASE)
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
        reply = await get_reply(persona_key, prompt)
    if reply is None:
        return None
    ok = await post_reply(persona_key, reply)
    return reply if ok else None


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
