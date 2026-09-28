from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from . import agent, collect, db, llm, media, notify, prompts_loader, search
from .config import settings
from .cost import Usage
from .official_sources import (
    AGENTIC_ARXIV,
    AGENTIC_CLASSICS,
    AGENTIC_SEARCHES,
    FRONTIER_SEARCHES,
    NEWS_FEEDS,
    headline_is_ai,
)
from .util import dumps, extract_json, loads, money_label, now_iso, today_str, to_simplified, truncate, looks_like_pick_reason, one_line_blurb


def _log(run_id: int, msg: str) -> None:
    db.append_log(run_id, msg)


def _seen(source: str, external_id: str) -> bool:
    return db.find_item(source, external_id) is not None


def _written(source: str, external_id: str) -> bool:
    rec = db.find_item(source, external_id)
    return rec is not None and rec.get("status") == "published"


def _finish(run_id: int, usage: Usage, status: str = "ok", error: str | None = None) -> None:
    db.finish_run(
        run_id,
        status,
        token_in_hit=usage.hit,
        token_in_miss=usage.miss,
        token_out=usage.out,
        cost_usd=usage.cost_usd(),
        error=error,
    )
    if status == "ok":
        _log(run_id, f"完成 tokens hit/miss/out={usage.hit}/{usage.miss}/{usage.out} cost≈{money_label(usage.cost_usd())}")


