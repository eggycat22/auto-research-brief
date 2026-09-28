from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import feedparser
import httpx

from . import db
from .config import settings
from .official_sources import OFFICIAL_SOURCES
from .util import html_to_text, loads, truncate, url_is_safe

UA = "Mozilla/5.0 (compatible; auto-research-brief/0.3; +https://github.com/)"
RSS_HEADERS = {"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"}


def _client(timeout: float = 40) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(timeout, connect=15),
        headers={"User-Agent": UA},
        follow_redirects=True,
    )


def collect_arxiv(categories: list[str], max_results: int) -> list[dict[str, Any]]:
    cats = " OR ".join(f"cat:{c}" for c in categories)
    url = (
        "https://export.arxiv.org/api/query"
        f"?search_query={cats}&start=0&max_results={max_results}"
        "&sortBy=submittedDate&sortOrder=descending"
    )
    with _client() as client:
        r = client.get(url)
        r.raise_for_status()
        time.sleep(3)
        xml = r.text
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml)
    items: list[dict[str, Any]] = []
    for entry in root.findall("a:entry", ns):
        aid = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()
        arxiv_id = aid.rsplit("/abs/", 1)[-1]
        title = " ".join((entry.findtext("a:title", default="", namespaces=ns) or "").split())
        summary = " ".join((entry.findtext("a:summary", default="", namespaces=ns) or "").split())
        published = entry.findtext("a:published", default="", namespaces=ns) or ""
        link = f"https://arxiv.org/abs/{arxiv_id}"
        items.append(
            {
                "kind": "paper",
                "source": "arxiv",
                "external_id": arxiv_id,
                "title": title,
                "summary": summary,
                "source_url": link,
                "published_at": published,
                "metadata": {"arxiv_id": arxiv_id},
            }
        )
    return items


def fetch_arxiv_html(arxiv_id: str) -> str:
    url = f"https://arxiv.org/html/{arxiv_id}"
    try:
        with _client() as client:
            r = client.get(url)
            if r.status_code >= 400:
                return ""
            return html_to_text(r.text, 10000)
    except httpx.HTTPError:
        return ""


def collect_hf_daily() -> list[dict[str, Any]]:
    url = "https://huggingface.co/api/daily_papers"
    with _client() as client:
        r = client.get(url)
        r.raise_for_status()
        data = r.json()
    items: list[dict[str, Any]] = []
    for row in data:
        paper = row.get("paper") or row
        pid = str(paper.get("id") or row.get("id") or "")
        title = paper.get("title") or row.get("title") or ""
        summary = paper.get("summary") or paper.get("abstract") or ""
        if not pid or not title:
            continue
        link = f"https://huggingface.co/papers/{pid}"
        items.append(
            {
                "kind": "paper",
                "source": "hf_daily",
                "external_id": pid,
                "title": title,
                "summary": summary,
                "source_url": link,
                "published_at": row.get("publishedAt") or "",
                "metadata": {"hf_id": pid},
            }
        )
    return items


def collect_rss(feed_url: str, source_name: str) -> list[dict[str, Any]]:
    if not url_is_safe(feed_url):
        return []
    raw: bytes | None = None
    try:
        with _client() as client:
            r = client.get(feed_url, headers=RSS_HEADERS)
            if r.status_code < 400:
                raw = r.content
    except httpx.HTTPError:
        raw = None
    parsed = feedparser.parse(raw if raw is not None else feed_url, request_headers=RSS_HEADERS)
    items: list[dict[str, Any]] = []
    for e in parsed.entries[:40]:
        ext = e.get("id") or e.get("link") or e.get("title")
        items.append(
            {
                "kind": "news",
                "source": source_name or "rss",
                "external_id": str(ext),
                "title": e.get("title") or "(untitled)",
                "summary": html_to_text(e.get("summary") or e.get("description") or "", 800),
                "source_url": e.get("link") or "",
                "published_at": e.get("published") or "",
                "metadata": {},
            }
        )
    return items


