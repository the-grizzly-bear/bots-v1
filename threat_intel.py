"""Threat-intel indicator lookup across the user's own registered
services: Shodan, VirusTotal, urlscan.io, and abuse.ch's suite (ThreatFox,
MalwareBazaar, URLhaus, YARAify, SSLBL, Feodo Tracker). One entry point
(check_indicator) auto-detects what kind of indicator it's given (IP,
domain, URL, or hash) and queries whichever services actually apply,
concurrently, then returns one combined readable report."""

import asyncio
import ipaddress
import os
import re
import time

import httpx

TIMEOUT = 15
BLOCKLIST_CACHE_TTL = 600  # feodo/sslbl are bulk downloads, not per-query lookups

_HASH_RE = re.compile(r"^[a-fA-F0-9]{32}$|^[a-fA-F0-9]{40}$|^[a-fA-F0-9]{64}$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}$")
_CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE)

_blocklist_cache: dict[str, tuple[float, list]] = {}


def _classify(indicator: str) -> str:
    indicator = indicator.strip()
    if _CVE_RE.match(indicator):
        return "cve"
    try:
        ipaddress.ip_address(indicator)
        return "ip"
    except ValueError:
        pass
    if _URL_RE.match(indicator):
        return "url"
    if _HASH_RE.match(indicator):
        return "hash"
    if _DOMAIN_RE.match(indicator):
        return "domain"
    return "unknown"


async def _get_blocklist(url: str) -> list:
    now = time.time()
    cached = _blocklist_cache.get(url)
    if cached and now - cached[0] < BLOCKLIST_CACHE_TTL:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []
    _blocklist_cache[url] = (now, data)
    return data


# ---- Shodan ----

async def _shodan_host(ip: str) -> str | None:
    key = os.environ.get("shodan")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"https://api.shodan.io/shodan/host/{ip}", params={"key": key})
            if r.status_code == 404:
                return "Shodan: no data (not indexed)."
            r.raise_for_status()
            d = r.json()
    except Exception as e:
        return f"Shodan: error ({e})."
    ports = d.get("ports", [])
    org = d.get("org", "unknown org")
    vulns = d.get("vulns", [])
    line = f"Shodan: {org}, {d.get('country_name', '?')}, open ports: {ports}"
    if vulns:
        line += f", KNOWN VULNS: {list(vulns)[:10]}"
    return line


# ---- VirusTotal ----

async def _vt(endpoint: str, resource: str) -> str | None:
    key = os.environ.get("virustotal")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(
                f"https://www.virustotal.com/api/v3/{endpoint}/{resource}",
                headers={"x-apikey": key},
            )
            if r.status_code == 404:
                return "VirusTotal: not found in their dataset."
            r.raise_for_status()
            attrs = r.json()["data"]["attributes"]
    except Exception as e:
        return f"VirusTotal: error ({e})."
    stats = attrs.get("last_analysis_stats", {})
    malicious = stats.get("malicious", 0)
    suspicious = stats.get("suspicious", 0)
    total = sum(stats.values()) or 1
    verdict = "CLEAN" if malicious == 0 and suspicious == 0 else f"{malicious + suspicious}/{total} engines flag this"
    return f"VirusTotal: {verdict} ({stats})"


async def _vt_url(url: str) -> str | None:
    """VT's URL endpoint needs a base64(no-padding) id derived from the URL."""
    import base64
    key = os.environ.get("virustotal")
    if not key:
        return None
    url_id = base64.urlsafe_b64encode(url.encode()).decode().strip("=")
    return await _vt("urls", url_id)


# ---- urlscan.io ----

async def _urlscan_search(query: str) -> str | None:
    key = os.environ.get("urlscan")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(
                "https://urlscan.io/api/v1/search/",
                params={"q": query},
                headers={"API-Key": key},
            )
            r.raise_for_status()
            d = r.json()
    except Exception as e:
        return f"urlscan.io: error ({e})."
    total = d.get("total", 0)
    if not total:
        return "urlscan.io: no prior scans found."
    recent = d.get("results", [])[:3]
    urls = [r.get("page", {}).get("url", "?") for r in recent]
    return f"urlscan.io: {total} prior scans, recent: {urls}"