def _parse_dt(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


LINUX_DO_FEEDS = (
    "https://linux.do/latest.rss",
    "https://linux.do/c/technology/14.rss",
)

_MEDIA_NAMES = {name for name, _kind, _url in NEWS_FEEDS}


def _source_bucket(candidate: dict[str, Any]) -> str:
    meta = candidate.get("metadata") or {}
    source = (candidate.get("source") or "").lower()
    if meta.get("official") or source.startswith("official:"):
        return "official"
    if meta.get("media") or candidate.get("source") in _MEDIA_NAMES:
        return "media"
    if "linux.do" in source:
        return "community"
    if source.startswith("open:"):
        return "open"
    return "other"


def _title_tokens(title: str) -> set[str]:
    raw = to_simplified(title or "").lower()
    tokens: set[str] = set()
    for word in re.findall(r"[a-z0-9]{3,}", raw):
        if word not in {"the", "and", "for", "with", "from", "new", "api"}:
            tokens.add(word)
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", raw):
        if len(chunk) >= 2:
            tokens.add(chunk)
            if len(chunk) >= 4:
                for i in range(0, len(chunk) - 1, 2):
                    tokens.add(chunk[i : i + 2])
    return tokens


_EVENT_RES = [
    ("wenxin-5", re.compile(r"文心\s*(大模型)?\s*5(\.0)?", re.I)),
    ("deepseek-v4", re.compile(r"deepseek[\s\-]*v?\.?4|深度求索.{0,8}v?4", re.I)),
    ("deepseek-ocr", re.compile(r"deepseek[\s\-]*ocr", re.I)),
    ("qwen3-omni", re.compile(r"qwen\s*3[\s\-]?omni|千问\s*3[\s\-]?omni", re.I)),
    ("ernie-5", re.compile(r"ernie\s*5|文心一言\s*5", re.I)),
]


def event_key(title: str) -> str:
    raw = to_simplified(title or "")
    for key, pat in _EVENT_RES:
        if pat.search(raw):
            return key
    return ""


def same_event(a: str, b: str) -> bool:
    ka, kb = event_key(a), event_key(b)
    if ka and kb and ka == kb:
        return True
    return _titles_match(a, b)


def _titles_match(a: str, b: str) -> bool:
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    union = len(ta | tb)
    if union and inter / union >= 0.32:
        return True
    na = re.sub(r"\s+", "", (a or "").lower())
    nb = re.sub(r"\s+", "", (b or "").lower())
    if len(na) >= 12 and len(nb) >= 12 and (na in nb or nb in na):
        return True
    return False


def annotate_cross_verification(candidates: list[dict[str, Any]]) -> None:
    clusters: list[list[dict[str, Any]]] = []
    for c in candidates:
        placed = False
        for cluster in clusters:
            if any(same_event(c.get("title") or "", other.get("title") or "") for other in cluster):
                cluster.append(c)
                placed = True
                break
        if not placed:
            clusters.append([c])
    for cluster in clusters:
        buckets = {_source_bucket(x) for x in cluster}
        buckets.discard("other")
        names = sorted({(x.get("source") or "")[:40] for x in cluster})
        for c in cluster:
            c["verify_count"] = max(1, len(buckets))
            c["verify_sources"] = names[:6]
            c["verify_buckets"] = sorted(buckets)


def _prefetch_ok(excerpt: str) -> bool:
    text = (excerpt or "").strip()
    if len(text) < 180:
        return False
    if "抓取失败" in text or "尚未完成核验" in text:
        return False
    return True


def prefetch_for_triage(candidates: list[dict[str, Any]], run_id: int, limit: int = 40) -> None:
    def _rank(c: dict[str, Any]) -> tuple:
        dt = collect._parse_published(c.get("published_at") or "")
        stamp = dt.timestamp() if dt else 0.0
        meta = c.get("metadata") or {}
        return (
            stamp,
            int(c.get("verify_count") or 1),
            int(bool(meta.get("official"))),
            int(bool(meta.get("media"))),
        )

    ranked = sorted(candidates, key=_rank, reverse=True)
    fetched = 0
    for c in ranked:
        if fetched >= limit:
            c["prefetch_ok"] = _prefetch_ok(c.get("excerpt") or c.get("summary") or "")
            continue
        url = c.get("source_url") or ""
        if c.get("source") == "arxiv":
            extra = collect.fetch_arxiv_html(c["external_id"])
        elif url:
            try:
                extra = collect.fetch_url_text(url)
            except Exception as exc:  # noqa: BLE001
                extra = f"（抓取失败: {type(exc).__name__}，尚未完成核验）"
        else:
            extra = c.get("summary") or ""
        c["excerpt"] = truncate(extra or c.get("summary") or "", 3500)
        c["prefetch_ok"] = _prefetch_ok(c["excerpt"])
        if url:
            fetched += 1
    _log(run_id, f"预抓取 {fetched} 条正文用于筛选")


def apply_verification_boost(candidates: list[dict[str, Any]]) -> None:
    for c in candidates:
        base = float(c.get("score") or 0)
        vc = int(c.get("verify_count") or 1)
        buckets = set(c.get("verify_buckets") or [])
        boost = 0.0
        if vc >= 3:
            boost = 2.0
        elif vc >= 2:
            boost = 1.5
        if vc >= 2 and "official" in buckets:
            boost += 0.5
        if boost:
            c["score_base"] = base
            c["score"] = min(10.0, base + boost)
            c["score_boost"] = boost


def select_frontier_kept(scored: list[dict[str, Any]], min_score: float, explain_count: int) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for c in scored:
        score = float(c.get("score") or 0)
        vc = int(c.get("verify_count") or 1)
        prefetch_ok = bool(c.get("prefetch_ok"))
        if c.get("keep") and score >= min_score:
            kept.append(c)
            continue
        if score >= min_score and prefetch_ok and vc >= 2:
            c["keep"] = True
            reason = (c.get("reason") or "").strip()
            c["reason"] = truncate((reason + "；多方印证") if reason else "多方印证", 40)
            kept.append(c)
    kept.sort(key=lambda x: x.get("score") or 0, reverse=True)
    deduped: list[dict[str, Any]] = []
    for c in kept:
        if any(same_event(c.get("title") or "", k.get("title") or "") for k in deduped):
            continue
        deduped.append(c)
    n = max(1, explain_count)
    return deduped[:n]


def frontier_leads(scored: list[dict[str, Any]], kept: list[dict[str, Any]], min_score: float) -> list[dict[str, Any]]:
    kept_ids = {(c["source"], c["external_id"]) for c in kept}
    leads: list[dict[str, Any]] = []
    for c in scored:
        key = (c["source"], c["external_id"])
        if key in kept_ids:
            continue
        score = float(c.get("score") or 0)
        vc = int(c.get("verify_count") or 1)
        if score >= min_score - 1 and vc >= 2:
            leads.append(c)
    leads.sort(key=lambda x: (x.get("verify_count") or 0, x.get("score") or 0), reverse=True)
    return leads[:5]


def gather_open_search(config: dict[str, Any], run_id: int, hours: int = 36) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for name, kind, url in NEWS_FEEDS:
        try:
            broad = kind == "rss_broad"
            if kind == "wordpress":
                batch = collect.collect_wordpress(url, name)
            elif "linux.do" in (url or ""):
                batch: list[dict[str, Any]] = []
                for feed in LINUX_DO_FEEDS:
                    batch = collect.collect_rss(feed, name)
                    if batch:
                        break
            else:
                batch = collect.collect_rss(url, name)
            fresh = [x for x in batch if x.get("external_id") and not _seen(x["source"], x["external_id"])]
            if broad:
                fresh = [
                    x
                    for x in fresh
                    if headline_is_ai(x.get("title") or "", x.get("summary") or "")
                ]
            fresh = [x for x in fresh if _stamp_and_keep(x, hours, unknown_ok=True)]
            for row in fresh:
                meta = dict(row.get("metadata") or {})
                meta["media"] = True
                row["metadata"] = meta
            _log(run_id, f"媒体 {name}: {len(batch)} 条，去重后 {len(fresh)} 条")
            found.extend(fresh)
        except Exception as exc:  # noqa: BLE001
            _log(run_id, f"媒体失败 {name}: {type(exc).__name__}")
    sources = collect.enabled_sources()
    for src in sources:
        typ = src["type"]
        try:
            if typ == "arxiv":
                if not config.get("include_arxiv"):
                    continue
                cats = src["config"].get("categories") or ["cs.AI", "cs.LG", "cs.CL"]
                mx = int(config.get("arxiv_max") or src["config"].get("max_results") or 40)
                batch = collect.collect_arxiv(cats, mx)
            elif typ == "hf_daily":
                if not config.get("include_hf_daily"):
                    continue
                batch = collect.collect_hf_daily()
            elif typ == "rss":
                url = src["config"].get("url") or ""
                batch = collect.collect_rss(url, src["name"]) if url else []
            else:
                batch = []
            new = [x for x in batch if x.get("external_id") and not _seen(x["source"], x["external_id"])]
            new = [x for x in new if _stamp_and_keep(x, hours, unknown_ok=True)]
            _log(run_id, f"开放检索 {src['name']}: {len(batch)} 条，去重后 {len(new)} 条")
            found.extend(new)
        except Exception as exc:  # noqa: BLE001
            _log(run_id, f"开放检索失败 {src['name']}: {type(exc).__name__}")
    for backend, query in FRONTIER_SEARCHES:
        try:
            rec = search.search(backend, query)
            batch = search.hits_as_candidates(rec.get("hits") or [], "open")
            new = [x for x in batch if x.get("external_id") and not _seen(x["source"], x["external_id"])]
            new = [x for x in new if _stamp_and_keep(x, hours, unknown_ok=True)]
            _log(run_id, f"开放检索 {backend}:{query[:48]} → {len(batch)}/{len(new)} engine={rec.get('note','')[:40]}")
            found.extend(new)
        except Exception as exc:  # noqa: BLE001
            _log(run_id, f"开放检索失败 {backend}: {type(exc).__name__}")
    return found


def _stamp_and_keep(item: dict[str, Any], hours: int, *, unknown_ok: bool) -> bool:
    pub = (item.get("published_at") or "").strip()
    if not pub:
        pub = collect.infer_published_at(
            item.get("source_url") or "",
            item.get("title") or "",
            item.get("excerpt") or "",
            item.get("summary") or "",
        )
        if pub:
            item["published_at"] = pub
    if not pub:
        return unknown_ok
    return collect.within_hours(pub, hours, empty_ok=False)


def _frontier_admissible(item: dict[str, Any], hours: int) -> tuple[bool, str]:
    excerpt = item.get("excerpt") or ""
    if "抓取失败" in excerpt or "尚未完成核验" in excerpt:
        return False, "正文未核验"
    if not _stamp_and_keep(item, hours, unknown_ok=False):
        return False, "无窗口内日期"
    if item.get("prefetch_ok"):
        return True, ""
    meta = item.get("metadata") or {}
    official = bool(meta.get("official"))
    kind = meta.get("kind") or ""
    if official and kind in {"rss", "github_releases"} and len((item.get("summary") or "")) >= 180:
        return True, ""
    return False, "正文未核验"


def filter_frontier_window(candidates: list[dict[str, Any]], hours: int, run_id: int) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    dropped = 0
    for c in candidates:
        ok, why = _frontier_admissible(c, hours)
        if ok:
            kept.append(c)
        else:
            dropped += 1
            c["keep"] = False
            c["reason"] = why
    _log(run_id, f"时效/核验过滤去掉 {dropped} 条，窗口内 {len(kept)} 条")
    return kept


def triage(candidates: list[dict[str, Any]], usage: Usage, run_id: int, prompt_name: str = "triage", extra_system: str = "") -> list[dict[str, Any]]:
    if not candidates:
        return []
    prompt = prompts_loader.load(prompt_name)
    if extra_system:
        prompt = prompt.rstrip() + "\n\n" + extra_system.strip()
    batch_size = 8
    for i in range(0, len(candidates), batch_size):
        chunk = candidates[i : i + batch_size]
        payload = []
        for idx, c in enumerate(chunk):
            body = c.get("excerpt") or c.get("summary") or ""
            payload.append(
                {
                    "id": str(idx),
                    "title": c["title"],
                    "abstract": truncate(body, 1200),
                    "source": c["source"],
                    "url": c.get("source_url") or "",
                    "official": bool((c.get("metadata") or {}).get("official")),
                    "published_at": c.get("published_at") or "",
                    "prefetch_ok": bool(c.get("prefetch_ok")),
                    "verify_count": int(c.get("verify_count") or 1),
                    "verify_sources": c.get("verify_sources") or [],
                    "classic": bool((c.get("metadata") or {}).get("classic")),
                }
            )
        user = "<<<SOURCE>>>\n" + dumps(payload) + "\n<<<END_SOURCE>>>"
        raw = llm.chat(
            [{"role": "system", "content": prompt}, {"role": "user", "content": user}],
            thinking=False,
            usage=usage,
            temperature=0.1,
        )
        try:
            parsed = extract_json(raw)
            rows = parsed.get("items") if isinstance(parsed, dict) else parsed
        except Exception:  # noqa: BLE001
            _log(run_id, f"筛选 JSON 解析失败 @ batch {i}")
            continue
        by_id = {str(r.get("id")): r for r in (rows or []) if isinstance(r, dict)}
        for idx, c in enumerate(chunk):
            verdict = by_id.get(str(idx), {})
            c["score"] = float(verdict.get("score") or 0)
            c["keep"] = bool(verdict.get("keep"))
            c["reason"] = verdict.get("reason") or ""
            c["tags"] = verdict.get("tags") or []
            c["release_status"] = verdict.get("release_status") or ""
    keep_n = sum(1 for c in candidates if c.get("keep"))
    _log(run_id, f"筛选完成：候选 {len(candidates)}，模型 keep={keep_n}")
    return candidates


def _store_skipped(candidates: list[dict[str, Any]], kept_ids: set[tuple[str, str]]) -> None:
    for c in candidates:
        if (c["source"], c["external_id"]) in kept_ids or _seen(c["source"], c["external_id"]):
            continue
        meta = dict(c.get("metadata") or {})
        if c.get("verify_count"):
            meta["verify_count"] = c.get("verify_count")
            meta["verify_sources"] = c.get("verify_sources") or []
        if c.get("score_boost"):
            meta["score_boost"] = c.get("score_boost")
        try:
            db.insert_item(
                {
                    "kind": c.get("kind") or "news",
                    "status": "skipped",
                    "title": c["title"],
                    "summary": c.get("reason") or truncate(c.get("summary") or "", 240),
                    "body_md": "",
                    "source": c["source"],
                    "source_url": c.get("source_url") or "",
                    "external_id": c["external_id"],
                    "score": c.get("score"),
                    "tags_json": dumps(c.get("tags") or []),
                    "metadata_json": dumps(meta),
                    "unread": False,
                    "published_at": c.get("published_at"),
                }
            )
        except Exception:  # noqa: BLE001
            pass


def write_news_brief(item: dict[str, Any], usage: Usage) -> str:
    prompt = prompts_loader.load("news")
    payload = {
        "title": item.get("title"),
        "url": item.get("source_url"),
        "source": item.get("source"),
        "published_at": item.get("published_at"),
        "reason": item.get("reason"),
        "release_status": item.get("release_status"),
        "excerpt": truncate(item.get("excerpt") or item.get("summary") or "", 3500),
        "community": "linux.do" in (item.get("source") or "").lower()
        or "linux.do" in (item.get("source_url") or "").lower(),
    }
    user = "<<<SOURCE>>>\n" + dumps(payload) + "\n<<<END_SOURCE>>>"
    text = llm.chat(
        [{"role": "system", "content": prompt}, {"role": "user", "content": user}],
        thinking=False,
        usage=usage,
        temperature=0.2,
        timeout=90,
    )
    return to_simplified(re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text or ""))