def collect_wordpress(api_url: str, source_name: str) -> list[dict[str, Any]]:
    base = api_url.split("?", 1)[0]
    if not url_is_safe(base):
        return []
    with _client() as client:
        r = client.get(api_url, headers={"Accept": "application/json"})
        r.raise_for_status()
        posts = r.json()
    if not isinstance(posts, list):
        return []
    items: list[dict[str, Any]] = []
    for post in posts[:15]:
        title_raw = (post.get("title") or {})
        excerpt_raw = (post.get("excerpt") or {})
        title = html_to_text(title_raw.get("rendered") if isinstance(title_raw, dict) else str(title_raw or ""), 200)
        excerpt = html_to_text(
            excerpt_raw.get("rendered") if isinstance(excerpt_raw, dict) else str(excerpt_raw or ""),
            800,
        )
        link = post.get("link") or ""
        pid = post.get("id")
        items.append(
            {
                "kind": "news",
                "source": source_name,
                "external_id": f"wp:{pid}" if pid else link,
                "title": title or "(untitled)",
                "summary": excerpt,
                "source_url": link,
                "published_at": post.get("date") or "",
                "metadata": {"media": True},
            }
        )
    return items


def collect_arxiv_query(query: str, max_results: int = 30, sort: str = "submittedDate") -> list[dict[str, Any]]:
    from urllib.parse import quote

    url = (
        "https://export.arxiv.org/api/query"
        f"?search_query={quote(query)}&start=0&max_results={max_results}"
        f"&sortBy={sort}&sortOrder=descending"
    )
    with _client() as client:
        r = client.get(url)
        r.raise_for_status()
        time.sleep(3)
        xml = r.text
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml)
    items: list[dict[str, Any]] = []
    for entry in root.findall("a:entry", ns):
        aid = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()
        arxiv_id = aid.rsplit("/abs/", 1)[-1]
        title = " ".join((entry.findtext("a:title", default="", namespaces=ns) or "").split())
        summary = " ".join((entry.findtext("a:summary", default="", namespaces=ns) or "").split())
        published = entry.findtext("a:published", default="", namespaces=ns) or ""
        items.append(
            {
                "kind": "paper",
                "source": "arxiv",
                "external_id": arxiv_id,
                "title": title,
                "summary": summary,
                "source_url": f"https://arxiv.org/abs/{arxiv_id}",
                "published_at": published,
                "metadata": {"arxiv_id": arxiv_id},
            }
        )
    return items


