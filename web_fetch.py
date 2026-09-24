import asyncio
import base64
import ipaddress
import os
import re
import socket
import time
from urllib.parse import urlparse

import httpx

FETCH_TIMEOUT = 15
MAX_CONTENT_CHARS = 4000
MAX_RESPONSE_BYTES = 5_000_000  # 5MB - refuse to buffer a huge/decompression-bomb response
MAX_REDIRECTS = 5
CACHE_TTL = 120

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
VISION_TIMEOUT = 60
VISION_PROMPT = (
    "Describe this image factually: transcribe any visible text exactly, "
    "note any charts/numbers/data, and describe the key subject. Be "
    "concise but complete - this description is the only way anyone else "
    "will know what's in the image."
)

# Every persona reacting to the same item independently calls fetch_url on
# the same link (up to 7x per item in practice) - this cache collapses those
# into one real fetch: concurrent callers share the in-flight request, and a
# short-lived result cache covers the (usually few seconds) gap between the
# initial round and the reaction pass.
_cache: dict[str, tuple[float, str]] = {}
_in_flight: dict[str, asyncio.Future] = {}

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_BLOCK_BREAK_RE = re.compile(r"<(br|p|div|li|h[1-6])[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANKLINES_RE = re.compile(r"\n{3,}")
_ENTITIES = {
    "&nbsp;": " ", "&amp;": "&", "&#39;": "'", "&apos;": "'",
    "&quot;": '"', "&lt;": "<", "&gt;": ">",
}

_META_TAG_RE = re.compile(r"<meta\s+([^>]*)>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"|([\w:-]+)\s*=\s*\'([^\']*)\'')
_OG_KEYS = ("og:title", "twitter:title", "og:description", "twitter:description")


def _decode_entities(text: str) -> str:
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    return text


def _extract_og_meta(html: str) -> str:
    """Pages built for social-media embeds (fxtwitter and similar) often
    ship an empty <body> with the real summary only in OpenGraph/Twitter
    meta tags - without this, those pages look 'broken' or content-free to
    the body-text extractor even though the link is completely valid."""
    found = {}
    for tag_match in _META_TAG_RE.finditer(html):
        attrs = {}
        for m in _ATTR_RE.finditer(tag_match.group(1)):
            key = (m.group(1) or m.group(3)).lower()
            val = m.group(2) if m.group(1) else m.group(4)
            attrs[key] = val
        key = attrs.get("property", "").lower() or attrs.get("name", "").lower()
        if key in _OG_KEYS and "content" in attrs and key not in found:
            value = _decode_entities(attrs["content"])
            value = _BLOCK_BREAK_RE.sub("\n", value)
            found[key] = _TAG_RE.sub(" ", value).strip()
    return "\n".join(dict.fromkeys(found[k] for k in _OG_KEYS if k in found))


def _html_to_text(html: str) -> str:
    html_stripped = _SCRIPT_STYLE_RE.sub(" ", html)
    body_only = re.sub(r"^.*?<body[^>]*>", "", html_stripped, flags=re.IGNORECASE | re.DOTALL)
    text = _BLOCK_BREAK_RE.sub("\n", body_only)
    text = _TAG_RE.sub(" ", text)
    text = _decode_entities(text)
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n"))
    text = _BLANKLINES_RE.sub("\n\n", "\n".join(lines)).strip()
    if len(text) < 30:
        og_text = _extract_og_meta(html)
        if og_text:
            return og_text
    return text


async def _describe_image(image_bytes: bytes, url: str) -> str:
    """Route image bytes to a vision model instead of refusing them outright -
    personas otherwise have no way to know what a chart, screenshot, or
    diagram in a linked post actually shows."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    try:
        async with httpx.AsyncClient(timeout=VISION_TIMEOUT) as client:
            resp = await client.post(OLLAMA_URL, json={
                "model": VISION_MODEL,
                "messages": [{"role": "user", "content": VISION_PROMPT, "images": [b64]}],
                "stream": False,
            })
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
    except Exception as e:
        return f"Error describing image at {url}: {e}"


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Refuse anything that isn't a plain http(s) request to a public
    address. The persona's own fetch target comes from feed content we don't
    control, so a malicious or compromised feed item could point it at
    internal infrastructure (localhost, the LAN, cloud metadata endpoints)
    instead of a real public URL - checked before every fetch AND every
    redirect hop, not just the initial URL."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Malformed URL."
    if parsed.scheme not in ("http", "https"):
        return False, f"Refusing non-http(s) scheme '{parsed.scheme}'."
    hostname = parsed.hostname
    if not hostname:
        return False, "URL has no hostname."
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        return False, f"Could not resolve host: {e}"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            return False, f"Refusing to fetch internal/private address ({ip})."
    return True, ""


async def fetch_url_content(url: str) -> str:
    now = time.time()
    cached = _cache.get(url)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    in_flight = _in_flight.get(url)
    if in_flight is not None:
        return await in_flight

    future = asyncio.get_event_loop().create_future()
    _in_flight[url] = future
    try:
        result = await _fetch_url_uncached(url)
        future.set_result(result)
        _cache[url] = (now, result)
        return result
    finally:
        _in_flight.pop(url, None)


async def _fetch_url_uncached(url: str) -> str:
    """Fetch a URL directly and return its readable text, truncated. No
    search engine, no JS rendering, no browser session - just reads the
    actual page a news item already links to, which is what personas were
    missing when they speculated about vague terminology instead of reading
    the source one click away.

    Redirects are followed manually (not via httpx's follow_redirects) so
    every hop gets the same internal-address check as the original URL -
    otherwise a safe-looking link could 302 straight into an SSRF."""
    ok, reason = _is_safe_url(url)
    if not ok:
        return reason

    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=FETCH_TIMEOUT) as client:
            for _ in range(MAX_REDIRECTS):
                async with client.stream("GET", url, headers={"User-Agent": "bots-v1/1.0"}) as resp:
                    if resp.is_redirect:
                        next_url = resp.headers.get("location")
                        if not next_url:
                            return f"Redirect from {url} had no Location header."
                        url = str(resp.next_request.url) if resp.next_request else next_url
                        ok, reason = _is_safe_url(url)
                        if not ok:
                            return reason
                        continue

                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type", "")
                    body = b""
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_RESPONSE_BYTES:
                            return f"Response from {url} exceeded {MAX_RESPONSE_BYTES} bytes, refusing to read further."
                    break
            else:
                return f"Too many redirects fetching {url}."
    except Exception as e:
        return f"Error fetching {url}: {e}"

    if content_type.startswith("image/"):
        return await _describe_image(body, url)

    raw_text = body.decode(resp.encoding or "utf-8", errors="replace")
    if "html" in content_type:
        text = _html_to_text(raw_text)
    elif "text/plain" in content_type or "json" in content_type or not content_type:
        text = raw_text
    else:
        return f"Cannot read non-text content type '{content_type}' from {url}"

    if not text.strip():
        return f"No readable text content found at {url}"
    if len(text) > MAX_CONTENT_CHARS:
        text = text[:MAX_CONTENT_CHARS] + "\n...[truncated]"
    return text
