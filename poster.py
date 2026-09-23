import asyncio
import httpx

DISCORD_MSG_LIMIT = 2000
MAX_RETRIES = 3


async def post_to_webhook(webhook_url: str, content: str, username: str, avatar_url: str | None):
    payload = {"content": content[:DISCORD_MSG_LIMIT], "username": username}
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