def github_headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers = {"User-Agent": UA, "Accept": accept}
    token = db.merged_secret("github_token", settings.github_token, "github_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_github_repo(full_name: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{full_name}"
    try:
        with _client() as client:
            r = client.get(url, headers=github_headers())
            if r.status_code >= 400 or not isinstance(r.json(), dict):
                return {}
            data = r.json()
            return {
                "description": data.get("description") or "",
                "full_name": data.get("full_name") or full_name,
                "stars": data.get("stargazers_count"),
            }
    except httpx.HTTPError:
        return {}


def _parse_star_row(row: dict[str, Any]) -> dict[str, Any] | None:
    repo = row.get("repo") if isinstance(row.get("repo"), dict) else row
    full = repo.get("full_name") or ""
    repo_id = repo.get("id")
    if not full or not repo_id:
        return None
    return {
        "kind": "github_star",
        "source": "github",
        "external_id": f"repo:{repo_id}",
        "title": full,
        "summary": repo.get("description") or "",
        "source_url": repo.get("html_url") or "",
        "published_at": row.get("starred_at") or repo.get("pushed_at") or "",
        "metadata": {
            "repo_id": repo_id,
            "full_name": full,
            "stars": repo.get("stargazers_count"),
            "language": repo.get("language"),
            "topics": repo.get("topics") or [],
            "default_branch": repo.get("default_branch") or "main",
            "license": (repo.get("license") or {}).get("spdx_id"),
            "pushed_at": repo.get("pushed_at"),
            "starred_at": row.get("starred_at") or "",
            "description": repo.get("description") or "",
        },
    }


def collect_github_stars(username: str, per_page: int = 100) -> tuple[list[dict[str, Any]], bool, str]:
    """Return (items, sync_complete, error). Paginate until exhausted. Public stars only."""
    items: list[dict[str, Any]] = []
    page = 1
    complete = True
    error = ""
    with _client() as client:
        while page <= 20:
            r = client.get(
                f"https://api.github.com/users/{username}/starred",
                headers=github_headers("application/vnd.github.star+json"),
                params={"per_page": per_page, "page": page},
            )
            if r.status_code >= 400:
                complete = False
                error = f"HTTP {r.status_code} for user {username}"
                break
            batch = r.json()
            if not isinstance(batch, list) or not batch:
                break
            for row in batch:
                parsed = _parse_star_row(row if isinstance(row, dict) else {})
                if parsed:
                    items.append(parsed)
            link = r.headers.get("link") or r.headers.get("Link") or ""
            if 'rel="next"' not in link:
                break
            page += 1
            time.sleep(0.4)
    return items, complete, error


def fetch_github_readme(full_name: str) -> str:
    url = f"https://api.github.com/repos/{full_name}/readme"
    try:
        with _client() as client:
            r = client.get(url, headers={**github_headers(), "Accept": "application/vnd.github.raw"})
            if r.status_code >= 400:
                return ""
            return (r.text or "")[:24000]
    except httpx.HTTPError:
        return ""


def fetch_github_tree(full_name: str, branch: str, path: str = "") -> list[str]:
    rel = (path or "").lstrip("/")
    url = f"https://api.github.com/repos/{full_name}/contents/{rel}" if rel else f"https://api.github.com/repos/{full_name}/contents/"
    try:
        with _client() as client:
            r = client.get(url, headers=github_headers(), params={"ref": branch})
            if r.status_code >= 400:
                return []
            data = r.json()
        if not isinstance(data, list):
            return []
        names = []
        for row in data[:40]:
            mark = "/" if row.get("type") == "dir" else ""
            names.append(f"{row.get('name')}{mark}")
        return names
    except httpx.HTTPError:
        return []


def fetch_url_text(url: str) -> str:
    if not url_is_safe(url):
        raise RuntimeError("不允许抓取该地址")
    with _client() as client:
        r = client.get(url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "html" in ctype:
            return html_to_text(r.text, 12000)
        return (r.text or "")[:12000]


def fetch_url_html(url: str) -> tuple[str, int, str]:
    """Return (body, status, note). Never raises for HTTP errors."""
    if not url_is_safe(url):
        return "", 0, "地址不安全"
    timeout = 55.0 if "amd.com" in url else 40.0
    headers = {"User-Agent": UA}
    if "x.ai" in url:
        headers["Referer"] = "https://x.ai/"
    last_err = ""
    for attempt in range(2):
        try:
            with _client(timeout=timeout) as client:
                r = client.get(url, headers=headers)
                if r.status_code < 400:
                    return r.text or "", r.status_code, ""
                if r.status_code == 403 and "x.ai" in url and attempt == 0:
                    url = url.replace("/news", "/blog")
                    continue
                return r.text or "", r.status_code, ""
        except httpx.HTTPError as exc:
            last_err = type(exc).__name__
            if attempt == 0:
                time.sleep(0.6)
                continue
    return "", 0, last_err or "fetch_failed"


def collect_github_releases(atom_or_repo_url: str, source_name: str, hours: int = 36) -> list[dict[str, Any]]:
    m = re.search(r"github\.com/([^/]+)/([^/]+)", atom_or_repo_url)
    if not m:
        return []
    owner, repo = m.group(1), m.group(2).removesuffix(".git")
    api = f"https://api.github.com/repos/{owner}/{repo}/releases"
    try:
        with _client() as client:
            r = client.get(api, headers=github_headers(), params={"per_page": 12})
            if r.status_code >= 400:
                return []
            rows = r.json()
    except httpx.HTTPError:
        return []
    if not isinstance(rows, list):
        return []
    items: list[dict[str, Any]] = []
    for row in rows[:12]:
        tag = row.get("tag_name") or row.get("name") or ""
        title = f"{repo} {tag}".strip() or repo
        body = row.get("body") or ""
        published = row.get("published_at") or ""
        if not _within_hours(published, hours):
            continue
        url = row.get("html_url") or f"https://github.com/{owner}/{repo}/releases"
        rid = str(row.get("id") or tag or title)
        items.append(
            {
                "kind": "news",
                "source": f"official:{source_name}",
                "external_id": f"ghrel-{owner}-{repo}-{rid}",
                "title": title,
                "summary": html_to_text(body, 800) if "<" in body else truncate(body, 800),
                "source_url": url,
                "published_at": published,
                "metadata": {"official": True, "org": source_name, "kind": "github_releases"},
            }
        )
    return items


def fetch_github_file(full_name: str, path: str, branch: str) -> str:
    url = f"https://api.github.com/repos/{full_name}/contents/{path.lstrip('/')}"
    try:
        with _client() as client:
            r = client.get(
                url,
                headers={**github_headers(), "Accept": "application/vnd.github.raw"},
                params={"ref": branch},
            )
            if r.status_code >= 400:
                return ""
            return (r.text or "")[:16000]
    except httpx.HTTPError:
        return ""


_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _iso_date(year: int, month: int, day: int) -> str:
    if not (2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}T00:00:00+00:00"


def infer_published_at(*texts: str) -> str:
    blob = " ".join(t or "" for t in texts)
    if not blob.strip():
        return ""
    m = re.search(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", blob)
    if m:
        found = _iso_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if found:
            return found
    m = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
        r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2}),?\s+(20\d{2})\b",
        blob,
        flags=re.I,
    )
    if m:
        found = _iso_date(int(m.group(3)), _MONTHS.get(m.group(1).lower(), 0), int(m.group(2)))
        if found:
            return found
    m = re.search(
        r"\b(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
        r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(20\d{2})\b",
        blob,
        flags=re.I,
    )
    if m:
        found = _iso_date(int(m.group(3)), _MONTHS.get(m.group(2).lower(), 0), int(m.group(1)))
        if found:
            return found
    return ""


