#!/usr/bin/env python3
"""Explore unresolved UDC URLs using official supplementary materials.

The award pages are the source of the project rows.  This second pass only
looks for URLs that were published in official UDC archive/first-round pages or
in a public finalist sheet linked from an official UDC page.  Search-engine
results and unverified guesses are deliberately out of scope.

Candidates are written to a separate UTF-8-BOM CSV.  A candidate is copied to
the main project CSV only when its project name matches an unresolved row.  A
newly copied URL is then checked with the same fact-only checker used by the
collector.  Login is never attempted.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from collect_udc_projects import (
    CSV_COLUMNS,
    Checker,
    ProjectRecord,
    RateLimiter,
    RobotsCache,
    clean_text,
    github_repo_url,
    check_records,
)


OFFICIAL_HOSTS = {"urbandata-challenge.jp", "www.urbandata-challenge.jp"}

# These pages are linked from the UDC site or are the site's historical
# archive pages.  They are intentionally explicit: this is not a crawler.
HTML_SOURCES = [
    (2015, "official archive", "https://urbandata-challenge.jp/2015/2015-archives"),
    (2015, "official first-round results", "https://urbandata-challenge.jp/2015/1st-results"),
    (2016, "official archive", "https://urbandata-challenge.jp/2016/2016-archives"),
    (2016, "official first-round results", "https://urbandata-challenge.jp/2016/2016-1st-results"),
    (2017, "official archive", "https://urbandata-challenge.jp/2017/2017-archives"),
    (2017, "official first-round results", "https://urbandata-challenge.jp/2017/2017-1st-results"),
    (2018, "official first-round results", "https://urbandata-challenge.jp/news/2018-finalyst"),
    (2019, "official first-round results", "https://urbandata-challenge.jp/news/2019-finalyst"),
    (2020, "official first-round results", "https://urbandata-challenge.jp/news/2020-finalist"),
    (2021, "official first-round results", "https://urbandata-challenge.jp/news/2021-finalist"),
    (2022, "official first-round results", "https://urbandata-challenge.jp/news/2022-finalist"),
    (2023, "official first-round results", "https://urbandata-challenge.jp/news/2023-finalist"),
    (2024, "official first-round results", "https://urbandata-challenge.jp/news/2024-finalist"),
    (2025, "official first-round results", "https://urbandata-challenge.jp/news/2025-finalist"),
]

# These are the public sheets linked by the official first-round result pages.
# 2018/2019 are kept here so a 401/private result is recorded as an explicit
# unresolved reason rather than silently disappearing.
SHEET_SOURCES = [
    (2018, "official finalist sheet", "http://bit.ly/udc2018-finalist"),
    (
        2019,
        "official finalist sheet",
        "https://docs.google.com/spreadsheets/d/1tI6X9uXutsDSdaTs5flwQK3VKQg3a4hyx-sU-IiKJJ0/edit#gid=1120126937",
    ),
    (2020, "official finalist sheet", "https://docs.google.com/spreadsheets/d/1lc56B5xNghkz82AzxBv_19iARWowPgISZwT3fekKzVw/edit?usp=sharing"),
    (2021, "official finalist sheet", "https://docs.google.com/spreadsheets/d/1hSYKFHLhibWWVv_ccyErgAc3gsAoTPbTIbPAMjmouP8/edit?usp=sharing"),
    (
        2022,
        "official finalist sheet",
        "https://docs.google.com/spreadsheets/d/1zPBJ2mUkFuIbnCl2qQqgY6zTwNbYm_ycYSMTWGoj7Fs/edit#gid=988414118",
    ),
    (
        2022,
        "official finalist sheet",
        "https://docs.google.com/spreadsheets/d/1zPBJ2mUkFuIbnCl2qQqgY6zTwNbYm_ycYSMTWGoj7Fs/edit#gid=606757580",
    ),
]

CANDIDATE_COLUMNS = [
    "year",
    "project_name",
    "team",
    "discovery_source",
    "candidate_url",
    "candidate_type",
    "match_basis",
    "source_record",
    "resolution_status",
    "notes",
]


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", clean_text(value)).casefold()
    return re.sub(r"[^0-9a-zぁ-んァ-ン一-龥]+", "", value)


def match_basis(candidate_name: str, project_name: str) -> str:
    candidate = normalize_name(candidate_name)
    project = normalize_name(project_name)
    if not candidate or not project:
        return ""
    if candidate == project:
        return "normalized exact project name"
    if len(candidate) >= 6 and len(project) >= 6 and (candidate in project or project in candidate):
        return "normalized project name containment"
    return ""


def external_url(value: str, base_url: str) -> str:
    absolute = urljoin(base_url, clean_text(value))
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return absolute.rstrip(".,;:)]}】」")


def urls_from_cell(cell: Optional[Tag], value: str, base_url: str) -> list[str]:
    urls: list[str] = []
    if cell:
        for anchor in cell.find_all("a", href=True):
            url = external_url(anchor.get("href", ""), base_url)
            if url and url not in urls:
                urls.append(url)
    # Google Sheets' public HTML often renders URL cells as plain text.
    for raw in re.findall(r"https?://[^\s<>\"']+", value):
        url = external_url(raw, base_url)
        if url and url not in urls:
            urls.append(url)
    return urls


def field_rows(table: Tag) -> dict[str, tuple[str, Optional[Tag]]]:
    fields: dict[str, tuple[str, Optional[Tag]]] = {}
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        label = clean_text(cells[0].get_text(" ", strip=True))
        if not label:
            continue
        fields[label] = (clean_text(cells[1].get_text(" ", strip=True)), cells[1])
    return fields


@dataclass
class Candidate:
    year: int
    source_url: str
    source_kind: str
    project_name: str
    team: str
    candidate_url: str
    candidate_type: str
    source_record: str
    notes: str = ""

    def as_dict(self, target_name: str, basis: str, status: str) -> dict[str, str]:
        return {
            "year": str(self.year),
            "project_name": target_name,
            "team": "",
            "discovery_source": self.source_url,
            "candidate_url": self.candidate_url,
            "candidate_type": self.candidate_type,
            "match_basis": basis,
            # Keep source provenance as URLs and match metadata; do not republish source-cell text.
            "source_record": "",
            "resolution_status": status,
            "notes": self.notes,
        }


@dataclass
class SourceResult:
    year: int
    kind: str
    url: str
    status: str
    notes: str
    candidates: list[Candidate]


def candidate_from_url(
    year: int,
    source_url: str,
    source_kind: str,
    project_name: str,
    team: str,
    raw_url: str,
    source_record: str,
) -> Optional[Candidate]:
    url = external_url(raw_url, source_url)
    if not url or urlparse(url).netloc.lower() in OFFICIAL_HOSTS:
        return None
    github = github_repo_url(url)
    candidate_url = github or url
    notes = ""
    if "ログイン" in source_record or "login" in source_record.casefold():
        notes = "source URL field mentions login-required operation; no authentication attempted"
    return Candidate(
        year=year,
        source_url=source_url,
        source_kind=source_kind,
        project_name=clean_text(project_name),
        team=clean_text(team),
        candidate_url=candidate_url,
        candidate_type="github repository" if github else "officially listed project URL",
        source_record=clean_text(source_record),
        notes=notes,
    )


def extract_archive_candidates(year: int, source_url: str, soup: BeautifulSoup) -> list[Candidate]:
    candidates: list[Candidate] = []
    for table in soup.find_all("table"):
        fields = field_rows(table)
        if "作品名" not in fields:
            continue
        name, name_cell = fields["作品名"]
        for url in urls_from_cell(name_cell, name, source_url):
            candidate = candidate_from_url(year, source_url, "official archive project link", name, "", url, name)
            if candidate:
                candidates.append(candidate)
                break
    return candidates


def extract_result_candidates(year: int, source_url: str, soup: BeautifulSoup) -> list[Candidate]:
    candidates: list[Candidate] = []
    for table in soup.find_all("table"):
        fields = field_rows(table)
        if "作品名" not in fields or "作品URL" not in fields:
            continue
        name, _ = fields["作品名"]
        team = fields.get("チーム名", ("", None))[0]
        url_value, url_cell = fields["作品URL"]
        for url in urls_from_cell(url_cell, url_value, source_url):
            candidate = candidate_from_url(year, source_url, "official first-round project URL", name, team, url, url_value)
            if candidate:
                candidates.append(candidate)
    return candidates


def extract_sheet_candidates(year: int, source_url: str, soup: BeautifulSoup) -> tuple[list[Candidate], str]:
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        header_index = None
        headers: list[str] = []
        for index, row in enumerate(rows[:6]):
            values = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            if "作品名" in values:
                headers = values
                header_index = index
                break
        if header_index is None:
            continue
        if "作品URL" not in headers:
            return [], "public sheet has no 作品URL column"
        name_index = headers.index("作品名")
        url_index = headers.index("作品URL")
        team_index = headers.index("チーム名") if "チーム名" in headers else None
        candidates: list[Candidate] = []
        for row in rows[header_index + 1 :]:
            cells = row.find_all(["th", "td"])
            if len(cells) <= max(name_index, url_index):
                continue
            name = clean_text(cells[name_index].get_text(" ", strip=True))
            if not name:
                continue
            team = clean_text(cells[team_index].get_text(" ", strip=True)) if team_index is not None and len(cells) > team_index else ""
            url_value = clean_text(cells[url_index].get_text(" ", strip=True))
            for url in urls_from_cell(cells[url_index], url_value, source_url):
                candidate = candidate_from_url(year, source_url, "official public finalist sheet project URL", name, team, url, url_value)
                if candidate:
                    candidates.append(candidate)
        return candidates, f"public sheet parsed; {len(candidates)} non-empty project URL cells"
    return [], "could not find a public sheet table"


def unresolved_targets(records: Iterable[ProjectRecord]) -> list[ProjectRecord]:
    result: list[ProjectRecord] = []
    seen: set[tuple[int, str]] = set()
    for record in records:
        if record.primary_url or record.github_url:
            continue
        if record.url_status != "unknown":
            continue
        key = (record.year, normalize_name(record.project_name))
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def discover_sources(
    targets: list[ProjectRecord], session: object, limiter: RateLimiter, robots: RobotsCache
) -> tuple[list[Candidate], dict[int, list[str]]]:
    # Checker.request is used for the same robots/rate-limit behavior as the
    # survival pass, but no page is opened beyond the public GET itself.
    checker = Checker(session, limiter, robots)  # type: ignore[arg-type]
    target_years = {record.year for record in targets}
    all_candidates: list[Candidate] = []
    source_notes: dict[int, list[str]] = {}
    sources = [source for source in HTML_SOURCES if source[0] in target_years] + [source for source in SHEET_SOURCES if source[0] in target_years]
    for year, kind, url in sources:
        response, request_notes = checker.request(url, headers={"Accept": "text/html,application/xhtml+xml"})
        if response is None:
            detail = "; ".join(request_notes)
            source_notes.setdefault(year, []).append(f"{kind} unavailable ({url}): {detail}")
            continue
        if response.status_code != 200:
            source_notes.setdefault(year, []).append(f"{kind} status={response.status_code} ({url})")
            continue
        soup = BeautifulSoup(response.content, "html.parser")
        if "sheet" in kind:
            candidates, detail = extract_sheet_candidates(year, url, soup)
        elif "archive" in kind:
            candidates = extract_archive_candidates(year, url, soup)
            detail = f"official archive parsed; {len(candidates)} project-link candidates"
        else:
            candidates = extract_result_candidates(year, url, soup)
            detail = f"official first-round page parsed; {len(candidates)} project-URL candidates"
        all_candidates.extend(candidates)
        source_notes.setdefault(year, []).append(f"{kind}: {detail} ({url})")
    return all_candidates, source_notes


def candidate_matches(target: ProjectRecord, candidates: Iterable[Candidate]) -> list[tuple[Candidate, str]]:
    matches: list[tuple[Candidate, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        if candidate.year != target.year:
            continue
        basis = match_basis(candidate.project_name, target.project_name)
        if not basis:
            continue
        key = (candidate.candidate_url, basis)
        if key not in seen:
            seen.add(key)
            matches.append((candidate, basis))
    return matches


def apply_candidates(
    records: list[ProjectRecord],
    candidates: list[Candidate],
    source_notes: dict[int, list[str]],
) -> tuple[list[dict[str, str]], list[ProjectRecord]]:
    ledger: list[dict[str, str]] = []
    changed: list[ProjectRecord] = []
    targets = unresolved_targets(records)
    for target in targets:
        matches = candidate_matches(target, candidates)
        distinct_urls = {candidate.candidate_url for candidate, _ in matches}
        if len(distinct_urls) == 1:
            candidate, basis = matches[0]
            status = "candidate_found"
            ledger.append(candidate.as_dict(target.project_name, basis, status))
            for record in records:
                if record.year != target.year or normalize_name(record.project_name) != normalize_name(target.project_name):
                    continue
                if record.primary_url or record.github_url:
                    continue
                if candidate.candidate_type == "github repository":
                    record.github_url = candidate.candidate_url
                else:
                    record.primary_url = candidate.candidate_url
                record.notes = "; ".join(
                    filter(
                        None,
                        [
                            record.notes,
                            f"URL explored from {candidate.source_url}",
                            candidate.notes,
                        ],
                    )
                )
                changed.append(record)
        elif len(distinct_urls) > 1:
            for candidate, basis in matches:
                ledger.append(candidate.as_dict(target.project_name, basis, "ambiguous_candidates"))
        else:
            notes = "no candidate URL matched the project name"
            if source_notes.get(target.year):
                notes += "; " + " | ".join(source_notes[target.year])
            ledger.append(
                {
                    "year": str(target.year),
                    "project_name": target.project_name,
                    "team": target.team,
                    "discovery_source": "",
                    "candidate_url": "",
                    "candidate_type": "",
                    "match_basis": "",
                    "source_record": "",
                    "resolution_status": "not_found",
                    "notes": notes,
                }
            )
    return ledger, changed


def load_records(path: Path) -> list[ProjectRecord]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        records: list[ProjectRecord] = []
        for row in csv.DictReader(handle):
            values = {column: row.get(column, "") for column in CSV_COLUMNS}
            values["year"] = int(values["year"])
            records.append(ProjectRecord(**values))
        return records


def write_records(records: Iterable[ProjectRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(record.as_dict() for record in records)


def write_ledger(rows: Iterable[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANDIDATE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/udc_projects.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/udc_projects.csv"))
    parser.add_argument("--candidates-output", type=Path, default=Path("data/udc_url_candidates.csv"))
    parser.add_argument("--year", type=int, action="append", choices=sorted({source[0] for source in HTML_SOURCES + SHEET_SOURCES}))
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    records = load_records(args.input)
    if args.year:
        selected_years = set(args.year)
        targets = [record for record in records if record.year in selected_years]
    else:
        targets = records
    unresolved = unresolved_targets(targets)
    user_agent = "UDC-survival-check/0.1 (+fact-only research; no login)"
    import requests

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    limiter = RateLimiter(1.0)
    robots = RobotsCache(session, limiter, user_agent)
    candidates, source_notes = discover_sources(unresolved, session, limiter, robots)
    ledger, changed = apply_candidates(targets, candidates, source_notes)
    checker = Checker(session, limiter, robots)
    check_records(changed, checker)
    # `targets` contains the same mutable record objects as `records`, so the
    # original CSV order is retained even when --year limits discovery.
    write_records(records, args.output)
    write_ledger(ledger, args.candidates_output)
    found = sum(1 for row in ledger if row["resolution_status"] == "candidate_found")
    ambiguous = sum(1 for row in ledger if row["resolution_status"] == "ambiguous_candidates")
    print(f"explored {len(unresolved)} unresolved project names")
    print(f"found {found} candidate matches; ambiguous candidate rows={ambiguous}")
    print(f"rechecked {len(changed)} CSV records")
    print(f"wrote candidate ledger to {args.candidates_output}")
    print(f"wrote project CSV to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
