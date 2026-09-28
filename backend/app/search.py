"""Search backends. Snippets are clues — callers must fetch the page to confirm."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from . import collect, db
from .config import settings
from .util import html_to_text, truncate, url_is_safe

UA = "YanduStudyAssistant/0.1 (personal research digest)"
MAX_HITS = 6


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(25, connect=12), headers={"User-Agent": UA}, follow_redirects=True)


def _hit(backend: str, title: str, url: str, snippet: str = "", extra: dict | None = None) -> dict[str, Any]:
    return {
        "backend": backend,
        "title": (title or "").strip() or url,
        "url": url,
        "snippet": truncate(html_to_text(snippet, 400) if "<" in (snippet or "") else (snippet or ""), 400),
        **(extra or {}),
    }


def _brave(query: str) -> list[dict[str, Any]]:
    key = db.merged_secret("brave_api_key", settings.brave_api_key, "brave_api_key")
    if not key:
        return []
    with _client() as client:
        r = client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": MAX_HITS},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
        if r.status_code >= 400:
            return []
        data = r.json()
    out = []
    for row in ((data.get("web") or {}).get("results") or [])[:MAX_HITS]:
        url = row.get("url") or ""
        if url_is_safe(url):
            out.append(
                _hit(
                    "web:brave",
                    row.get("title") or "",
                    url,
                    row.get("description") or "",
                    extra={"published_at": row.get("page_age") or ""},
                )
            )
    return out


def _tavily(query: str) -> list[dict[str, Any]]:
    key = db.merged_secret("tavily_api_key", settings.tavily_api_key, "tavily_api_key")
    if not key:
        return []
    with _client() as client:
        r = client.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": query, "max_results": MAX_HITS, "search_depth": "basic"},
        )
        if r.status_code >= 400:
            return []
        data = r.json()
    out = []
    for row in (data.get("results") or [])[:MAX_HITS]:
        url = row.get("url") or ""
        if url_is_safe(url):
            out.append(
                _hit(
                    "web:tavily",
                    row.get("title") or "",
                    url,
                    row.get("content") or "",
                    extra={"published_at": row.get("published_date") or ""},
                )
            )
    return out


def _unwrap_ddg(href: str) -> str:
    if "uddg=" in href:
        qs = parse_qs(urlparse(href).query)
        if qs.get("uddg"):
            return unquote(qs["uddg"][0])
    return href


def _duckduckgo(query: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with _client() as client:
            r = client.post("https://html.duckduckgo.com/html/", data={"q": query})
            if r.status_code >= 400:
                return []
            html = r.text or ""
    except httpx.HTTPError:
        return []
    for m in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</(?:a|td)',
        html,
        flags=re.I | re.S,
    ):
        url = _unwrap_ddg(m.group(1))
        if not url.startswith("http") or not url_is_safe(url):
            continue
        title = re.sub(r"<[^>]+>", "", m.group(2))
        snippet = re.sub(r"<[^>]+>", "", m.group(3))
        out.append(_hit("web:ddg", title, url, snippet))
        if len(out) >= MAX_HITS:
            break
    if out:
        return out
    for m in re.finditer(r'href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, flags=re.I | re.S):
        url = _unwrap_ddg(m.group(1))
        if "duckduckgo.com" in url or not url_is_safe(url):
            continue
        title = re.sub(r"<[^>]+>", " ", m.group(2))
        title = " ".join(title.split())
        if len(title) < 8:
            continue
        out.append(_hit("web:ddg", title, url, ""))
        if len(out) >= MAX_HITS:
            break
    return out


def search_web(query: str) -> tuple[list[dict[str, Any]], str]:
    for fn, name in ((_brave, "brave"), (_tavily, "tavily"), (_duckduckgo, "ddg")):
        hits = fn(query)
        if hits:
            return hits, name
    return [], "web-empty"


def search_github(query: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with _client() as client:
            r = client.get(
                "https://api.github.com/search/repositories",
                headers=collect.github_headers(),
                params={"q": query, "per_page": MAX_HITS, "sort": "stars", "order": "desc"},
            )
            if r.status_code >= 400:
                return []
            data = r.json()
    except httpx.HTTPError:
        return []
    for row in (data.get("items") or [])[:MAX_HITS]:
        url = row.get("html_url") or ""
        if url_is_safe(url):
            out.append(
                _hit(
                    "github",
                    row.get("full_name") or "",
                    url,
                    row.get("description") or "",
                    extra={"stars": row.get("stargazers_count"), "pushed_at": row.get("pushed_at")},
                )
            )
    return out


def search_hf(query: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with _client() as client:
            r = client.get(
                "https://huggingface.co/api/models",
                params={"search": query, "limit": MAX_HITS, "sort": "downloads", "direction": -1},
            )
            if r.status_code >= 400:
                return []
            rows = r.json()
    except httpx.HTTPError:
        return []
    if not isinstance(rows, list):
        return []
    for row in rows[:MAX_HITS]:
        mid = row.get("id") or row.get("modelId") or ""
        url = f"https://huggingface.co/{mid}" if mid else ""
        if mid and url_is_safe(url):
            out.append(_hit("huggingface", mid, url, (row.get("pipeline_tag") or "") + " " + str(row.get("downloads") or "")))
    return out


def search_arxiv(query: str) -> list[dict[str, Any]]:
    items = collect.collect_arxiv_query(query, max_results=MAX_HITS, sort="relevance")
    return [
        _hit(
            "arxiv",
            x["title"],
            x.get("source_url") or "",
            x.get("summary") or "",
            extra={"arxiv_id": x.get("external_id"), "published_at": x.get("published_at") or ""},
        )
        for x in items
    ]


def search_s2(query: str) -> list[dict[str, Any]]:
    try:
        with _client() as client:
            r = client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={
                    "query": query,
                    "limit": MAX_HITS,
                    "fields": "title,url,abstract,year,citationCount,externalIds,openAccessPdf",
                },
            )
            if r.status_code >= 400:
                return []
            data = r.json()
    except httpx.HTTPError:
        return []
    out = []
    for row in (data.get("data") or [])[:MAX_HITS]:
        url = row.get("url") or ""
        pdf = ((row.get("openAccessPdf") or {}) or {}).get("url") or ""
        if not url and row.get("externalIds", {}).get("ArXiv"):
            url = f"https://arxiv.org/abs/{row['externalIds']['ArXiv']}"
        if url and url_is_safe(url):
            out.append(
                _hit(
                    "s2",
                    row.get("title") or "",
                    url,
                    row.get("abstract") or "",
                    extra={
                        "citations": row.get("citationCount"),
                        "year": row.get("year"),
                        "pdf": pdf,
                        "published_at": f"{row.get('year')}-01-01" if row.get("year") else "",
                    },
                )
            )
    return out


def search_library(query: str) -> list[dict[str, Any]]:
    rows = db.list_items(q=query, status="published", limit=8)
    out = []
    for row in rows:
        out.append(
            _hit(
                "library",
                row.get("title") or "",
                f"/item/{row['id']}",
                row.get("summary") or "",
                extra={"item_id": row["id"], "kind": row.get("kind")},
            )
        )
    return out


def search(backend: str, query: str) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"backend": backend, "hits": [], "note": "empty query"}
    backend = (backend or "web").lower()
    note = ""
    if backend in {"web", "ddg", "brave", "tavily"}:
        hits, used = search_web(q)
        note = f"engine={used}; snippets are not confirmation"
        backend = "web"
    elif backend in {"github", "gh"}:
        hits = search_github(q)
    elif backend in {"huggingface", "hf"}:
        hits = search_hf(q)
    elif backend == "arxiv":
        hits = search_arxiv(q)
    elif backend in {"s2", "scholar", "semanticscholar"}:
        hits = search_s2(q)
    elif backend in {"library", "yandu"}:
        hits = search_library(q)
    else:
        return {"backend": backend, "hits": [], "note": f"unknown backend {backend}"}
    return {"backend": backend, "query": q, "hits": hits, "note": note or "fetch hit URLs before treating as fact"}


def hits_as_candidates(hits: list[dict[str, Any]], source_prefix: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for h in hits:
        url = h.get("url") or ""
        if not url or url.startswith("/"):
            continue
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        backend = h.get("backend") or "web"
        arxiv_id = h.get("arxiv_id") or ""
        items.append(
            {
                "kind": "news" if backend != "arxiv" else "paper",
                "source": f"{source_prefix}:{backend}",
                "external_id": arxiv_id or f"search-{digest}",
                "title": h.get("title") or url,
                "summary": h.get("snippet") or "",
                "source_url": url,
                "published_at": h.get("published_at") or h.get("published_date") or h.get("pushed_at") or "",
                "metadata": {
                    "search": True,
                    "backend": backend,
                    "official": False,
                    "stars": h.get("stars"),
                    "citations": h.get("citations"),
                    "year": h.get("year"),
                    "arxiv_id": arxiv_id,
                },
            }
        )
    return items