def _parse_published(published_at: str) -> datetime | None:
    raw = (published_at or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        dt = None
        for spec in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
            try:
                dt = datetime.strptime(raw, spec)
                break
            except ValueError:
                continue
        if dt is None:
            inferred = infer_published_at(raw)
            if inferred:
                try:
                    dt = datetime.fromisoformat(inferred)
                except ValueError:
                    dt = None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def within_hours(published_at: str, hours: int, *, empty_ok: bool = False) -> bool:
    if not (published_at or "").strip():
        return empty_ok
    dt = _parse_published(published_at)
    if dt is None:
        return empty_ok
    return datetime.now(timezone.utc) - dt <= timedelta(hours=hours)


def _within_hours(published_at: str, hours: int) -> bool:
    return within_hours(published_at, hours, empty_ok=False)


_SKIP_HOME_TITLE = re.compile(
    r"^(home|about|blog|news|careers?|docs?|documentation|research|products?|pricing|contact|"
    r"privacy|login|sign in|github|twitter|linkedin|download|overview|changelog|release notes|"
    r"首页|关于|文档|产品|研究|新闻|新闻中心|加入我们|联系我们|隐私|登录)$",
    re.I,
)


def _headline_items(html: str, source_name: str, page_url: str, limit: int = 8) -> list[dict[str, Any]]:
    text = html_to_text(html, 8000)
    headings = re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", html or "", flags=re.I | re.S)
    titles: list[str] = []
    for raw in headings:
        title = re.sub(r"<[^>]+>", "", raw)
        title = " ".join(title.split())
        if 8 <= len(title) <= 180 and not _SKIP_HOME_TITLE.match(title):
            titles.append(title)
    if not titles:
        for line in text.splitlines():
            line = line.strip()
            if 12 <= len(line) <= 160 and not _SKIP_HOME_TITLE.match(line):
                titles.append(line)
            if len(titles) >= limit:
                break
    items: list[dict[str, Any]] = []
    for title in titles[:limit]:
        digest = hashlib.sha1(f"{page_url}|{title}".encode("utf-8")).hexdigest()[:16]
        items.append(
            {
                "kind": "news",
                "source": f"official:{source_name}",
                "external_id": f"official-{digest}",
                "title": title,
                "summary": text[:800],
                "source_url": page_url,
                "published_at": infer_published_at(title, page_url),
                "metadata": {"official": True, "page": page_url, "org": source_name},
            }
        )
    return items


def _homepage_articles(html: str, source_name: str, page_url: str, limit: int = 8) -> list[dict[str, Any]]:
    base = urlparse(page_url)
    text = html_to_text(html, 8000)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html or "", flags=re.I | re.S):
        href = urljoin(page_url, m.group(1).strip())
        title = re.sub(r"<[^>]+>", "", m.group(2))
        title = " ".join(title.split())
        if len(title) < 8 or len(title) > 180 or _SKIP_HOME_TITLE.match(title):
            continue
        hp = urlparse(href)
        if hp.scheme not in {"http", "https"} or not url_is_safe(href):
            continue
        if hp.netloc and base.netloc and hp.netloc != base.netloc:
            continue
        if href.rstrip("/") == page_url.rstrip("/"):
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        digest = hashlib.sha1(f"{href}|{title}".encode("utf-8")).hexdigest()[:16]
        items.append(
            {
                "kind": "news",
                "source": f"official:{source_name}",
                "external_id": f"official-{digest}",
                "title": title,
                "summary": text[:800],
                "source_url": href,
                "published_at": infer_published_at(href, title),
                "metadata": {"official": True, "page": page_url, "org": source_name, "article": True},
            }
        )
        if len(items) >= limit:
            break
    return items or _headline_items(html, source_name, page_url, limit)


