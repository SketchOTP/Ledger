#!/usr/bin/env python3
"""Generate VAPID keys for Web Push. Append output to .env."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.web_push import export_vapid_keypair  # noqa: E402  (path setup must precede import)


def main() -> int:
    public_key, private_pem = export_vapid_keypair()
    pem_one_line = private_pem.replace("\n", "\\n")
    print("# Add to .env for Web Push (PWA background notifications)")
    print(f"VAPID_PUBLIC_KEY={public_key}")
    print(f"VAPID_PRIVATE_KEY={pem_one_line}")
    print("VAPID_SUBJECT=mailto:you@example.com")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
