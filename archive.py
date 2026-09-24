"""Writes curated (not raw-firehose) findings to the intel-archive repo:
confirmed threat-intel hits and MEDIUM+ escalation items. Commits and
pushes each write immediately - volume here should be low by design (only
real hits/notable items, not everything that passes through the feed), so
per-event commits are fine."""

import asyncio
import json
import subprocess
import time
from pathlib import Path

REPO_DIR = Path("/home/user/projects/intel-archive")
_write_lock = asyncio.Lock()


def _month_file(subdir: str) -> Path:
    stamp = time.strftime("%Y-%m")
    return REPO_DIR / subdir / f"{stamp}.jsonl"


def _git(*args: str) -> None:
    subprocess.run(["git", "-C", str(REPO_DIR), *args], check=True, capture_output=True)


async def _append_and_push(subdir: str, record: dict, commit_prefix: str) -> None:
    async with _write_lock:
        try:
            path = _month_file(subdir)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            rel_path = path.relative_to(REPO_DIR)
            _git("add", str(rel_path))
            _git("commit", "-m", f"{commit_prefix}: {record.get('indicator') or record.get('summary', '')[:60]}")
            _git("push", "origin", "main")
        except Exception as e:
            print(f"[archive] failed to write/push: {e!r}", flush=True)


async def log_ioc_hit(indicator: str, kind: str, verdict: str, message_link: str | None = None) -> None:
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "indicator": indicator,
        "type": kind,
        "verdict": verdict,
        "source": message_link,
    }
    await _append_and_push("iocs", record, "IOC hit")


async def log_notable(tier: str, summary: str, message_link: str | None = None) -> None:
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tier": tier,
        "summary": summary,
        "source": message_link,
    }
    await _append_and_push("notable", record, f"{tier} item")


def _safe_slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in text)[:80]


async def log_rule_update(source_name: str, rule_path: str, diff_content: str) -> None:
    """Rule bodies (Sigma/YARA/etc diffs) get their own file per event, not
    a JSONL log - these are meant to be read/reused directly, not just
    logged as a record."""
    async with _write_lock:
        try:
            stamp = time.strftime("%Y-%m-%d_%H%M%S")
            filename = f"{stamp}_{_safe_slug(source_name)}_{_safe_slug(rule_path)}.diff"
            path = REPO_DIR / "rules" / filename
            path.parent.mkdir(exist_ok=True)
            path.write_text(diff_content, encoding="utf-8")
            rel_path = path.relative_to(REPO_DIR)
            _git("add", str(rel_path))
            _git("commit", "-m", f"Rule update: {source_name} - {rule_path}")
            _git("push", "origin", "main")
        except Exception as e:
            print(f"[archive] failed to write/push rule: {e!r}", flush=True)
