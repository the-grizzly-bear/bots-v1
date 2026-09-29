import asyncio
import httpx

DISCORD_MSG_LIMIT = 2000
MAX_RETRIES = 3


def _truncate_at_boundary(content: str, limit: int) -> str:
    """A bare content[:limit] slice chops mid-word whenever a reply runs
    long (caught live: Marcus's reply ended mid-sentence at '...why it
    might b' - the real generated text was complete, this just hacked it
    off at the 2000th character). Back up to the last sentence end, or
    failing that the last word break, inside the limit instead."""
    if len(content) <= limit:
        return content
    # room for an ellipsis marker so it's visibly cut, not silently wrong
    truncated = content[:limit - 1]
    for boundary in (". ", "! ", "? ", "\n"):
        idx = truncated.rfind(boundary)
        if idx != -1 and idx > limit * 0.5:
            return truncated[:idx + 1].rstrip() + "…"
    idx = truncated.rfind(" ")
    if idx > limit * 0.5:
        return truncated[:idx].rstrip() + "…"
    return truncated + "…"


async def post_to_webhook(webhook_url: str, content: str, username: str, avatar_url: str | None):
    payload = {"content": _truncate_at_boundary(content, DISCORD_MSG_LIMIT), "username": username}
    if avatar_url:
        payload["avatar_url"] = avatar_url
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(webhook_url, json=payload)
                if resp.status_code == 429:
                    retry_after = resp.json().get("retry_after", 1)
                    await asyncio.sleep(retry_after)
                    continue
                resp.raise_for_status()
                return
        except httpx.TimeoutException as e:
            last_error = e
            await asyncio.sleep(1)
    raise last_error or RuntimeError("post_to_webhook failed after retries")
