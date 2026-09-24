"""Unusual Whales data access via its internal web-app API (no official
public API on this account). Auth works by replaying the static
Authorization/uw-sh headers a logged-in browser session uses - verified via
HAR capture that uw-sh stays constant across hundreds of requests and many
pages, i.e. it's a session-scoped value, not a per-request signature. Same
approach already proven in ~/projects/agent-dashboard/backend/uw_client.py -
this is a bots-v1-local copy reading credentials from .env instead of a
settings DB.

Credentials: UW_BEARER_TOKEN / UW_SH in .env. Re-capture via a fresh HAR
export from a logged-in browser session if they ever stop working (session
expiry, logout, etc.) - same account/session as agent-dashboard uses."""

import asyncio
import os

import httpx

BASE_URL = "https://phx.unusualwhales.com/api"


def _headers() -> dict:
    return {
        "authorization": f"Bearer {os.environ.get('UW_BEARER_TOKEN', '')}",
        "accept": "application/json",
        "origin": "https://unusualwhales.com",
        "referer": "https://unusualwhales.com/",
        "uw-path": "/live-options-flow",
        "uw-sh": os.environ.get("UW_SH", ""),
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    }


async def _get(path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=15, headers=_headers()) as client:
        resp = await client.get(f"{BASE_URL}{path}", params=params)
        resp.raise_for_status()
        return resp.json()


async def option_flow(limit: int = 200) -> list[dict]:
    data = await _get("/option_trades_v2", {"limit": limit, "order": "Time"})
    return data.get("data", [])


def filter_by_ticker(trades: list[dict], ticker: str) -> list[dict]:
    ticker = ticker.upper()
    return [t for t in trades if t.get("underlying_symbol") == ticker]


async def dark_pool(ticker: str, limit: int = 20) -> list[dict]:
    data = await _get("/flow/dark-pool", {"ticker_symbol": ticker.upper(), "limit": limit})
    return data.get("trades", [])


async def company_info(ticker: str) -> dict:
    data = await _get(f"/companies/{ticker.upper()}", {"thin": "true"})
    return data.get("company", {})


async def max_pain(ticker: str) -> dict:
    return await _get(f"/max-pain/{ticker.upper()}")


async def gex(ticker: str) -> dict:
    return await _get(f"/gex/{ticker.upper()}")


async def news(ticker: str, limit: int = 5) -> list[dict]:
    data = await _get("/news/headlines-feed/ticker-search", {"ticker": ticker.upper()})
    items = data if isinstance(data, list) else data.get("data", [])
    return items[:limit]


async def ticker_snapshot(ticker: str) -> str:
    """Real options flow / dark pool / GEX context for a ticker - this is
    what actually explains an 'unusual' move, versus get_ticker_context's
    plain price/company-name lookup."""
    ticker = ticker.upper()
    if not os.environ.get("UW_BEARER_TOKEN"):
        return "Unusual Whales credentials not configured (UW_BEARER_TOKEN/UW_SH missing from .env)."
    results = await asyncio.gather(
        company_info(ticker), max_pain(ticker), gex(ticker),
        dark_pool(ticker, limit=10), option_flow(limit=300), news(ticker, limit=3),
        return_exceptions=True,
    )
    info, mp, gx, dp, flow, hl = [r if not isinstance(r, BaseException) else None for r in results]
    return format_snapshot(ticker, info, mp, gx, dp, flow, hl)


def format_snapshot(ticker, info, mp, gx, dp, flow, headlines) -> str:
    lines = [f"Unusual Whales data for {ticker}:"]

    if isinstance(info, dict) and info:
        lines.append(
            f"- {info.get('full_name', ticker)} | marketcap ${info.get('marketcap', '?')} | "
            f"P/E {info.get('pe_ratio', '?')} | next earnings {info.get('next_earnings_date', '?')}"
        )
    if isinstance(mp, dict) and mp.get("date"):
        lines.append(f"- Max pain date {mp.get('date')}")
    if isinstance(gx, dict) and gx:
        lines.append(f"- GEX data: {str(gx)[:300]}")
    if isinstance(dp, list) and dp:
        total_prem = sum(float(t.get("premium", 0)) for t in dp)
        lines.append(f"- Dark pool: {len(dp)} recent prints, ${total_prem:,.0f} total premium")
    if isinstance(flow, list):
        matches = filter_by_ticker(flow, ticker)
        if matches:
            calls = sum(1 for t in matches if t.get("option_type") == "call")
            puts = len(matches) - calls
            total_prem = sum(float(t.get("premium", 0)) for t in matches)
            lines.append(
                f"- Options flow (last {len(flow)} market-wide prints): {len(matches)} for {ticker} "
                f"({calls} calls / {puts} puts), ${total_prem:,.0f} total premium"
            )
    if isinstance(headlines, list) and headlines:
        for h in headlines[:3]:
            title = h.get("headline") or h.get("title")
            if title:
                lines.append(f"- Headline: {title}")

    if len(lines) == 1:
        return f"No Unusual Whales data could be retrieved for {ticker}."
    return "\n".join(lines)