def enrich_candidate(item: dict[str, Any], usage: Usage, run_id: int, deep: bool = False) -> dict[str, Any]:
    extra = ""
    url = item.get("source_url") or ""
    if item.get("source") == "arxiv":
        extra = collect.fetch_arxiv_html(item["external_id"])
    elif url:
        try:
            extra = collect.fetch_url_text(url)
        except Exception as exc:  # noqa: BLE001
            extra = f"（抓取失败: {type(exc).__name__}，尚未完成核验）"
    item = dict(item)
    item["excerpt"] = truncate(extra, 6000)
    item["research_notes"] = ""
    item["official_figures"] = []
    if deep and url:
        folder = f"frontier/{item.get('external_id') or 'x'}".replace("/", "_")[:80]
        rec = agent.run(
            (
                f"核实并补充这篇即将写入日报的条目。\n标题：{item.get('title')}\nURL：{url}\n"
                f"摘要：{truncate(item.get('summary') or '', 800)}\n"
                "用 search + fetch_url 打开官方页，区分传闻/Preview/Beta/GA/API/权重。"
                "需要机制细节时再读文档或 PDF。不要写完整日报，只交核实笔记。"
            ),
            usage=usage,
            folder=folder,
            max_rounds=5,
            extra_system="这是日报主篇 enrichment，控制在几次工具内。",
        )
        item["research_notes"] = rec.get("notes") or ""
        item["official_figures"] = rec.get("figures") or []
        _log(run_id, f"主篇检索 {item.get('title','')[:40]} rounds={rec.get('rounds')}")
    return item


