"""Ablation: current (parallel round + bolted-on reaction pass) vs proposed
(single sequential queue, each persona sees the transcript-so-far) for the
passive news-reaction flow. Measures call count, wall-clock time, and lets
you eyeball transcript quality side by side. Real captured items, no fake
data - CVE-2026-96803 (substantive) and the goodlog win7 PR (trivial noise,
the exact item that triggered the earlier scribe topic-blending bug)."""

import asyncio
import time

from personas import PERSONAS
from ollama_chat import chat
from bot import (
    NEWS_NOTE, REACT_NOTE, PASS_WORD, is_pass, system_prompt_for, clean_reply,
    READ_CHANNEL_TOOL, FETCH_URL_TOOL, execute_tool,
)

TOOLS = [READ_CHANNEL_TOOL, FETCH_URL_TOOL]

CANDIDATE_KEYS = list(PERSONAS.keys())

ITEMS = {
    "SUBSTANTIVE (CVE-2026-96803)": (
        "New post just now in #news from CVE Feed: CVE-2026-96803 - java110 "
        "MicroCommunity fallBack API Endpoint BusinessApi.java "
        "QueryServiceSMOImpl.fallBack sql injection\n"
        "https://cvefeed.io/vuln/detail/CVE-2026-96803"
    ),
    "TRIVIAL NOISE (goodlog win7 PR)": (
        "New post just now in #news from Sigma: Merge PR #6259 from "
        "@swachchhanda000 - Fix goodlog win7 baseline path…\n"
        "https://github.com/SigmaHQ/sigma/commit/09e36f806795b464c7653c45a99e57cd6fe43e90"
    ),
}


async def current_arch(item_text: str):
    t0 = time.time()
    round1_raw = await asyncio.gather(*(chat(system_prompt_for(k), item_text + NEWS_NOTE, tools=TOOLS, tool_executor=execute_tool) for k in CANDIDATE_KEYS))
    calls = len(CANDIDATE_KEYS)
    transcript_lines = [f"User: {item_text}"]
    spoken = []
    for k, r in zip(CANDIDATE_KEYS, round1_raw):
        r = clean_reply(r, own_name=PERSONAS[k]["name"])
        if not is_pass(r):
            transcript_lines.append(f"{PERSONAS[k]['name']}: {r}")
            spoken.append(k)

    reaction_calls = 0
    if len(transcript_lines) > 2 and spoken:
        reaction_transcript = "\n".join(transcript_lines) + REACT_NOTE
        round2_raw = await asyncio.gather(*(chat(system_prompt_for(k), reaction_transcript, tools=TOOLS, tool_executor=execute_tool) for k in spoken))
        reaction_calls = len(spoken)
        for k, r in zip(spoken, round2_raw):
            r = clean_reply(r, own_name=PERSONAS[k]["name"])
            if not is_pass(r):
                transcript_lines.append(f"{PERSONAS[k]['name']} [reaction]: {r}")

    return transcript_lines, calls + reaction_calls, time.time() - t0


async def proposed_arch(item_text: str):
    t0 = time.time()
    transcript_lines = [f"User: {item_text}"]
    calls = 0
    for k in CANDIDATE_KEYS:
        transcript = "\n".join(transcript_lines) + NEWS_NOTE
        r = await chat(system_prompt_for(k), transcript, tools=TOOLS, tool_executor=execute_tool)
        calls += 1
        r = clean_reply(r, own_name=PERSONAS[k]["name"])
        if not is_pass(r):
            transcript_lines.append(f"{PERSONAS[k]['name']}: {r}")
    return transcript_lines, calls, time.time() - t0


async def main():
    for label, item_text in ITEMS.items():
        print(f"\n{'#' * 72}\n{label}\n{'#' * 72}")
        for arch_name, arch_fn in [("CURRENT (parallel + reaction pass)", current_arch), ("PROPOSED (sequential queue)", proposed_arch)]:
            transcript_lines, calls, elapsed = await arch_fn(item_text)
            print(f"\n{'=' * 70}\n{arch_name}\n{'=' * 70}")
            print(f"calls={calls}  wall_time={elapsed:.1f}s  replies={len(transcript_lines) - 1}")
            for line in transcript_lines[1:]:
                print(f"  - {line[:300]}")


if __name__ == "__main__":
    asyncio.run(main())