def collect_official_patrol(hours: int = 36) -> tuple[list[dict[str, Any]], list[str]]:
    """Phase 1: hit fixed official entry points. Returns (candidates, gaps)."""
    found: list[dict[str, Any]] = []
    gaps: list[str] = []
    for name, kind, url in OFFICIAL_SOURCES:
        try:
            if kind in {"rss", "github_releases"}:
                batch = collect_rss(url, f"official:{name}") if url else []
                if kind == "rss" and not batch and name == "Meta AI":
                    for alt in (
                        "https://about.fb.com/news/category/technologies/feed/",
                        "https://ai.meta.com/blog/feed/",
                    ):
                        batch = collect_rss(alt, f"official:{name}")
                        if batch:
                            break
                if kind == "github_releases" and not batch and url:
                    batch = collect_github_releases(url, name, hours=hours)
                recent = [x for x in batch if within_hours(x.get("published_at") or "", hours, empty_ok=False)]
                for row in recent:
                    row["kind"] = "news"
                    meta = dict(row.get("metadata") or {})
                    meta.update({"official": True, "org": name, "kind": kind})
                    row["metadata"] = meta
                found.extend(recent)
                if not batch:
                    gaps.append(f"{name}：订阅源无条目或无法解析")
            elif kind == "hf_daily":
                continue
            elif kind == "homepage":
                html, status, err = fetch_url_html(url)
                if status == 0 or status >= 400 or not html:
                    gaps.append(f"{name}：尚未完成核验（{err or status or 'empty'}）")
                    continue
                text = html_to_text(html, 2000)
                if len(text) < 80:
                    gaps.append(f"{name}：页面过短或疑似动态渲染，尚未完成核验")
                    continue
                found.extend(_homepage_articles(html, name, url))
            time.sleep(0.25)
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"{name}：尚未完成核验（{type(exc).__name__}）")
    return found, gaps


def enabled_sources() -> list[dict[str, Any]]:
    out = []
    for src in db.list_sources():
        if src["enabled"]:
            cfg = loads(src["config_json"], {})
            src = dict(src)
            src["config"] = cfg
            out.append(src)
    return out


def _html_title(html: str) -> str:
    found = re.search(r"<title[^>]*>([\s\S]{1,200})</title>", html or "", re.I)
    if not found:
        return ""
    raw = re.sub(r"\s+", " ", found.group(1))
    return html_to_text(raw, 120).replace("\n", " ").strip()


def _arxiv_id_from_url(url: str) -> str:
    found = re.search(
        r"arxiv\.org/(?:abs|pdf|html|ftp)/([a-z\-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?",
        url or "",
        re.I,
    )
    return found.group(1) if found else ""


def _github_from_url(url: str) -> dict[str, str] | None:
    found = re.search(
        r"github\.com/([^/]+)/([^/#?]+)(?:/(blob|tree)/([^/]+)/?(.*))?",
        url or "",
        re.I,
    )
    if not found:
        return None
    owner = found.group(1)
    repo = found.group(2).removesuffix(".git")
    if owner.lower() in {"settings", "orgs", "login", "marketplace", "features", "topics"}:
        return None
    path = (found.group(5) or "").split("?")[0].rstrip("/")
    return {
        "full_name": f"{owner}/{repo}",
        "kind": found.group(3) or "",
        "branch": found.group(4) or "main",
        "path": path,
    }


