#!/usr/bin/env python3
"""Summarize a UDC project CSV without network access."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args()

    with args.csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    statuses = Counter(row.get("url_status", "") or "(blank)" for row in rows)
    years = sorted({row.get("year", "") for row in rows if row.get("year", "")})
    with_primary = sum(bool(row.get("primary_url", "").strip()) for row in rows)
    with_github = sum(bool(row.get("github_url", "").strip()) for row in rows)

    print(f"records: {len(rows)}")
    if years:
        print(f"years: {years[0]}-{years[-1]}")
    print(f"with_primary_url: {with_primary}")
    print(f"with_github_url: {with_github}")
    print("status:")
    for status, count in sorted(statuses.items()):
        print(f"  {status}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
