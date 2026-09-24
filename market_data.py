import httpx

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
TIMEOUT = 10


async def get_ticker_context(ticker: str) -> str:
    """Real price/company context for a ticker symbol - free, no key, no
    auth. Exists because raw ticker alerts (e.g. from unusual_whales) give
    no context at all, and tickers get reassigned to unrelated companies
    after mergers/delistings - PARA used to be Paramount, it's since been
    reassigned to an unrelated microcap, which caused real confusion before
    this existed."""
    ticker = ticker.strip().upper().lstrip("$")
    if not ticker or not ticker.replace(".", "").isalnum():
        return f"'{ticker}' doesn't look like a valid ticker symbol."

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                YAHOO_CHART_URL.format(ticker=ticker),
                params={"interval": "1d", "range": "5d"},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return f"Error fetching ticker data for {ticker}: {e}"

    result = data.get("chart", {}).get("result")
    if not result:
        error = data.get("chart", {}).get("error", {})
        return f"No data found for ticker '{ticker}' - {error.get('description', 'it may not exist or may be delisted')}."

    meta = result[0]["meta"]
    name = meta.get("longName") or meta.get("shortName") or ticker
    price = meta.get("regularMarketPrice")
    change_pct = meta.get("regularMarketChangePercent")
    volume = meta.get("regularMarketVolume")
    day_low = meta.get("regularMarketDayLow")
    day_high = meta.get("regularMarketDayHigh")
    week52_low = meta.get("fiftyTwoWeekLow")
    week52_high = meta.get("fiftyTwoWeekHigh")
    exchange = meta.get("fullExchangeName", "")

    lines = [f"{ticker} = {name} ({exchange})"]
    if price is not None:
        change_str = f"{change_pct:+.2f}%" if change_pct is not None else "unknown change"
        lines.append(f"Price: ${price:.2f} ({change_str})")
    if volume is not None:
        lines.append(f"Volume: {volume:,}")
    if day_low is not None and day_high is not None:
        lines.append(f"Day range: ${day_low:.2f} - ${day_high:.2f}")
    if week52_low is not None and week52_high is not None:
        lines.append(f"52-week range: ${week52_low:.2f} - ${week52_high:.2f}")

    return "\n".join(lines)