def run_frontier(task: dict[str, Any], run_id: int) -> None:
    usage = Usage()
    config = loads(task.get("config_json") or "{}", {})
    min_score = float(config.get("min_score") or 7)
    explain_count = int(config.get("explain_count") or 8)
    hours = int(config.get("window_hours") or 36)
    day = today_str()
    try:
        official, gaps = collect.collect_official_patrol(hours=hours)
        _log(run_id, f"固定源巡检 {len(official)} 条，缺口 {len(gaps)}")
        for gap in gaps:
            _log(run_id, f"核验缺口 {gap}")
        open_found = gather_open_search(config, run_id, hours=hours)
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for row in official + open_found:
            if (row.get("kind") or "") == "paper" or row.get("source") in {"arxiv", "hf_daily"}:
                continue
            key = (row.get("source") or "", row.get("external_id") or "")
            if not key[1]:
                continue
            merged[key] = row
        _log(run_id, f"候选合计 {len(merged)}")
        candidates = [v for k, v in merged.items() if not _seen(k[0], k[1])]
        annotate_cross_verification(candidates)
        cross_n = sum(1 for c in candidates if (c.get("verify_count") or 1) >= 2)
        _log(run_id, f"多方印证线索 {cross_n} 条")
        prefetch_for_triage(candidates, run_id)
        candidates = filter_frontier_window(candidates, hours, run_id)
        scored = triage(candidates, usage, run_id, "triage")
        apply_verification_boost(scored)
        kept = select_frontier_kept(scored, min_score, explain_count)
        leads = frontier_leads(scored, kept, min_score)
        _log(run_id, f"最终入选 {len(kept)} 条，待跟进线索 {len(leads)} 条")
        _store_skipped(scored, {(k["source"], k["external_id"]) for k in kept})
        published: list[dict[str, Any]] = []
        for c in kept:
            url = c.get("source_url") or ""
            c = dict(c)
            if not c.get("excerpt"):
                extra = ""
                if url:
                    try:
                        extra = collect.fetch_url_text(url)
                    except Exception as exc:  # noqa: BLE001
                        extra = f"（抓取失败: {type(exc).__name__}，尚未完成核验）"
                c["excerpt"] = truncate(extra or c.get("summary") or "", 4000)
                c["prefetch_ok"] = _prefetch_ok(c["excerpt"])
            ok, why = _frontier_admissible(c, hours)
            if not ok:
                _log(run_id, f"落笔前丢弃 {c.get('title','')[:40]}：{why}")
                continue
            try:
                body = write_news_brief(c, usage)
            except Exception as exc:  # noqa: BLE001
                _log(run_id, f"快讯失败 {c.get('title','')[:40]}: {type(exc).__name__}")
                continue
            if not (body or "").strip() or (body or "").strip().upper().startswith("SKIP"):
                _log(run_id, f"模型拒写 {c.get('title','')[:40]}")
                continue
            try:
                rec = db.insert_item(
                    {
                        "kind": "news",
                        "status": "published",
                        "title": to_simplified(c["title"]),
                        "summary": to_simplified(c.get("reason") or truncate(c.get("summary") or "", 240)),
                        "body_md": to_simplified(body),
                        "source": c["source"],
                        "source_url": url,
                        "external_id": c["external_id"],
                        "score": c.get("score"),
                        "tags_json": dumps(c.get("tags") or ["news"]),
                        "metadata_json": dumps(
                            {
                                **(c.get("metadata") or {}),
                                "verify_count": c.get("verify_count"),
                                "verify_sources": c.get("verify_sources") or [],
                                "score_boost": c.get("score_boost"),
                            }
                        ),
                        "published_at": c.get("published_at") or "",
                        "unread": True,
                    }
                )
                published.append(rec)
                _log(run_id, f"快讯 {c.get('title','')[:48]}")
            except Exception as exc:  # noqa: BLE001
                _log(run_id, f"入库失败 {c.get('title','')[:40]}: {type(exc).__name__}")
        lines = [f"- [{p['title']}](/item/{p['id']}) — {p.get('summary') or ''}" for p in published]
        lead_lines = [
            f"- {c.get('title')}（{c.get('verify_count')} 方线索，{c.get('reason') or '待核实'}）"
            for c in leads
        ]
        if published:
            body = f"# {day} 前沿快讯\n\n" + "\n".join(lines)
            summary = f"今日 {len(published)} 条已确认快讯"
        elif gaps and leads:
            body = (
                f"# {day} 前沿快讯\n\n部分官方源未核验；以下线索有多方报道，但尚未写成快讯。\n\n"
                + "\n".join(lead_lines)
                + "\n\n## 官方源缺口\n\n"
                + "\n".join(f"- {g}" for g in gaps)
            )
            summary = "有待核实线索，部分官方源未核验"
        elif gaps:
            body = (
                f"# {day} 前沿快讯\n\n部分官方源未核验，今日无已确认快讯。\n\n"
                + "\n".join(f"- {g}" for g in gaps)
            )
            summary = "部分官方源未核验，今日无已确认快讯"
        elif leads:
            body = f"# {day} 前沿快讯\n\n以下线索有多方报道，待进一步核实。\n\n" + "\n".join(lead_lines)
            summary = "有待跟进线索"
        else:
            body = f"# {day} 前沿快讯\n\n过去 {hours} 小时没有筛到足够重要的产品/行业新闻。"
            summary = "暂无足够重要的新闻"
        digest = db.find_item("yandu", f"digest-{day}")
        payload = {
            "kind": "digest",
            "status": "published",
            "title": f"{day} 前沿快讯",
            "summary": summary[:240],
            "body_md": body,
            "source": "yandu",
            "external_id": f"digest-{day}",
            "tags_json": dumps(["frontier", "news"]),
            "metadata_json": dumps(
                {
                    "gaps": gaps,
                    "titles": [p["title"] for p in published],
                    "leads": [c.get("title") for c in leads],
                }
            ),
            "unread": True,
        }
        if digest:
            rec = (
                db.update_item(
                    digest["id"],
                    title=payload["title"],
                    summary=payload["summary"],
                    body_md=body,
                    unread=1,
                    metadata_json=payload["metadata_json"],
                )
                or digest
            )
        else:
            rec = db.insert_item(payload)
        if published:
            result = notify.send_card(
                f"今日快讯 · {len(published)} 条",
                "\n".join(p["title"] for p in published),
                rec["id"],
            )
        else:
            result = notify.send_skip("今日快讯", rec.get("summary") or "今日无快讯")
        _log(run_id, f"推送: {result}")
        _finish(run_id, usage)
    except Exception as exc:  # noqa: BLE001
        _log(run_id, f"失败: {type(exc).__name__}: {exc}")
        _finish(run_id, usage, "failed", type(exc).__name__)
        raise