def ingest_public_url(url: str) -> dict[str, Any]:
    url = (url or "").strip()
    if not url_is_safe(url):
        return {"ok": False, "title": "", "body": "", "source": "web", "error": "地址不安全"}
    arxiv_id = _arxiv_id_from_url(url)
    if arxiv_id:
        return _ingest_arxiv(arxiv_id)
    github = _github_from_url(url)
    if github:
        return _ingest_github(github)
    path = urlparse(url).path.lower()
    if path.endswith(".pdf") or "/pdf/" in path:
        return _ingest_pdf(url)
    return _ingest_html(url)


def _ingest_arxiv(arxiv_id: str) -> dict[str, Any]:
    title = ""
    abstract = ""
    try:
        with _client() as client:
            r = client.get(f"https://export.arxiv.org/api/query?id_list={arxiv_id}")
            if r.status_code < 400:
                ns = {"a": "http://www.w3.org/2005/Atom"}
                root = ET.fromstring(r.text)
                entry = root.find("a:entry", ns)
                if entry is not None:
                    title = " ".join((entry.findtext("a:title", default="", namespaces=ns) or "").split())
                    abstract = " ".join((entry.findtext("a:summary", default="", namespaces=ns) or "").split())
    except (httpx.HTTPError, ET.ParseError):
        pass
    html_body = fetch_arxiv_html(arxiv_id)
    body = html_body or abstract
    if not body:
        pdf = _ingest_pdf(f"https://arxiv.org/pdf/{arxiv_id}")
        if pdf.get("ok") and pdf.get("body"):
            pdf["title"] = title or pdf.get("title") or arxiv_id
            pdf["source"] = "arxiv"
            return pdf
        return {
            "ok": False,
            "title": title or arxiv_id,
            "body": "",
            "source": "arxiv",
            "error": "未能抽出 arXiv 正文",
        }
    return {"ok": True, "title": title or arxiv_id, "body": truncate(body, 16000), "source": "arxiv", "error": ""}


def _ingest_github(info: dict[str, str]) -> dict[str, Any]:
    full_name = info["full_name"]
    path = info.get("path") or ""
    branch = info.get("branch") or "main"
    if info.get("kind") == "blob" and path:
        if path.lower().endswith(".pdf"):
            raw = f"https://raw.githubusercontent.com/{full_name}/{branch}/{path}"
            rec = _ingest_pdf(raw)
            rec["title"] = rec.get("title") or f"{full_name}/{path}"
            rec["source"] = "github"
            return rec
        text = fetch_github_file(full_name, path, branch)
        if text:
            return {"ok": True, "title": f"{full_name}/{path}", "body": truncate(text, 16000), "source": "github", "error": ""}
        return {"ok": False, "title": f"{full_name}/{path}", "body": "", "source": "github", "error": "未能读取该文件"}
    readme = fetch_github_readme(full_name)
    if not readme:
        return {"ok": False, "title": full_name, "body": "", "source": "github", "error": "未能读取 README"}
    return {"ok": True, "title": full_name, "body": truncate(readme, 16000), "source": "github", "error": ""}


def _ingest_pdf(url: str) -> dict[str, Any]:
    from . import pdftool

    rec = pdftool.read_pdf(url, folder="inbox", pages="", extract_figures=False)
    text = rec.get("text") or ""
    if not rec.get("ok") or len(text.strip()) < 80:
        return {
            "ok": False,
            "title": "",
            "body": "",
            "source": "pdf",
            "error": rec.get("error") or "PDF 正文过短",
        }
    return {"ok": True, "title": "", "body": text, "source": "pdf", "error": ""}


def _ingest_html(url: str) -> dict[str, Any]:
    html, status, err = fetch_url_html(url)
    if not html or status >= 400:
        return {"ok": False, "title": "", "body": "", "source": "web", "error": err or f"HTTP {status or 0}"}
    title = _html_title(html)
    body = html_to_text(html, 16000)
    if len(body) < 80:
        return {"ok": False, "title": title, "body": body, "source": "web", "error": "页面过短或像动态渲染"}
    return {"ok": True, "title": title, "body": body, "source": "web", "error": ""}
