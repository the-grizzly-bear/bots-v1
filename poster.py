import httpx

DISCORD_MSG_LIMIT = 2000


async def post_to_webhook(webhook_url: str, content: str, username: str, avatar_url: str | None):
    payload = {"content": content[:DISCORD_MSG_LIMIT], "username": username}
    if avatar_url:
        payload["avatar_url"] = avatar_url
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(webhook_url, json=payload)
        resp.raise_for_status()