def sync_github_ledger(username: str, run_id: int) -> tuple[list[dict[str, Any]], bool, str]:
    stars, complete, sync_err = collect.collect_github_stars(username)
    _log(run_id, f"Star 同步 {len(stars)} 个公开收藏，complete={complete} {sync_err}".strip())
    if sync_err and not stars:
        _log(run_id, f"公开 Star 列表不可用：{sync_err}。请核对 GitHub 用户名是否存在、是否改名。")
        return stars, complete, sync_err
    seen_ids: set[str] = set()
    for repo in stars:
        ext = repo["external_id"]
        seen_ids.add(ext)
        meta = repo.get("metadata") or {}
        existing = db.find_github_star(meta.get("repo_id"), meta.get("full_name") or repo["title"])
        if not existing:
            db.insert_item(
                {
                    "kind": "github_star",
                    "status": "queued",
                    "title": repo["title"],
                    "summary": repo.get("summary") or "",
                    "body_md": "",
                    "source": "github",
                    "source_url": repo.get("source_url") or "",
                    "external_id": ext,
                    "unread": False,
                    "published_at": repo.get("published_at"),
                    "metadata_json": dumps(meta),
                    "tags_json": dumps(["github", "queued"]),
                }
            )
            continue
        merged = loads(existing.get("metadata_json") or "{}", {})
        merged.update(meta)
        fields: dict[str, Any] = {
            "title": repo["title"],
            "source_url": repo.get("source_url") or existing.get("source_url") or "",
            "metadata_json": dumps(merged),
        }
        if existing.get("external_id") != ext:
            fields["external_id"] = ext
        if existing.get("status") == "unstarred":
            fields["status"] = "queued"
        if existing.get("status") != "published":
            fields["summary"] = repo.get("summary") or existing.get("summary") or ""
        try:
            db.update_item(existing["id"], **fields)
        except Exception:  # noqa: BLE001
            _log(run_id, f"账本写回冲突，保留原行: {repo['title']}")
    if complete:
        ledger = db.list_items(kind="github_star", source="github", limit=500)
        for row in ledger:
            if row["status"] not in {"queued", "retry"}:
                continue
            if row.get("external_id") in seen_ids:
                continue
            db.update_item(row["id"], status="unstarred")
            _log(run_id, f"完整同步后移出队列（保留报告）: {row['title']}")
    else:
        _log(run_id, "同步未完成，不把缺席项目当作取消收藏")
    return stars, complete, sync_err


