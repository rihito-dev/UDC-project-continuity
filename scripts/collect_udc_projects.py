#!/usr/bin/env python3
"""Collect and re-check Urban Data Challenge award projects.

The collector is deliberately fact-only.  It does not infer a cause of death
or score a project.  Award records come from the official UDC award pages;
URLs are only used when an official page explicitly links to them.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup, Tag
from bs4.dammit import UnicodeDammit


AWARD_PAGES = {
    2025: "https://urbandata-challenge.jp/news/udc2025prize",
    2024: "https://urbandata-challenge.jp/news/udc2024prize",
    2023: "https://urbandata-challenge.jp/news/udc2023prize",
    2022: "https://urbandata-challenge.jp/news/udc2022prize",
    2021: "https://urbandata-challenge.jp/news/udc2021prize",
    2020: "https://urbandata-challenge.jp/news/udc2020prize",
    2019: "https://urbandata-challenge.jp/highlight/udc2019prize",
    2018: "https://urbandata-challenge.jp/highlight/udc2018prize",
    2017: "https://urbandata-challenge.jp/2017/udc2017prize",
    2016: "https://urbandata-challenge.jp/2016/udc2016prize",
    2015: "https://urbandata-challenge.jp/2015/prize",
    2014: "https://urbandata-challenge.jp/2014/2014-archives",
}

CSV_COLUMNS = [
    "year",
    "award",
    "category",
    "project_name",
    "team",
    "source_url",
    "primary_url",
    "github_url",
    "url_status",
    "http_status",
    "page_title_now",
    "evidence_snippet",
    "wayback_first",
    "wayback_last",
    "wayback_count",
    "github_last_commit",
    "checked_at",
    "cause_of_death",
    "cause_resolved",
    "notes",
]

STANDARD_AWARDS = {"最優秀賞", "優秀賞", "金賞", "銀賞", "銅賞"}
DIVISION_NAMES = {
    "一般部門",
    "ビジネス・プロ部門",
    "ビジネス・プロフェッショナル部門",
    "アプリケーション部門",
    "データ部門",
    "アイデア部門",
    "アイディア部門",
    "アクティビティ部門",
    "ソリューション部門",
}
PARKING_TERMS = (
    "domain for sale",
    "this domain is for sale",
    "buy this domain",
    "domain parking",
    "parked domain",
    "ドメイン販売",
    "ドメイン売却",
    "ドメイン取得",
    "このドメイン",
    "広告",
)
LOGIN_TERMS = (
    "sign in",
    "log in",
    "ログイン",
    "パスワード",
    "password",
)
EXCLUDED_GITHUB_PATHS = {
    "login",
    "features",
    "topics",
    "marketplace",
    "organizations",
    "orgs",
    "settings",
}


def clean_text(value: str) -> str:
    return " ".join(value.replace("\u3000", " ").split())


def strip_brackets(value: str) -> str:
    return clean_text(value).strip("【】[]()（） ")


def is_missing(value: str) -> bool:
    return clean_text(value) in {"", "-", "–", "—", "該当なし", "なし"}


def is_division(value: str) -> bool:
    value = strip_brackets(value)
    # Keep this exact: award strings such as "水戸市長特別賞（アイデア部門）"
    # also end in 部門 but are not the official category field.
    return value in DIVISION_NAMES


def is_award_marker(value: str) -> bool:
    value = clean_text(value)
    stripped = strip_brackets(value)
    if stripped in STANDARD_AWARDS:
        return True
    if value.startswith("【") and value.endswith("】") and "賞" in stripped:
        return True
    return (
        "賞" in stripped
        and not is_division(stripped)
        and len(stripped) <= 35
        and not any(x in stripped for x in ("受賞者", "審査結果", "講評"))
    )


def is_project_name(value: str) -> bool:
    return not is_missing(value) and clean_text(value) not in {
        "作品名",
        "チーム名",
        "代表者名／チーム名",
        "拠点名",
    }


def main_content(soup: BeautifulSoup) -> Tag:
    return soup.find("article") or soup.find("main") or soup.body or soup


def preceding_strings(tag: Tag, limit: int = 160) -> Iterator[str]:
    seen = 0
    for string in tag.find_all_previous(string=True):
        value = clean_text(str(string))
        if value:
            yield value
            seen += 1
            if seen >= limit:
                return


def previous_award(tag: Tag) -> str:
    for value in preceding_strings(tag):
        if is_award_marker(value):
            return strip_brackets(value)
    return ""


def previous_division(tag: Tag) -> str:
    for value in preceding_strings(tag):
        value = strip_brackets(value)
        if is_division(value):
            return value
    return ""


def links_for_tag(tag: Tag, base_url: str) -> list[str]:
    links: list[str] = []
    for anchor in tag.find_all("a", href=True):
        href = clean_text(anchor.get("href", ""))
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if absolute not in links:
            links.append(absolute)
    return links


def external_project_link(tag: Tag, base_url: str) -> str:
    official_host = urlparse(base_url).netloc.lower()
    for link in links_for_tag(tag, base_url):
        if urlparse(link).netloc.lower() != official_host:
            return link
    return ""


def github_repo_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.netloc.lower().split(":")[0] not in {"github.com", "www.github.com"}:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].lower() in EXCLUDED_GITHUB_PATHS:
        return ""
    repo = parts[1].removesuffix(".git")
    if not repo:
        return ""
    return f"https://github.com/{parts[0]}/{repo}"


@dataclass
class ProjectRecord:
    year: int
    award: str
    category: str
    project_name: str
    team: str
    source_url: str
    primary_url: str = ""
    github_url: str = ""
    url_status: str = "unknown"
    http_status: str = ""
    page_title_now: str = ""
    evidence_snippet: str = ""
    wayback_first: str = ""
    wayback_last: str = ""
    wayback_count: str = ""
    github_last_commit: str = ""
    checked_at: str = ""
    cause_of_death: str = ""
    cause_resolved: str = ""
    notes: str = ""

    def as_dict(self) -> dict[str, str]:
        values = self.__dict__.copy()
        values["year"] = str(values["year"])
        # Public export minimization: team names and copied body text are not persisted.
        values["team"] = ""
        values["evidence_snippet"] = ""
        values["cause_of_death"] = ""
        values["cause_resolved"] = ""
        return {column: str(values.get(column, "")) for column in CSV_COLUMNS}


def record_from_row(
    year: int,
    source_url: str,
    headers: list[str],
    row: Tag,
    context_award: str = "",
    context_category: str = "",
) -> Optional[ProjectRecord]:
    cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
    if not cells or len(cells) != len(headers):
        return None
    values = dict(zip(headers, cells))
    name = values.get("作品名", "")
    if not is_project_name(name):
        return None
    award = values.get("賞", "") or context_award
    category = values.get("部門", "") or context_category
    team = values.get("チーム名", "") or values.get("代表者名／チーム名", "")
    link = external_project_link(row, source_url)
    github = github_repo_url(link)
    notes = []
    if values.get("テーマ"):
        notes.append(f"theme={values['テーマ']}")
    if values.get("タイプ"):
        notes.append(f"type={values['タイプ']}")
    if not category:
        notes.append("official award page has no 部門 value for this award row")
    return ProjectRecord(
        year=year,
        award=award,
        category=category,
        project_name=name,
        team=team if not is_missing(team) else "",
        source_url=source_url,
        primary_url=link if not github else "",
        github_url=github,
        notes="; ".join(notes),
    )


def table_headers(table: Tag) -> list[str]:
    first = table.find("tr")
    if not first:
        return []
    return [clean_text(cell.get_text(" ", strip=True)) for cell in first.find_all(["th", "td"])]


def parse_modern_tables(year: int, source_url: str, content: Tag) -> list[ProjectRecord]:
    records: list[ProjectRecord] = []
    for table in content.find_all("table"):
        headers = table_headers(table)
        if "作品名" not in headers:
            continue
        row_context_award = previous_award(table)
        row_context_category = "" if row_context_award and row_context_award not in STANDARD_AWARDS else previous_division(table)
        for row in table.find_all("tr")[1:]:
            record = record_from_row(
                year,
                source_url,
                headers,
                row,
                context_award=row_context_award,
                context_category=row_context_category,
            )
            if record:
                records.append(record)
    return records


def parse_older_tables(year: int, source_url: str, content: Tag) -> list[ProjectRecord]:
    records: list[ProjectRecord] = []
    for table in content.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = table_headers(table)
        has_header = "作品名" in headers
        for row in rows[1:] if has_header else rows:
            cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
            if len(cells) < 2:
                continue
            if len(cells) >= 3 and cells[0] in STANDARD_AWARDS:
                award, name, team = cells[0], cells[1], cells[2]
                name_cell = row.find_all(["th", "td"])[1]
            else:
                award, name, team = previous_award(table), cells[-2], cells[-1]
                name_cell = row.find_all(["th", "td"])[-2]
            name = re.sub(r"\s*作品ページ$", "", name).strip()
            team = re.sub(r"\s*受賞(者)?コメント.*$", "", team).strip()
            if "地域拠点" in name or "地域拠点" in award:
                continue
            if not is_project_name(name):
                continue
            category = previous_division(table) if award in STANDARD_AWARDS else ""
            link = external_project_link(name_cell, source_url)
            github = github_repo_url(link)
            records.append(
                ProjectRecord(
                    year=year,
                    award=award,
                    category=category,
                    project_name=name,
                    team="" if is_missing(team) else team,
                    source_url=source_url,
                    primary_url=link if not github else "",
                    github_url=github,
                    notes="official award table; theme/type not provided in this table",
                )
            )
    return records


def parse_2014(source_url: str, content: Tag) -> list[ProjectRecord]:
    records: list[ProjectRecord] = []
    for table in content.find_all("table"):
        rows = table.find_all("tr")
        fields: dict[str, tuple[str, Tag]] = {}
        for row in rows:
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            label = clean_text(cells[0].get_text(" ", strip=True))
            fields[label] = (clean_text(cells[1].get_text(" ", strip=True)), cells[1])
        if "作品名" not in fields or "部門賞/特別賞" not in fields:
            continue
        name, name_cell = fields["作品名"]
        award_value = fields["部門賞/特別賞"][0]
        if not is_project_name(name) or award_value in {"-/-", "-"}:
            continue
        award_parts = [clean_text(part) for part in award_value.split("/") if not is_missing(part)]
        if not award_parts:
            continue
        category = previous_division(table)
        if not category:
            # The 2014 page uses h5 headings for its division names.
            for heading in table.find_all_previous(["h4", "h5"]):
                value = strip_brackets(heading.get_text(" ", strip=True))
                if is_division(value):
                    category = value
                    break
        link = external_project_link(name_cell, source_url)
        github = github_repo_url(link)
        overview = fields.get("作品概要", ("", None))[0]
        notes = ["official 2014 archive record"]
        if overview:
            notes.append("application_description_present_on_source_page")
        if len(award_parts) > 1:
            notes.append("部門賞/特別賞 recorded together")
        records.append(
            ProjectRecord(
                year=2014,
                award=" / ".join(award_parts),
                category=category,
                project_name=name,
                team="",
                source_url=source_url,
                primary_url=link if not github else "",
                github_url=github,
                notes="; ".join(notes),
            )
        )
    return records


def next_time_text(heading: Tag) -> str:
    for element in heading.next_elements:
        if isinstance(element, Tag) and element.name in {"h3", "h4", "h5"}:
            return ""
        if isinstance(element, Tag) and "time" in (element.get("class") or []):
            return clean_text(element.get_text(" ", strip=True))
    return ""


def parse_2015_2016(year: int, source_url: str, content: Tag) -> list[ProjectRecord]:
    records: list[ProjectRecord] = []
    current_section = ""
    for heading in content.find_all(["h3", "h4", "h5"]):
        text = clean_text(heading.get_text(" ", strip=True))
        if heading.name in {"h3", "h4"}:
            current_section = text
            continue
        if not text or "地域拠点" in text:
            continue
        name = ""
        explicit_award = ""
        explicit_category = ""
        match = re.match(r"^(最優秀賞|優秀賞|金賞|銀賞|銅賞)\s*[「『\"](.+?)[」』\"]$", text)
        if match:
            explicit_award, name = match.groups()
        else:
            match = re.match(r"^(.+?部門)\s*[「『\"](.+?)[」』\"]$", text)
            if match:
                explicit_category, name = match.groups()
            else:
                match = re.match(r"^[「『\"](.+?)[」』\"]$", text)
                if match:
                    name = match.group(1)
        if not name or not is_project_name(name):
            continue
        if explicit_award:
            award = explicit_award
        elif current_section:
            award = current_section
        else:
            award = ""
        category = explicit_category
        if not category and current_section in DIVISION_NAMES:
            category = current_section
        if not category and current_section.endswith("部門賞"):
            category = current_section.removesuffix("賞")
        team = next_time_text(heading)
        notes = ["official heading/prose award record"]
        if not category:
            notes.append(f"section={current_section}" if current_section else "category_not_stated")
        records.append(
            ProjectRecord(
                year=year,
                award=award,
                category=category,
                project_name=name,
                team=team,
                source_url=source_url,
                notes="; ".join(notes),
            )
        )
    return records


def deduplicate(records: Iterable[ProjectRecord]) -> list[ProjectRecord]:
    result: list[ProjectRecord] = []
    seen: set[tuple[int, str, str, str]] = set()
    for record in records:
        key = (record.year, record.award, record.category, record.project_name)
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


class RateLimiter:
    def __init__(self, interval_seconds: float = 1.0) -> None:
        self.interval_seconds = interval_seconds
        self.last_request: dict[str, float] = {}

    def wait(self, host: str) -> None:
        now = time.monotonic()
        remaining = self.interval_seconds - (now - self.last_request.get(host, 0.0))
        if remaining > 0:
            time.sleep(remaining)
        self.last_request[host] = time.monotonic()


class RobotsCache:
    def __init__(self, session: requests.Session, limiter: RateLimiter, user_agent: str) -> None:
        self.session = session
        self.limiter = limiter
        self.user_agent = user_agent
        self.cache: dict[str, Optional[RobotFileParser]] = {}
        self.notes: dict[str, str] = {}

    def allowed(self, url: str) -> tuple[bool, str]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False, "unsupported URL scheme"
        host = parsed.netloc.lower()
        if host not in self.cache:
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            self.limiter.wait(host)
            try:
                response = self.session.get(robots_url, timeout=20, allow_redirects=True)
                if response.status_code == 404:
                    self.cache[host] = RobotFileParser()
                    self.cache[host].parse([])
                    self.notes[host] = "robots.txt returned 404; no rules found"
                elif 200 <= response.status_code < 300:
                    parser = RobotFileParser()
                    parser.set_url(robots_url)
                    parser.parse(response.text.splitlines())
                    self.cache[host] = parser
                    self.notes[host] = "robots.txt read"
                else:
                    self.cache[host] = None
                    self.notes[host] = f"robots.txt status={response.status_code}; request skipped conservatively"
            except requests.RequestException as exc:
                self.cache[host] = None
                self.notes[host] = f"robots.txt error={type(exc).__name__}; request skipped conservatively"
        parser = self.cache[host]
        if parser is None:
            return False, self.notes.get(host, "robots.txt unavailable")
        if not parser.can_fetch(self.user_agent, url):
            return False, "blocked by robots.txt"
        return True, self.notes.get(host, "robots.txt read")


@dataclass
class PageResult:
    status: str
    http_status: str = ""
    title: str = ""
    evidence: str = ""
    final_url: str = ""
    notes: list[str] = field(default_factory=list)


class Checker:
    def __init__(self, session: requests.Session, limiter: RateLimiter, robots: RobotsCache) -> None:
        self.session = session
        self.limiter = limiter
        self.robots = robots
        self.wayback_cache: dict[str, tuple[str, str, str, str]] = {}
        self.github_cache: dict[str, tuple[str, str]] = {}

    def request(self, url: str, **kwargs: object) -> tuple[Optional[requests.Response], list[str]]:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        allowed, robot_note = self.robots.allowed(url)
        notes = [robot_note]
        if not allowed:
            return None, notes
        self.limiter.wait(host)
        try:
            response = self.session.get(url, timeout=25, allow_redirects=True, **kwargs)
            return response, notes
        except requests.RequestException as exc:
            notes.append(f"request error={type(exc).__name__}: {exc}")
            return None, notes

    @staticmethod
    def decode_page(response: requests.Response) -> str:
        # Prefer an explicit HTTP charset, then strict UTF-8, then the page's own
        # declaration. Statistical guessing comes last because it can misread
        # UTF-8 Japanese as MacRoman; requests' Latin-1 fallback is never used.
        content_type = response.headers.get("content-type", "")
        header = requests.utils.get_encoding_from_headers(response.headers) if "charset" in content_type.lower() else None
        dammit = UnicodeDammit(
            response.content,
            known_definite_encodings=[header] if header else [],
            user_encodings=["utf-8"],
            is_html=True,
        )
        return dammit.unicode_markup or ""

    @staticmethod
    def page_evidence(html: str, content_type: str) -> tuple[str, str]:
        if "html" not in content_type and not html.lstrip().startswith("<"):
            return "", ""
        soup = BeautifulSoup(html, "html.parser")
        title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
        body = soup.body or soup
        evidence = clean_text(body.get_text(" ", strip=True))[:200]
        return title, evidence

    @staticmethod
    def likely_login(url: str, title: str, evidence: str, response: requests.Response) -> bool:
        if "/login" in urlparse(url).path.lower():
            return True
        combined = f"{title} {evidence}".casefold()
        if "password" in combined and any(term in combined for term in ("sign in", "log in", "ログイン")):
            return True
        return response.status_code in {401, 403}

    @staticmethod
    def domain_key(host: str) -> str:
        return host.lower().split(":")[0].removeprefix("www.")

    @staticmethod
    def related(project_name: str, title: str, evidence: str, response_text: str) -> bool:
        haystack = clean_text(f"{title} {evidence} {response_text}").casefold()
        name = clean_text(project_name).casefold()
        if name and name in haystack:
            return True
        quoted_aliases = re.findall(r"[「『“”\"]([^」』“”\"]{2,})[」』“”\"]", name)
        for alias in quoted_aliases:
            alias = clean_text(alias)
            if alias and alias in haystack:
                return True
        compact_haystack = re.sub(r"[^0-9a-zぁ-んァ-ン一-龥]+", "", haystack)
        compact_name = re.sub(r"[^0-9a-zぁ-んァ-ン一-龥]+", "", name)
        if compact_name and len(compact_name) >= 5 and compact_name in compact_haystack:
            return True
        tokens = [token for token in re.findall(r"[a-z0-9]{3,}|[ぁ-んァ-ン一-龥]{2,}", name) if token not in {"with", "and"}]
        if tokens:
            hits = sum(1 for token in tokens if token in haystack)
            if hits >= max(1, min(2, len(tokens))):
                return True
        return False

    def check_page(self, url: str, project_name: str) -> PageResult:
        response, notes = self.request(url, headers={"Accept": "text/html,application/xhtml+xml"})
        if response is None:
            return PageResult(status="unknown", notes=notes)
        html = self.decode_page(response)
        title, evidence = self.page_evidence(html, response.headers.get("content-type", ""))
        final_url = response.url
        initial_host = self.domain_key(urlparse(url).netloc)
        final_host = self.domain_key(urlparse(final_url).netloc)
        if initial_host and final_host and initial_host != final_host:
            notes.append(f"redirected to different domain: {final_host}")
            return PageResult("redirected_domain", str(response.status_code), title, evidence, final_url, notes)
        if self.likely_login(final_url, title, evidence, response):
            notes.append("login-protected or access-denied page; not entered")
            return PageResult("unknown", str(response.status_code), title, evidence, final_url, notes)
        if response.status_code in {404, 410}:
            return PageResult("unreachable", str(response.status_code), title, evidence, final_url, notes)
        if 500 <= response.status_code <= 599:
            self.limiter.wait(initial_host)
            try:
                retry = self.session.get(url, timeout=25, allow_redirects=True)
                if 500 <= retry.status_code <= 599:
                    notes.append("5xx observed twice")
                    return PageResult("unreachable", str(retry.status_code), title, evidence, retry.url, notes)
                notes.append(f"retry status={retry.status_code}; not treated as constant 5xx")
            except requests.RequestException as exc:
                notes.append(f"5xx retry error={type(exc).__name__}")
            return PageResult("unknown", str(response.status_code), title, evidence, final_url, notes)
        if response.status_code == 200:
            related = self.related(project_name, title, evidence, html[:200000])
            if related:
                notes.append("project-name relatedness found in title/body")
                return PageResult("reachable_related", "200", title, evidence, final_url, notes)
            if any(term in f"{title} {evidence}".casefold() for term in PARKING_TERMS):
                notes.append("200 page matched parking/advertising terms")
            else:
                notes.append("200 page did not match project name")
            return PageResult("reachable_unrelated", "200", title, evidence, final_url, notes)
        return PageResult("unknown", str(response.status_code), title, evidence, final_url, notes)

    def wayback(self, url: str) -> tuple[str, str, str, str]:
        if url in self.wayback_cache:
            return self.wayback_cache[url]
        endpoint = "https://web.archive.org/cdx/search/cdx"
        response, notes = self.request(
            endpoint,
            params={
                "url": url,
                "output": "json",
                "fl": "timestamp,statuscode,mimetype",
                "filter": "mimetype:text/html",
                "collapse": "digest",
                "matchType": "exact",
            },
            headers={"Accept": "application/json"},
        )
        if response is None:
            result = ("", "", "", "; ".join(notes))
            self.wayback_cache[url] = result
            return result
        try:
            payload = response.json()
            rows = payload[1:] if payload and isinstance(payload[0], list) else payload
            timestamps = [str(row[0]) for row in rows if isinstance(row, list) and row and str(row[0]).isdigit()]
            first = min(timestamps)[:8] if timestamps else ""
            last = max(timestamps)[:8] if timestamps else ""
            result = (
                f"{first[:4]}-{first[4:6]}-{first[6:8]}" if len(first) == 8 else "",
                f"{last[:4]}-{last[4:6]}-{last[6:8]}" if len(last) == 8 else "",
                str(len(timestamps)),
                "; ".join(notes) + "; CDX filters=mimetype:text/html,collapse=digest",
            )
        except (ValueError, TypeError, IndexError) as exc:
            result = ("", "", "", "; ".join(notes) + f"; CDX parse error={type(exc).__name__}")
        self.wayback_cache[url] = result
        return result

    def github_last_commit(self, github_url: str) -> tuple[str, str]:
        if github_url in self.github_cache:
            return self.github_cache[github_url]
        parts = [part for part in urlparse(github_url).path.split("/") if part]
        if len(parts) < 2:
            result = ("", "invalid GitHub repository URL")
            self.github_cache[github_url] = result
            return result
        api_url = f"https://api.github.com/repos/{parts[0]}/{parts[1]}/commits"
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response, notes = self.request(
            api_url,
            params={"per_page": "1"},
            headers=headers,
        )
        if response is None:
            result = ("", "; ".join(notes))
        elif response.status_code == 200:
            try:
                commits = response.json()
                commit = commits[0]["commit"]
                date = commit.get("committer", {}).get("date") or commit.get("author", {}).get("date") or ""
                result = (date, "; ".join(notes) + "; GitHub REST API")
            except (IndexError, KeyError, TypeError, ValueError) as exc:
                result = ("", "; ".join(notes) + f"; GitHub API parse error={type(exc).__name__}")
        else:
            result = ("", "; ".join(notes) + f"; GitHub API status={response.status_code}")
        self.github_cache[github_url] = result
        return result


def parse_page(year: int, source_url: str, html: str) -> list[ProjectRecord]:
    soup = BeautifulSoup(html, "html.parser")
    content = main_content(soup)
    if year == 2014:
        return parse_2014(source_url, content)
    if year in {2015, 2016}:
        return parse_2015_2016(year, source_url, content)
    if year in {2017, 2018, 2019}:
        return parse_older_tables(year, source_url, content)
    return parse_modern_tables(year, source_url, content)


def collect_records(
    years: Iterable[int], session: requests.Session, limiter: RateLimiter, robots: RobotsCache
) -> tuple[list[ProjectRecord], list[str]]:
    records: list[ProjectRecord] = []
    errors: list[str] = []
    for year in years:
        source_url = AWARD_PAGES[year]
        allowed, robot_note = robots.allowed(source_url)
        if not allowed:
            errors.append(f"{year}: {robot_note}")
            continue
        limiter.wait(urlparse(source_url).netloc.lower())
        try:
            response = session.get(source_url, timeout=30, allow_redirects=True)
            if response.status_code != 200:
                errors.append(f"{year}: official page status={response.status_code}")
                continue
            records.extend(parse_page(year, source_url, response.text))
        except requests.RequestException as exc:
            errors.append(f"{year}: {type(exc).__name__}: {exc}")
        except Exception as exc:  # keep one malformed year from stopping the run
            errors.append(f"{year}: parser error={type(exc).__name__}: {exc}")
    return deduplicate(records), errors


def check_records(records: list[ProjectRecord], checker: Checker) -> None:
    checked_at = datetime.now(timezone.utc).isoformat()
    for record in records:
        target_url = record.primary_url or record.github_url
        record.checked_at = checked_at
        if not target_url:
            record.url_status = "unknown"
            record.notes = "; ".join(filter(None, [record.notes, "no explicit public URL or GitHub URL on official source page"]))
            continue
        if not record.primary_url:
            record.notes = "; ".join(filter(None, [record.notes, "survival check used github_url because primary_url is absent"]))
        try:
            page = checker.check_page(target_url, record.project_name)
            record.url_status = page.status
            record.http_status = page.http_status
            record.page_title_now = page.title
            # Use page text transiently for classification, but do not persist third-party body text.
            record.evidence_snippet = ""
            record.notes = "; ".join(filter(None, [record.notes, *page.notes]))
            first, last, count, wayback_note = checker.wayback(target_url)
            record.wayback_first = first
            record.wayback_last = last
            record.wayback_count = count
            record.notes = "; ".join(filter(None, [record.notes, wayback_note]))
        except Exception as exc:  # record-level resilience is intentional
            record.url_status = "unknown"
            record.notes = "; ".join(filter(None, [record.notes, f"record check error={type(exc).__name__}: {exc}"]))
        if record.github_url:
            try:
                commit, github_note = checker.github_last_commit(record.github_url)
                record.github_last_commit = commit
                record.notes = "; ".join(filter(None, [record.notes, github_note]))
            except Exception as exc:
                record.notes = "; ".join(filter(None, [record.notes, f"GitHub check error={type(exc).__name__}: {exc}"]))


def write_csv(records: Iterable[ProjectRecord], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(record.as_dict() for record in records)


def select_test_records(records: list[ProjectRecord], limit: Optional[int]) -> list[ProjectRecord]:
    if not limit or limit >= len(records):
        return records
    # Prefer GitHub records first so the 5-record gate exercises both the
    # survival logic and the GitHub REST API path when one is available.
    with_github = [record for record in records if record.github_url]
    with_primary = [record for record in records if record.primary_url and not record.github_url]
    without_urls = [record for record in records if not (record.primary_url or record.github_url)]
    return (with_github + with_primary + without_urls)[:limit]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/udc_projects.csv"))
    parser.add_argument("--limit", type=int, default=None, help="limit records after preferring records with explicit URLs")
    parser.add_argument("--year", type=int, action="append", choices=sorted(AWARD_PAGES), help="restrict collection to one or more years")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    user_agent = "UDC-survival-check/0.1 (+fact-only research; no login)"
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    limiter = RateLimiter(1.0)
    robots = RobotsCache(session, limiter, user_agent)
    years = sorted(args.year or AWARD_PAGES.keys(), reverse=True)
    records, errors = collect_records(years, session, limiter, robots)
    selected = select_test_records(records, args.limit)
    checker = Checker(session, limiter, robots)
    check_records(selected, checker)
    write_csv(selected, args.output)
    print(f"wrote {len(selected)} records to {args.output}")
    print(f"parsed {len(records)} records before limit")
    if errors:
        print("collection errors:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
