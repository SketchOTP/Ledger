#!/usr/bin/env python3
"""Call POST /api/scheduler/run-due on an interval (local appliance loop)."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INTERVAL_SEC = 900  # 15 minutes
DEFAULT_BACKOFF_SEC = 60
MAX_BACKOFF_SEC = 900

logger = logging.getLogger("ledgermind.scheduler_loop")


def load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def api_base_url() -> str:
    port = os.environ.get("API_PORT", "9543")
    host = os.environ.get("API_HOST", "127.0.0.1")
    return os.environ.get("LEDGERMIND_API_URL", f"http://{host}:{port}").rstrip("/")


def run_due_url() -> str:
    return f"{api_base_url()}/api/scheduler/run-due"


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("SCHEDULER_API_TOKEN") or os.environ.get("LM_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def call_run_due() -> dict:
    url = run_due_url()
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={"Content-Type": "application/json", **_auth_headers()},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    load_dotenv()
    interval = int(os.environ.get("SCHEDULER_LOOP_INTERVAL_SEC", DEFAULT_INTERVAL_SEC))
    backoff = DEFAULT_BACKOFF_SEC

    logger.info("Scheduler loop starting; interval=%ss url=%s", interval, run_due_url())
    logger.info("Advisory jobs only — no offer approval or money movement")

    while True:
        try:
            result = call_run_due()
            ran = result.get("ran", 0)
            logger.info("run-due ok: ran=%s", ran)
            backoff = DEFAULT_BACKOFF_SEC
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            logger.error("run-due HTTP %s: %s", exc.code, body)
            backoff = min(backoff * 2, MAX_BACKOFF_SEC)
        except urllib.error.URLError as exc:
            logger.error("API unavailable: %s — backing off %ss", exc.reason, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SEC)
            continue
        except Exception as exc:
            logger.exception("run-due failed: %s", exc)
            backoff = min(backoff * 2, MAX_BACKOFF_SEC)

        time.sleep(interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        logger.info("Scheduler loop stopped")
        raise SystemExit(0) from None