def pick_github_star(config: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    rows = [
        row
        for row in db.list_items(kind="github_star", source="github", limit=400, order="created_at ASC")
        if row.get("status") in {"queued", "retry"}
    ]
    if not rows:
        return None, "无待分析项目"
    prefer = (config.get("prefer_repo") or "").strip()
    if prefer:
        for row in rows:
            meta = loads(row.get("metadata_json") or "{}", {})
            hay = f"{row.get('title') or ''} {meta.get('full_name') or ''}"
            if prefer.lower() in hay.lower():
                return row, f"你指定优先：{prefer}"
    now = datetime.now(timezone.utc)
    fresh: list[tuple[datetime, dict[str, Any]]] = []
    old: list[dict[str, Any]] = []
    for row in rows:
        meta = loads(row.get("metadata_json") or "{}", {})
        starred = _parse_dt(str(meta.get("starred_at") or row.get("published_at") or ""))
        created = _parse_dt(row.get("created_at") or "") or now
        if starred and (now - starred) <= timedelta(days=14):
            fresh.append((starred, row))
        else:
            old.append(row)
        row["_created"] = created
    old.sort(key=lambda r: r.get("created_at") or "")
    fresh.sort(key=lambda x: x[0], reverse=True)
    if old:
        oldest = old[0]
        created = _parse_dt(oldest.get("created_at") or "")
        if created and (now - created) >= timedelta(days=21):
            return oldest, "消化历史收藏，避免长期饥饿"
    if fresh:
        return fresh[0][1], "新收藏优先"
    if old:
        return old[0], "继续消化历史队列"
    return rows[0], "队列中的下一个"


def _star_figures(full_name: str, branch: str, readme: str, folder: str) -> list[dict[str, Any]]:
    base = f"https://raw.githubusercontent.com/{full_name}/{branch}/"
    urls = media.extract_image_urls(readme, base)
    saved: list[dict[str, Any]] = []
    for i, url in enumerate(urls, start=1):
        raw = media.github_raw_url(url, full_name, branch)
        rec = media.save_remote_image(folder, raw, i)
        rec["caption_hint"] = url
        saved.append(rec)
    return saved


def write_github_star(item: dict[str, Any], usage: Usage, pick_reason: str) -> tuple[str, list[dict[str, Any]]]:
    meta = item.get("metadata") or loads(item.get("metadata_json") or "{}", {})
    full_name = meta.get("full_name") or item.get("title") or ""
    branch = meta.get("default_branch") or "main"
    readme = collect.fetch_github_readme(full_name)
    tree = collect.fetch_github_tree(full_name, branch)
    extra_files: dict[str, str] = {}
    for path in ("LICENSE", "LICENSE.md", "pyproject.toml", "package.json", "CITATION.cff"):
        text = collect.fetch_github_file(full_name, path, branch)
        if text:
            extra_files[path] = truncate(text, 2500)
    folder = f"stars/{meta.get('repo_id') or full_name.replace('/', '_')}"
    seed_figures = _star_figures(full_name, branch, readme, folder)
    research = agent.run(
        (
            f"深入分析 GitHub 仓库 {full_name} {item.get('source_url') or ''}\n"
            f"选择原因：{pick_reason}\n"
            "先 github_tree 与 README，再文档中的架构图（save_image）、关键代码、许可证。"
            "用 search(github/web/s2) 找论文与真正同类实现。未运行代码。"
        ),
        usage=usage,
        folder=folder,
        max_rounds=10,
        extra_system=f"主角仓库 {full_name}，默认分支 {branch}。",
    )
    figures = list(seed_figures)
    figures.extend(research.get("figures") or [])
    material = {
        "repo": full_name,
        "url": item.get("source_url"),
        "description": item.get("summary"),
        "metadata": meta,
        "pick_reason": pick_reason,
        "analyzed_on": today_str(),
        "root_entries": tree,
        "files": extra_files,
        "readme": truncate(readme, 12000),
        "research_notes": research.get("notes") or "",
        "tool_log": research.get("log") or [],
        "official_figures": figures,
        "note": "未运行代码，不得声称实测。原图仅使用 official_figures 里 saved=true 的 local_path。research_notes 是检索助手笔记，写稿时以其引用的已 fetch 内容为准。",
    }
    prompt = prompts_loader.load("github_star")
    user = "<<<SOURCE>>>\n" + dumps(material) + "\n<<<END_SOURCE>>>"
    body = llm.chat(
        [{"role": "system", "content": prompt}, {"role": "user", "content": user}],
        thinking=True,
        usage=usage,
        temperature=0.4,
        timeout=300,
    )
    return to_simplified(body), figures


def write_weekly_stars(reports: list[dict[str, Any]], usage: Usage) -> str:
    prompt = prompts_loader.load("weekly")
    payload = [
        {"title": r["title"], "url": r.get("source_url"), "summary": r.get("summary"), "body": truncate(r.get("body_md") or "", 1800)}
        for r in reports
    ]
    user = "<<<SOURCE>>>\n" + dumps({"as_of": today_str(), "reports": payload}) + "\n<<<END_SOURCE>>>"
    return llm.chat(
        [{"role": "system", "content": prompt}, {"role": "user", "content": user}],
        thinking=True,
        usage=usage,
        temperature=0.3,
        timeout=180,
    )


def run_github_star(task: dict[str, Any], run_id: int) -> None:
    usage = Usage()
    config = loads(task.get("config_json") or "{}", {})
    username = db.merged_secret("github_username", settings.github_username, "github_username")
    try:
        if not username:
            _log(run_id, "未配置 GitHub 用户名")
            _finish(run_id, usage, "failed", "no_github_username")
            return
        stars, complete, sync_err = sync_github_ledger(username, run_id)
        if sync_err and not stars:
            notify.send_card(
                "GitHub Star 读不到",
                f"读不到公开收藏：{sync_err}。请在设置里改 GitHub 用户名。",
            )
            _finish(run_id, usage, "failed", "github_user_unavailable")
            return
        rec, reason = pick_github_star(config)
        if not rec:
            _log(run_id, reason + "；今日没有新报告可写，不凑一篇")
            notify.send_skip("今日 Star", reason)
            _finish(run_id, usage)
            return
        _log(run_id, f"选择 {rec['title']}：{reason}")
        try:
            body, figures = write_github_star(rec, usage, reason)
        except Exception as exc:  # noqa: BLE001
            db.update_item(rec["id"], status="retry")
            _log(run_id, f"写作失败，标记待重试: {type(exc).__name__}")
            _finish(run_id, usage, "failed", type(exc).__name__)
            raise
        saved_n = sum(1 for f in figures if f.get("saved"))
        meta = loads(rec.get("metadata_json") or "{}", {})
        full_name = meta.get("full_name") or rec.get("title") or ""
        meta["pick_reason"] = reason
        meta["analyzed_on"] = today_str()
        meta["sync_complete"] = complete
        meta["figures"] = figures
        desc = (rec.get("summary") or "").strip()
        if looks_like_pick_reason(desc) or not desc:
            desc = (meta.get("description") or "").strip()
        if looks_like_pick_reason(desc) or not desc:
            info = collect.fetch_github_repo(full_name)
            desc = (info.get("description") or "").strip()
            if desc:
                meta["description"] = desc
        if looks_like_pick_reason(desc) or not desc:
            desc = one_line_blurb(body) or desc
        updated = db.update_item(
            rec["id"],
            status="published",
            body_md=to_simplified(body),
            unread=1,
            summary=to_simplified(desc),
            published_at=now_iso(),
            tags_json=dumps(["github", "star"]),
            metadata_json=dumps(meta),
        ) or rec
        _log(run_id, f"已保存报告 #{updated['id']}，原图 {saved_n}/{len(figures)}")
        weekly_id = None
        local_now = datetime.now(ZoneInfo(settings.tz))
        if local_now.weekday() == 6:
            week_from = (local_now - timedelta(days=7)).date().isoformat()
            done = [
                r
                for r in db.list_items(kind="github_star", status="published", limit=30)
                if (r.get("created_at") or "") >= week_from and (r.get("body_md") or "").strip()
            ]
            if len(done) >= 2:
                weekly_body = write_weekly_stars(done, usage)
                weekly_ext = f"star-weekly-{today_str()}"
                existing_w = db.find_item("yandu", weekly_ext)
                payload_w = {
                    "kind": "digest",
                    "status": "published",
                    "title": f"{today_str()} Star 周回顾",
                    "summary": f"基于本周 {len(done)} 份已完成报告",
                    "body_md": weekly_body,
                    "source": "yandu",
                    "external_id": weekly_ext,
                    "tags_json": dumps(["weekly", "github"]),
                }
                if existing_w:
                    weekly = db.update_item(existing_w["id"], title=payload_w["title"], summary=payload_w["summary"], body_md=weekly_body, unread=1) or existing_w
                else:
                    weekly = db.insert_item(payload_w)
                weekly_id = weekly["id"]
                _log(run_id, f"周回顾 #{weekly_id}")
            else:
                _log(run_id, "本周完成不足两份，不凑比较")
        result = notify.send_card(
            updated["title"],
            updated.get("summary") or "",
            updated["id"],
        )
        _log(run_id, f"推送: {result}")
        _finish(run_id, usage)
    except Exception as exc:  # noqa: BLE001
        _log(run_id, f"失败: {type(exc).__name__}: {exc}")
        _finish(run_id, usage, "failed", type(exc).__name__)
        raise


def write_agentic(item: dict[str, Any], usage: Usage, pick_reason: str) -> str:
    extra = ""
    if item.get("source") == "arxiv":
        extra = collect.fetch_arxiv_html(item["external_id"])
    elif item.get("source_url"):
        try:
            extra = collect.fetch_url_text(item["source_url"])
        except Exception as exc:  # noqa: BLE001
            extra = f"（抓取失败: {type(exc).__name__}）"
    folder = f"agentic/{(item.get('external_id') or 'x')}".replace("/", "_")[:80]
    pdf_url = ""
    if item.get("source") == "arxiv":
        pdf_url = f"https://arxiv.org/pdf/{item['external_id']}"
    research = agent.run(
        (
            f"精读这项 Agentic 工作。\n标题：{item.get('title')}\nURL：{item.get('source_url')}\n"
            f"摘要：{truncate(item.get('summary') or extra, 1500)}\n"
            f"{'arXiv PDF: ' + pdf_url if pdf_url else ''}\n"
            "用 github/huggingface/s2/web 找官方代码与独立验证，不要只停在 arXiv。"
            "用 read_pdf 抽方法页和原图。核验开放程度与许可证。"
        ),
        usage=usage,
        folder=folder,
        max_rounds=10,
        extra_system=f"选择原因：{pick_reason}",
    )
    history = [
        {"title": r["title"], "url": r.get("source_url")}
        for r in db.list_items(kind="agentic", status="published", limit=20)
    ]
    material = {
        "title": item["title"],
        "url": item.get("source_url"),
        "abstract": item.get("summary"),
        "excerpt": truncate(extra, 6000),
        "pick_reason": pick_reason,
        "already_covered": history,
        "research_notes": research.get("notes") or "",
        "tool_log": research.get("log") or [],
        "official_figures": research.get("figures") or [],
        "analyzed_on": today_str(),
        "note": "research_notes 来自工具检索。未运行代码。原图只用 saved=true 的 local_path。",
    }
    prompt = prompts_loader.load("agentic")
    user = "<<<SOURCE>>>\n" + dumps(material) + "\n<<<END_SOURCE>>>"
    return to_simplified(
        llm.chat(
            [{"role": "system", "content": prompt}, {"role": "user", "content": user}],
            thinking=True,
            usage=usage,
            temperature=0.35,
            timeout=300,
        )
        or ""
    )


def _classic_candidates() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in AGENTIC_CLASSICS:
        out.append(
            {
                "kind": "paper" if row.get("source") == "arxiv" else "agentic",
                "source": row["source"],
                "external_id": row["external_id"],
                "title": row["title"],
                "summary": row.get("summary") or "",
                "source_url": row.get("source_url") or "",
                "published_at": row.get("published_at") or "",
                "metadata": {"classic": True},
            }
        )
    return out


def _agentic_mature(item: dict[str, Any]) -> bool:
    meta = item.get("metadata") or {}
    if meta.get("classic"):
        return True
    try:
        stars = int(meta.get("stars") or 0)
    except (TypeError, ValueError):
        stars = 0
    try:
        cites = int(meta.get("citations") or 0)
    except (TypeError, ValueError):
        cites = 0
    if stars >= 300 or cites >= 80:
        return True
    pub = _parse_dt(str(item.get("published_at") or ""))
    if pub:
        age = (datetime.now(timezone.utc) - pub).days
        if age < 30:
            return False
        if age >= 45:
            return True
        return cites >= 20 or stars >= 100
    year = meta.get("year")
    try:
        year_i = int(year) if year else 0
    except (TypeError, ValueError):
        year_i = 0
    if year_i and year_i <= 2024:
        return True
    if year_i >= 2026:
        return False
    backend = str(meta.get("backend") or "")
    if "github" in backend or (item.get("source") or "").endswith(":github"):
        return stars >= 200
    return False


def run_agentic(task: dict[str, Any], run_id: int) -> None:
    usage = Usage()
    config = loads(task.get("config_json") or "{}", {})
    mx = int(config.get("arxiv_max") or 25)
    try:
        batch = _classic_candidates()
        _log(run_id, f"Agentic 经典候选 {len(batch)}")
        arxiv_batch = collect.collect_arxiv_query(AGENTIC_ARXIV, mx, sort="relevance")
        batch.extend(arxiv_batch)
        for backend, query in AGENTIC_SEARCHES:
            try:
                rec = search.search(backend, query)
                extra = search.hits_as_candidates(rec.get("hits") or [], "agentic")
                _log(run_id, f"Agentic {backend}:{query[:40]} → {len(extra)}")
                batch.extend(extra)
            except Exception as exc:  # noqa: BLE001
                _log(run_id, f"Agentic 检索失败 {backend}: {type(exc).__name__}")
        merged: dict[str, dict[str, Any]] = {}
        for x in batch:
            key = f"{x.get('source')}:{x.get('external_id')}"
            if x.get("external_id"):
                merged[key] = x
        unseen = [
            x
            for x in merged.values()
            if not _written(x["source"], x["external_id"]) and _agentic_mature(x)
        ]
        _log(run_id, f"Agentic 候选 {len(batch)} 去重后成熟未写 {len(unseen)}")
        if not unseen:
            _log(run_id, "没有合格新稿，不降低门槛凑数")
            notify.send_skip("今日精读", "没有合格且未讲过的工作，今日跳过。")
            _finish(run_id, usage)
            return
        covered = [r["title"] for r in db.list_items(kind="agentic", status="published", limit=40)]
        scored = triage(
            unseen,
            usage,
            run_id,
            "agentic_pick",
            extra_system="already_covered: " + dumps(covered),
        )
        kept_picks = [x for x in scored if x.get("keep") and (x.get("score") or 0) >= 8]
        kept_picks.sort(
            key=lambda x: (
                x.get("score") or 0,
                int(bool((x.get("metadata") or {}).get("classic"))),
            ),
            reverse=True,
        )
        pick = kept_picks[0] if kept_picks else None
        if not pick or (pick.get("score") or 0) < 8:
            _log(run_id, "筛选后无合格项；未写过的候选下次仍可入选")
            notify.send_skip("今日精读", "没有达到门槛的工作，今日跳过。")
            _finish(run_id, usage)
            return
        body = write_agentic(pick, usage, pick.get("reason") or "领域重要性优先")
        payload = {
            "kind": "agentic",
            "status": "published",
            "title": to_simplified(pick["title"]),
            "summary": to_simplified(pick.get("reason") or truncate(pick.get("summary") or "", 240)),
            "body_md": to_simplified(body),
            "source": pick["source"],
            "source_url": pick.get("source_url") or "",
            "external_id": pick["external_id"],
            "score": pick.get("score"),
            "tags_json": dumps(pick.get("tags") or ["agentic"]),
            "metadata_json": dumps(pick.get("metadata") or {}),
            "unread": 1,
            "published_at": now_iso(),
        }
        existing = db.find_item(pick["source"], pick["external_id"])
        if existing:
            rec = db.update_item(existing["id"], **{k: v for k, v in payload.items() if k != "source"}) or existing
        else:
            rec = db.insert_item(payload)
        result = notify.send_card(rec["title"], rec.get("summary") or "", rec["id"])
        _log(run_id, f"已写 #{rec['id']} 推送: {result}")
        _finish(run_id, usage)
    except Exception as exc:  # noqa: BLE001
        _log(run_id, f"失败: {type(exc).__name__}: {exc}")
        _finish(run_id, usage, "failed", type(exc).__name__)
        raise


def run_task(task: dict[str, Any], run_id: int) -> None:
    typ = task.get("type") or ""
    runners: dict[str, Callable[[dict[str, Any], int], None]] = {
        "frontier_digest": run_frontier,
        "daily_briefing": run_frontier,
        "github_star": run_github_star,
        "agentic_reading": run_agentic,
    }
    fn = runners.get(typ)
    if not fn:
        db.append_log(run_id, f"未知任务类型 {typ}")
        db.finish_run(run_id, "failed", error="unknown_type")
        return
    fn(task, run_id)


def run_briefing(task: dict[str, Any], run_id: int) -> None:
    run_task(task, run_id)
