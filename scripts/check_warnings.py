#!/usr/bin/env python3
"""Compare pytest warning counts against a committed baseline (no new warning debt)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINE_JSON = ROOT / "reports" / "validation" / "warning_baseline.json"
BASELINE_MD = ROOT / "reports" / "validation" / "warning_baseline.md"


def _repo_rel_path(filename: str | None) -> str:
    if not filename:
        return "unknown"
    try:
        return str(Path(filename).resolve().relative_to(ROOT))
    except ValueError:
        return filename


class WarningRecorder:
    """Pytest plugin: record each warning with category and source file."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def pytest_warning_recorded(self, warning_message, when, nodeid, location) -> None:
        cat = getattr(warning_message.category, "__name__", str(warning_message.category))
        fpath = _repo_rel_path(warning_message.filename)
        self.items.append((cat, fpath))


def collect_warnings() -> dict:
    """Run pytest and return aggregated warning counts."""
    os.environ.setdefault("AI_PROVIDER", "mock")
    os.environ.setdefault("LEDGERMIND_AUTH_DISABLED", "1")

    recorder = WarningRecorder()
    exit_code = pytest.main(["-q", "app/tests"], plugins=[recorder])
    if exit_code != 0:
        print(f"pytest failed with exit code {exit_code}", file=sys.stderr)
        sys.exit(exit_code)

    by_category = Counter(cat for cat, _ in recorder.items)
    by_file = Counter(fpath for _, fpath in recorder.items)
    return {
        "version": 1,
        "recorded_at": date.today().isoformat(),
        "total": len(recorder.items),
        "by_category": dict(sorted(by_category.items())),
        "by_file": dict(sorted(by_file.items(), key=lambda item: (-item[1], item[0]))),
    }


def load_baseline() -> dict:
  if not BASELINE_JSON.is_file():
    print(f"Missing baseline: {BASELINE_JSON}", file=sys.stderr)
    print("Run: python scripts/check_warnings.py --update-baseline", file=sys.stderr)
    sys.exit(1)
  with BASELINE_JSON.open(encoding="utf-8") as fh:
    return json.load(fh)


def compare(current: dict, baseline: dict) -> list[str]:
  """Return human-readable regressions (empty if within baseline)."""
  regressions: list[str] = []

  if current["total"] > baseline["total"]:
    regressions.append(
      f"total warnings increased: {current['total']} > baseline {baseline['total']} "
      f"(+{current['total'] - baseline['total']})"
    )

  base_cats = baseline.get("by_category") or {}
  for cat, count in current.get("by_category", {}).items():
    allowed = base_cats.get(cat, 0)
    if count > allowed:
      regressions.append(f"category {cat}: {count} > baseline {allowed} (+{count - allowed})")

  base_files = baseline.get("by_file") or {}
  for fpath, count in current.get("by_file", {}).items():
    allowed = base_files.get(fpath, 0)
    if count > allowed:
      regressions.append(f"file {fpath}: {count} > baseline {allowed} (+{count - allowed})")

  return regressions


def write_baseline_md(data: dict) -> None:
  lines = [
    "# Pytest warning baseline",
    "",
    f"**Recorded:** {data.get('recorded_at', 'unknown')}  ",
    f"**Total warnings:** {data['total']}",
    "",
    "Machine-readable baseline: `warning_baseline.json` (used by `scripts/check_warnings.py`).",
    "",
    "CI fails only when warning counts **increase** above this baseline. Fixing warnings",
    "and lowering counts is allowed; run `--update-baseline` after an intentional cleanup.",
    "",
    "## By category",
    "",
    "| Category | Count |",
    "|----------|------:|",
  ]
  for cat, count in sorted(data.get("by_category", {}).items(), key=lambda x: (-x[1], x[0])):
    lines.append(f"| `{cat}` | {count} |")

  lines.extend(["", "## By file (app source)", "", "| File | Count |", "|------|------:|"])
  for fpath, count in sorted(data.get("by_file", {}).items(), key=lambda x: (-x[1], x[0])):
    if not fpath.startswith("app/"):
      continue
    lines.append(f"| `{fpath}` | {count} |")

  lines.extend(
    [
      "",
      "## Primary debt",
      "",
      "Baseline was cleared after migrating `datetime.utcnow()` to `datetime.now(UTC)`.",
      "New warnings should not increase above the recorded baseline.",
      "",
      "## Commands",
      "",
      "```bash",
      "python scripts/check_warnings.py              # compare to baseline",
      "python scripts/check_warnings.py --update-baseline  # refresh after cleanup",
      "```",
    ]
  )
  BASELINE_MD.parent.mkdir(parents=True, exist_ok=True)
  BASELINE_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_baseline(data: dict) -> None:
  BASELINE_JSON.parent.mkdir(parents=True, exist_ok=True)
  with BASELINE_JSON.open("w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
  write_baseline_md(data)
  print(f"Updated {BASELINE_JSON}")
  print(f"Updated {BASELINE_MD}")


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--update-baseline",
    action="store_true",
    help="Record current warning counts as the new baseline (after intentional cleanup).",
  )
  args = parser.parse_args()

  print("Collecting pytest warnings…", file=sys.stderr)
  current = collect_warnings()
  print(f"Collected {current['total']} warnings", file=sys.stderr)

  if args.update_baseline:
    update_baseline(current)
    return

  baseline = load_baseline()
  regressions = compare(current, baseline)
  if regressions:
    print("WARNING REGRESSION — counts increased above baseline:", file=sys.stderr)
    for line in regressions:
      print(f"  - {line}", file=sys.stderr)
    print(
      f"\nCurrent total: {current['total']} | Baseline total: {baseline['total']}",
      file=sys.stderr,
    )
    sys.exit(1)

  delta = baseline["total"] - current["total"]
  if delta > 0:
    print(f"OK: {current['total']} warnings ({delta} below baseline — debt reduced)")
  else:
    print(f"OK: {current['total']} warnings (matches baseline)")


if __name__ == "__main__":
  main()
