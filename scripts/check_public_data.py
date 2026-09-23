"""Check that the public CSVs keep the promises made in README and data/README."""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUSES = {"reachable_related", "unreachable", "reachable_unrelated", "redirected_domain", "unknown"}
ALWAYS_EMPTY = {
    "data/udc_projects.csv": ["team", "evidence_snippet", "cause_of_death", "cause_resolved"],
    "data/udc_url_candidates.csv": ["team", "source_record"],
}


def load(name: str) -> list[dict[str, str]]:
    with (ROOT / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    errors: list[str] = []
    for name, columns in ALWAYS_EMPTY.items():
        for line, row in enumerate(load(name), 2):
            for column in columns:
                if row.get(column):
                    errors.append(f"{name}:{line}: {column} must be empty in the public dataset")

    projects = load("data/udc_projects.csv")
    counts = Counter(row["url_status"] for row in projects)
    for status in set(counts) - STATUSES:
        errors.append(f"undocumented url_status: {status}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    expected = {
        "受賞記録": len(projects),
        "明示的な公開URLあり": sum(1 for row in projects if row["primary_url"]),
        "GitHub URLあり": sum(1 for row in projects if row["github_url"]),
        **{f"`{status}`": counts[status] for status in STATUSES},
    }
    for label, value in expected.items():
        match = re.search(rf"^\| {re.escape(label)} \| (\d+) \|$", readme, re.MULTILINE)
        if not match or int(match.group(1)) != value:
            errors.append(f"README snapshot row {label!r} should be {value}")

    for error in errors:
        print(error, file=sys.stderr)
    print(f"checked {len(projects)} project rows: {'FAILED' if errors else 'ok'}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
