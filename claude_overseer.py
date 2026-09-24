"""One-shot calls to the sandboxed Claude Code container (see the separate
claude-discord-bridge project) for judgment calls where quality matters more
than volume - currently just the escalation-tier classifier. Deliberately
NOT the full claude_bridge.py machinery from that project (no session/
--resume, no webhook handling) since a classification is stateless and
independent each time - reusing that machinery here would also mean an
awkward cross-project import between two separate repos.

Tested head-to-head against the local Ollama classifier before wiring this
in: better-calibrated (correctly downgraded an AI-industry press-release
item that Ollama over-escalated to MEDIUM), same CRITICAL/HIGH judgment on
genuinely severe items, and reliably follows the "output ONLY the final
tier line" instruction once the prompt asked for it explicitly."""

import asyncio

CONTAINER_NAME = "claude-discord-bridge"
TIMEOUT = 120


async def claude_oneshot(system_prompt: str, user_message: str) -> str:
    cmd = [
        "docker", "exec", CONTAINER_NAME,
        "claude", "-p", user_message,
        "--append-system-prompt", system_prompt,
        "--dangerously-skip-permissions",
        "--output-format", "json",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
    except asyncio.TimeoutError:
        raise RuntimeError(f"claude_oneshot timed out after {TIMEOUT}s")
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {stderr.decode(errors='replace')[:300]}")
    import json
    data = json.loads(stdout.decode(errors="replace"))
    return data.get("result") or ""