# ---- abuse.ch: ThreatFox / MalwareBazaar / URLhaus / YARAify ----

def _abuse_headers() -> dict:
    return {"Auth-Key": os.environ.get("abuse.ch", "")}


async def _threatfox(indicator: str) -> str | None:
    if not os.environ.get("abuse.ch"):
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                "https://threatfox-api.abuse.ch/api/v1/",
                json={"query": "search_ioc", "search_term": indicator},
                headers=_abuse_headers(),
            )
            r.raise_for_status()
            d = r.json()
    except Exception as e:
        return f"ThreatFox: error ({e})."
    if d.get("query_status") != "ok":
        return "ThreatFox: no match."
    data = d.get("data", [])[:3]
    entries = [f"{e.get('malware_printable', '?')} ({e.get('threat_type', '?')})" for e in data]
    return f"ThreatFox: MATCH - {entries}"


async def _malwarebazaar_hash(file_hash: str) -> str | None:
    if not os.environ.get("abuse.ch"):
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                "https://mb-api.abuse.ch/api/v1/",
                data={"query": "get_info", "hash": file_hash},
                headers=_abuse_headers(),
            )
            r.raise_for_status()
            d = r.json()
    except Exception as e:
        return f"MalwareBazaar: error ({e})."
    if d.get("query_status") != "ok":
        return "MalwareBazaar: no match."
    info = d.get("data", [{}])[0]
    return f"MalwareBazaar: MATCH - {info.get('signature', 'unknown signature')}, file type {info.get('file_type', '?')}"


async def _urlhaus(indicator: str, is_url: bool) -> str | None:
    if not os.environ.get("abuse.ch"):
        return None
    endpoint = "url" if is_url else "host"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                f"https://urlhaus-api.abuse.ch/v1/{endpoint}/",
                data={endpoint: indicator},
                headers=_abuse_headers(),
            )
            r.raise_for_status()
            d = r.json()
    except Exception as e:
        return f"URLhaus: error ({e})."
    if d.get("query_status") not in ("ok",):
        return "URLhaus: no match."
    threat = d.get("threat") or d.get("urls", [{}])[0].get("threat", "unknown threat")
    return f"URLhaus: MATCH - {threat}"


async def _yaraify_hash(file_hash: str) -> str | None:
    if not os.environ.get("abuse.ch"):
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                "https://yaraify-api.abuse.ch/api/v1/",
                json={"query": "lookup_hash", "search_term": file_hash},
                headers=_abuse_headers(),
            )
            r.raise_for_status()
            d = r.json()
    except Exception as e:
        return f"YARAify: error ({e})."
    if d.get("data") is None:
        return "YARAify: no match."
    data = d["data"]
    yara_rules = data.get("yara_rules", []) if isinstance(data, dict) else []
    rule_names = [y.get("rule_name", "?") for y in yara_rules[:5]]
    return f"YARAify: MATCH - matching rules: {rule_names or 'file known, no rule names returned'}"


async def _feodo_check(ip: str) -> str:
    data = await _get_blocklist("https://feodotracker.abuse.ch/downloads/ipblocklist.json")
    for entry in data:
        if entry.get("ip_address") == ip:
            return f"Feodo Tracker: MATCH - active {entry.get('malware', '?')} C2 server."
    return "Feodo Tracker: not a known botnet C2."


# ---- CISA KEV (actively-exploited CVEs - free, no key) ----

async def _cisa_kev(cve_id: str) -> str:
    data = await _get_blocklist("https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json")
    entries = data.get("vulnerabilities", []) if isinstance(data, dict) else []
    for entry in entries:
        if entry.get("cveID", "").upper() == cve_id.upper():
            return (
                f"CISA KEV: ACTIVELY EXPLOITED - added {entry.get('dateAdded')}, "
                f"due date {entry.get('dueDate')}, ransomware use: {entry.get('knownRansomwareCampaignUse', 'Unknown')}"
            )
    return "CISA KEV: not in the actively-exploited catalog (doesn't mean it's not exploited, just not confirmed/tracked)."


