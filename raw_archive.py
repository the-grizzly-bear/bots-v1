"""Complete, unfiltered capture of every watched-channel post (intel-v4's
#news plus the external unusual_whales feeds) into intel-archive/raw/.
Unlike archive.py's curated logs, this is high-volume by design, so writes
are local-only (cheap, no network) and a periodic background task batches
the accumulated changes into one commit+push instead of one per item."""

import asyncio
import json
import subprocess
import time
from pathlib import Path

REPO_DIR = Path("/home/user/projects/intel-archive")
FLUSH_INTERVAL_SECONDS = 300  # batch commits every 5 minutes, not per-item

_pending = False
_write_lock = asyncio.Lock()


def _day_file() -> Path:
    stamp = time.strftime("%Y-%m-%d")
    return REPO_DIR / "raw" / f"{stamp}.jsonl"


async def _git(*args: str) -> None:
    # See archive.py's _git() for why this runs off the event loop - same
    # blocking-subprocess-over-network issue applies here too.
    await asyncio.to_thread(
        subprocess.run, ["git", "-C", str(REPO_DIR), *args], check=True, capture_output=True
    )


async def log_raw_post(channel_name: str, author: str, content: str) -> None:
    global _pending
    async with _write_lock:
        try:
            path = _day_file()
            path.parent.mkdir(exist_ok=True)
            record = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "channel": channel_name,
                "author": author,
                "content": content,
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            _pending = True
        except Exception as e:
            print(f"[raw_archive] failed to write: {e!r}", flush=True)


async def _flush() -> None:
    global _pending
    async with _write_lock:
        if not _pending:
            return
        try:
            await _git("add", "raw/")
            status = await asyncio.to_thread(
                subprocess.run,
                ["git", "-C", str(REPO_DIR), "status", "--porcelain"],
                capture_output=True, text=True, check=True,
            )
            if not status.stdout.strip():
                _pending = False
                return
            count = len(status.stdout.strip().splitlines())
            await _git("commit", "-m", f"Raw capture batch ({time.strftime('%Y-%m-%d %H:%M UTC')})")
            await _git("push", "origin", "main")
            print(f"[raw_archive] flushed batch, {count} file(s) changed", flush=True)
            _pending = False
        except Exception as e:
            print(f"[raw_archive] failed to flush: {e!r}", flush=True)


async def flush_loop() -> None:
    while True:
        await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
        await _flush()
