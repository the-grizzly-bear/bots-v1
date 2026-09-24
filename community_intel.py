"""Community-discussion corroboration - free, no key. Distinct from
threat_intel.py's IOC checks: this answers 'has the tech community actually
engaged with this story/URL', a general signal rather than a
threat-specific one."""

import httpx

TIMEOUT = 15
HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"


async def check_hn_discussion(query: str) -> str:
    """query can be a URL (searched as-is) or a topic/keyword phrase."""
    query = query.strip()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(HN_SEARCH_URL, params={"query": query, "tags": "story", "hitsPerPage": 5})
            r.raise_for_status()
            d = r.json()
    except Exception as e:
        return f"Hacker News: error ({e})."

    hits = d.get("hits", [])
    if not hits:
        return f"Hacker News: no discussion found for '{query}'."

    lines = [f"Hacker News discussion for '{query}':"]
    for h in hits:
        title = h.get("title", "?")
        points = h.get("points", 0)
        comments = h.get("num_comments", 0)
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        lines.append(f"- \"{title}\" ({points} points, {comments} comments) {url}")
    return "\n".join(lines)