# ---- GreyNoise community (free, no key) ----

async def _greynoise(ip: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"https://api.greynoise.io/v3/community/{ip}")
            d = r.json()
    except Exception as e:
        return f"GreyNoise: error ({e})."
    if d.get("noise"):
        return f"GreyNoise: mass internet scanner ({d.get('classification', '?')}) - likely NOT a targeted threat, just background noise."
    if d.get("riot"):
        return "GreyNoise: known-benign common business service (RIOT)."
    return "GreyNoise: not observed mass-scanning the internet (doesn't rule out targeted activity)."


# ---- AlienVault OTX (free, no key needed for general lookups) ----

_OTX_TYPE_MAP = {"ip": "IPv4", "domain": "domain", "url": "url", "hash": "file"}


async def _otx(indicator: str, kind: str) -> str | None:
    otx_type = _OTX_TYPE_MAP.get(kind)
    if not otx_type:
        return None
    section = "general" if otx_type != "file" else "analysis"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"https://otx.alienvault.com/api/v1/indicators/{otx_type}/{indicator}/{section}")
            if r.status_code == 404:
                return "AlienVault OTX: no data."
            r.raise_for_status()
            d = r.json()
    except Exception as e:
        return f"AlienVault OTX: error ({e})."
    pulse_count = d.get("pulse_info", {}).get("count", 0)
    if not pulse_count:
        return "AlienVault OTX: not referenced in any community threat report (pulse)."
    names = [p.get("name", "?") for p in d.get("pulse_info", {}).get("pulses", [])[:3]]
    return f"AlienVault OTX: referenced in {pulse_count} community threat report(s): {names}"


# ---- Wayback Machine (domain/URL age context - free, no key) ----

async def _wayback_age(url_or_domain: str) -> str | None:
    target = url_or_domain if url_or_domain.startswith("http") else f"http://{url_or_domain}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get("http://archive.org/wayback/available", params={"url": target})
            d = r.json()
    except Exception as e:
        return f"Wayback Machine: error ({e})."
    snap = d.get("archived_snapshots", {}).get("closest")
    if not snap:
        return "Wayback Machine: no historical archive found - may be a brand-new domain."
    return f"Wayback Machine: earliest/closest archive from {snap.get('timestamp', '?')[:8]} - has some history."


# ---- Combined entry point ----

async def check_indicator(indicator: str) -> str:
    indicator = indicator.strip()
    kind = _classify(indicator)
    if kind == "unknown":
        return f"'{indicator}' doesn't look like a CVE ID, IP, domain, URL, or file hash - can't classify it."

    if kind == "cve":
        results = [await _cisa_kev(indicator)]
    elif kind == "ip":
        results = await asyncio.gather(
            _shodan_host(indicator), _vt("ip_addresses", indicator),
            _threatfox(indicator), _feodo_check(indicator),
            _greynoise(indicator), _otx(indicator, "ip"),
            return_exceptions=True,
        )
    elif kind == "domain":
        results = await asyncio.gather(
            _vt("domains", indicator), _urlscan_search(f"domain:{indicator}"),
            _threatfox(indicator), _otx(indicator, "domain"), _wayback_age(indicator),
            return_exceptions=True,
        )
    elif kind == "url":
        results = await asyncio.gather(
            _urlhaus(indicator, is_url=True), _urlscan_search(f'page.url:"{indicator}"'),
            _vt_url(indicator), _otx(indicator, "url"), _wayback_age(indicator),
            return_exceptions=True,
        )
    else:  # hash
        results = await asyncio.gather(
            _malwarebazaar_hash(indicator), _vt("files", indicator), _yaraify_hash(indicator),
            _otx(indicator, "hash"),
            return_exceptions=True,
        )

    lines = [f"Threat-intel check for {indicator} ({kind}):"]
    for r in results:
        if isinstance(r, BaseException):
            continue
        if r:
            lines.append(f"- {r}")
    if len(lines) == 1:
        return f"No threat-intel data available for {indicator} (no services configured or all failed)."
    return "\n".join(lines)
