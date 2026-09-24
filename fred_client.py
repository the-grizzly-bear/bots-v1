import os

import httpx

FRED_API_KEY = os.environ.get("FRED_API_KEY")
BASE_URL = "https://api.stlouisfed.org/fred"
TIMEOUT = 10


async def get_fred_series(query: str) -> str:
    """Real macro/economic data from FRED (St. Louis Fed) - CPI, unemployment,
    Fed funds rate, GDP, yields, etc. Personas rarely know an exact FRED
    series id (e.g. CPIAUCSL), so this takes either a real series id or a
    free-text query: it tries the query as a literal series id first (fast
    path, no extra request), and falls back to FRED's own series search if
    that doesn't resolve."""
    query = query.strip()
    if not query:
        return "No series or query given."
    if not FRED_API_KEY:
        return "FRED_API_KEY not configured."

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            series_id = query.upper() if query.replace("_", "").isalnum() else None
            title = None

            if series_id:
                resp = await client.get(f"{BASE_URL}/series", params={
                    "series_id": series_id, "api_key": FRED_API_KEY, "file_type": "json",
                })
                if resp.status_code == 200:
                    matches = resp.json().get("seriess") or []
                    if matches:
                        title = matches[0]["title"]
                        units = matches[0].get("units", "")
                        freq = matches[0].get("frequency", "")

            if not title:
                resp = await client.get(f"{BASE_URL}/series/search", params={
                    "search_text": query, "api_key": FRED_API_KEY, "file_type": "json", "limit": 1,
                })
                resp.raise_for_status()
                matches = resp.json().get("seriess") or []
                if not matches:
                    return f"No FRED series found matching '{query}'."
                series_id = matches[0]["id"]
                title = matches[0]["title"]
                units = matches[0].get("units", "")
                freq = matches[0].get("frequency", "")

            obs_resp = await client.get(f"{BASE_URL}/series/observations", params={
                "series_id": series_id, "api_key": FRED_API_KEY, "file_type": "json",
                "sort_order": "desc", "limit": 1,
            })
            obs_resp.raise_for_status()
            observations = obs_resp.json().get("observations") or []
    except Exception as e:
        return f"Error fetching FRED data for '{query}': {e}"

    if not observations:
        return f"{title} ({series_id}) - no observations available."

    latest = observations[0]
    return (
        f"{title} ({series_id})\n"
        f"Latest: {latest['value']} {units} as of {latest['date']} ({freq})"
    )
