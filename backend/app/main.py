from __future__ import annotations

import json
import re
from urllib.parse import quote, urlsplit
from pathlib import Path
from typing import Any

import markdown
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.types import ASGIApp, Receive, Scope, Send

from . import auth, collect, db, llm, notify, pipeline, prompts_loader, scheduler
from .config import settings
from .cost import Usage
from .util import (
    cron_explain,
    cron_from_form,
    cron_parts,
    dumps,
    loads,
    money_label,
    spend_label,
    USD_CNY,
    now_iso,
    REPEAT_OPTIONS,
    repeat_from_dow,
    task_label,
    today_str,
    truncate,
    extract_http_url,
    looks_like_pick_reason,
    one_line_blurb,
)

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def _notify_flash(raw: str) -> str:
    if not raw:
        return ""
    if raw == "ok":
        return "测试卡片已发出。企业微信或已开启微信插件的微信里应能看到。"
    if raw == "skipped: no wecom":
        return "还没配齐 CorpID、AgentId、Secret。先保存再测。"
    if raw.startswith("skipped:"):
        return raw
    return raw


def _mark_tech_tokens(text: str) -> str:
    parts = re.split(r"(```[\s\S]*?```|`[^`\n]+`)", text or "")
    token = re.compile(
        r"(?<![A-Za-z0-9_/`])("
        r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+"
        r"|[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){1,}\.[A-Za-z0-9]+"
        r"|#{2,}[A-Za-z0-9_]+#{2,}"
        r")(?![A-Za-z0-9_/`])"
    )
    out: list[str] = []
    for i, part in enumerate(parts):
        out.append(part if i % 2 else token.sub(r"`\1`", part))
    return "".join(out)


def md_to_html(text: str) -> str:
    html = markdown.markdown(
        _mark_tech_tokens(text or ""),
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
    )
    return _break_inline_code_paths(html)


def _break_inline_code_paths(html: str) -> str:
    parts = re.split(r"(<pre[\s\S]*?</pre>)", html or "")
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(part)
            continue
        out.append(
            re.sub(
                r"<code>([^<]*)</code>",
                lambda m: "<code>" + m.group(1).replace("/", "/<wbr>") + "</code>",
                part,
            )
        )
    return "".join(out)


def _strip_leftover_stars(html: str) -> str:
    parts = re.split(r"(<pre[\s\S]*?</pre>|<code[\s\S]*?</code>)", html)
    out: list[str] = []
    for i, part in enumerate(parts):
        out.append(part if i % 2 else part.replace("*", ""))
    return "".join(out)


def chat_md_to_html(text: str) -> str:
    cleaned = re.sub(r"\*\*\s*([^*]+?)\s*\*\*", r"**\1**", text or "")
    return _strip_leftover_stars(md_to_html(cleaned))


templates.env.filters["md"] = md_to_html
templates.env.filters["chat_md"] = chat_md_to_html
templates.env.filters["loads"] = lambda s: loads(s, {})
templates.env.filters["cron_zh"] = cron_explain
templates.env.filters["task_label"] = task_label
templates.env.filters["money"] = money_label
templates.env.filters["spend"] = spend_label
templates.env.globals["usd_cny"] = USD_CNY
templates.env.filters["cron_hour"] = lambda c: cron_parts(c)[0]
templates.env.filters["cron_minute"] = lambda c: cron_parts(c)[1]
templates.env.filters["cron_repeat"] = lambda c: repeat_from_dow(cron_parts(c)[2])
templates.env.globals["repeat_options"] = REPEAT_OPTIONS
templates.env.globals["hours"] = list(range(24))
templates.env.globals["minutes"] = [0, 5, 10, 15, 20, 30, 45]

app = FastAPI(title="研读", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
_MEDIA = settings.data_dir / "media"
_MEDIA.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(_MEDIA)), name="media")


def _cookie(scope: Scope, name: str) -> str | None:
    for key, value in scope.get("headers") or []:
        if key == b"cookie":
            for part in value.decode("latin-1").split(";"):
                k, _, v = part.strip().partition("=")
                if k == name:
                    return v
    return None


class GateMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if auth.open_path(path):
            await self.app(scope, receive, send)
            return
        token = _cookie(scope, auth.COOKIE)
        if auth.session_ok(token):
            await self.app(scope, receive, send)
            return
        if path.startswith("/api/"):
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
        elif auth.needs_setup():
            response = RedirectResponse("/setup", status_code=302)
        else:
            response = RedirectResponse("/login", status_code=302)
        await response(scope, receive, send)


app.add_middleware(GateMiddleware)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    auth.bootstrap_password()
    if auth.needs_setup() and (settings.host or "") in {"0.0.0.0", "::"}:
        print("研读尚未设置口令。浏览器打开后走首次设置；公网暴露时请尽快设口令。")
    scheduler.start()


def render(request: Request, name: str, **ctx: Any) -> HTMLResponse:
    ctx.setdefault("today", today_str())
    ctx.setdefault("running_jobs", db.list_running())
    return templates.TemplateResponse(request, name, ctx)


@app.get("/health")
def health() -> dict[str, str]:
    return {"ok": "yandu"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    if auth.needs_setup():
        return RedirectResponse("/setup", status_code=302)
    err = request.query_params.get("e") or ""
    messages = {
        "1": "口令不对。",
        "locked": "尝试过多，过几分钟再试。",
    }
    return render(request, "login.html", error=messages.get(err, ""))


@app.post("/login")
def login_submit(request: Request, password: str = Form(...)) -> RedirectResponse:
    if auth.needs_setup():
        return RedirectResponse("/setup", status_code=303)
    ip = auth.client_ip(request)
    if auth.login_blocked(ip):
        return RedirectResponse("/login?e=locked", status_code=303)
    if not auth.password_ok(password):
        auth.login_fail(ip)
        return RedirectResponse("/login?e=1", status_code=303)
    auth.login_ok(ip)
    resp = RedirectResponse("/", status_code=303)
    auth.attach_session(resp, request)
    return resp


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request) -> HTMLResponse:
    if not auth.needs_setup():
        return RedirectResponse("/login", status_code=302)
    err = request.query_params.get("e") or ""
    messages = {
        "weak": f"口令至少 {auth.MIN_PASSWORD_LEN} 位，不要用 change-me 这类常见词。",
        "mismatch": "两次口令不一致。",
        "locked": "尝试过多，过几分钟再试。",
    }
    return render(request, "setup.html", error=messages.get(err, ""))


@app.post("/setup")
def setup_submit(
    request: Request,
    password: str = Form(""),
    password2: str = Form(""),
    llm_api_key: str = Form(""),
    github_username: str = Form(""),
) -> RedirectResponse:
    if not auth.needs_setup():
        return RedirectResponse("/", status_code=303)
    ip = auth.client_ip(request)
    if auth.login_blocked(ip):
        return RedirectResponse("/setup?e=locked", status_code=303)
    if password != password2:
        auth.login_fail(ip)
        return RedirectResponse("/setup?e=mismatch", status_code=303)
    err = auth.complete_setup(
        password,
        llm_api_key=llm_api_key,
        github_username=github_username,
    )
    if err:
        auth.login_fail(ip)
        return RedirectResponse("/setup?e=weak", status_code=303)
    auth.login_ok(ip)
    resp = RedirectResponse("/", status_code=303)
    auth.attach_session(resp, request)
    return resp


@app.post("/logout")
def logout() -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


def _today_items() -> list[dict[str, Any]]:
    kinds = {"paper", "news", "github_star", "digest", "agentic"}
    return [
        i
        for i in db.list_items(limit=120, day=today_str(), status="published")
        if i["kind"] in kinds
    ]


def _is_weekly_digest(item: dict[str, Any]) -> bool:
    return str(item.get("external_id") or "").startswith("star-weekly")


def _event_published_at(item: dict[str, Any]) -> str:
    pub = (item.get("published_at") or "").strip()
    created = (item.get("created_at") or "").strip()
    if pub and created and pub[:19] == created[:19]:
        pub = ""
    if pub:
        return pub
    return collect.infer_published_at(
        item.get("title") or "",
        item.get("body_md") or "",
        item.get("summary") or "",
        item.get("source_url") or "",
    )


def _fresh_news_item(item: dict[str, Any]) -> bool:
    body = item.get("body_md") or ""
    if "发布日期未标注" in body or "抓取失败" in body or "尚未完成核验" in body:
        return False
    pub = _event_published_at(item)
    if not pub:
        return False
    return collect.within_hours(pub, 48, empty_ok=False)


def _dedupe_news(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        title = it.get("title") or ""
        if any(pipeline.same_event(title, x.get("title") or "") for x in out):
            continue
        out.append(it)
    return out


def _digest_card_summary(item: dict[str, Any], news_n: int) -> str:
    n = len([ln for ln in (item.get("body_md") or "").splitlines() if ln.startswith("- [")])
    if n:
        return f"今日 {n} 条已确认快讯"
    if news_n:
        return f"今日 {news_n} 条已确认快讯"
    s = (item.get("summary") or "").strip()
    if s and not any(token in s for token in ("发布", "融资", "推出", "估值")):
        return s
    return "今日快讯目录"


def _star_card_summary(item: dict[str, Any]) -> str:
    s = (item.get("summary") or "").strip()
    if s and not looks_like_pick_reason(s):
        return s
    meta = loads(item.get("metadata_json") or "{}", {})
    desc = (meta.get("description") or "").strip()
    if desc:
        return desc
    return one_line_blurb(item.get("body_md") or "") or s


def _present_story(item: dict[str, Any], *, news_n: int = 0) -> dict[str, Any]:
    it = dict(item)
    kind = it.get("kind") or ""
    if kind == "digest" and not _is_weekly_digest(it):
        it["summary"] = _digest_card_summary(it, news_n)
    elif kind == "github_star":
        it["summary"] = _star_card_summary(it)
    return it


def _slot_content(typ: str, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    typ = typ or ""
    if typ in {"frontier_digest", "daily_briefing"}:
        digest = [i for i in items if i["kind"] == "digest" and not _is_weekly_digest(i)]
        news = [i for i in items if i["kind"] in {"paper", "news"}]
        headlines = _dedupe_news([n for n in news if _fresh_news_item(n)])
        return [_present_story(i, news_n=len(headlines)) for i in digest], headlines
    if typ == "github_star":
        weekly = [i for i in items if i["kind"] == "digest" and _is_weekly_digest(i)]
        stars = [_present_story(i) for i in items if i["kind"] == "github_star"]
        return weekly + stars, []
    if typ == "agentic_reading":
        return [i for i in items if i["kind"] == "agentic"], []
    return [i for i in items if i["kind"] == typ], []


def _item_back(request: Request, item: dict[str, Any]) -> tuple[str, str]:
    origin = (request.query_params.get("from") or "").strip()
    if origin not in {"today", "library", "inbox"}:
        path = urlsplit(request.headers.get("referer") or "").path
        if path == "/":
            origin = "today"
        elif path.startswith("/library"):
            origin = "library"
        elif path.startswith("/inbox"):
            origin = "inbox"
        elif (item.get("kind") or "") == "inbox":
            origin = "inbox"
        elif any(row["id"] == item["id"] for row in _today_items()):
            origin = "today"
        else:
            origin = "library"
    labels = {"today": ("今日", "/"), "library": ("文库", "/library"), "inbox": ("灵感", "/inbox")}
    return labels[origin]


def _item_day(item: dict[str, Any]) -> str:
    return (item.get("created_at") or "")[:10]


def _day_label(day: str) -> str:
    if day == today_str():
        return "今日"
    parts = day.split("-")
    if len(parts) != 3:
        return day or "未标注"
    year, month, date = parts
    try:
        return f"{int(year)}年{int(month)}月{int(date)}日"
    except ValueError:
        return day


def _library_kind(item: dict[str, Any]) -> str:
    kind = item.get("kind") or ""
    if kind == "agentic":
        return "agentic"
    if kind == "github_star" or (kind == "digest" and _is_weekly_digest(item)):
        return "github"
    if kind in {"news", "paper", "digest"}:
        return "frontier"
    return "other"


def _library_sections(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels = {
        "frontier": "前沿日报",
        "github": "GitHub",
        "agentic": "精读",
        "other": "其他",
    }
    order = ["frontier", "github", "agentic", "other"]
    buckets = {key: [] for key in order}
    for item in items:
        buckets[_library_kind(item)].append(_present_story(item))
    return [
        {"key": key, "label": labels[key], "stories": buckets[key]}
        for key in order
        if buckets[key]
    ]


def _item_tag(item: dict[str, Any]) -> str:
    kind = item.get("kind") or ""
    if kind == "digest":
        return "周回顾" if _is_weekly_digest(item) else "日报"
    if kind == "github_star":
        return "GitHub"
    if kind == "agentic":
        return "精读"
    return item.get("source") or kind


@app.get("/", response_class=HTMLResponse)
def today(request: Request) -> HTMLResponse:
    items = _today_items()
    slots = []
    for task in db.list_tasks():
        stories, headlines = _slot_content(task.get("type") or "", items)
        slots.append({"task": task, "stories": stories, "headlines": headlines})
    inbox_n = len(db.list_items(kind="inbox", limit=200))
    return render(
        request,
        "today.html",
        slots=slots,
        inbox_n=inbox_n,
        today_cost=db.today_cost_usd(),
        item_tag=_item_tag,
    )


@app.get("/library", response_class=HTMLResponse)
def library(request: Request, q: str = "", kind: str = "") -> HTMLResponse:
    items = db.list_items(q=q or None, kind=kind or None, status="published", limit=80)
    return render(
        request,
        "library.html",
        sections=_library_sections(items),
        q=q,
        kind=kind,
        item_tag=_item_tag,
        day_label=lambda item: _day_label(_item_day(item)),
    )


@app.get("/item/{item_id}", response_class=HTMLResponse)
def item_page(request: Request, item_id: int) -> HTMLResponse:
    item = db.get_item(item_id)
    if not item:
        return render(request, "missing.html")
    if item["unread"]:
        db.update_item(item_id, unread=0)
        item["unread"] = 0
    messages = db.list_messages(item_id)
    back_label, back_href = _item_back(request, item)
    return render(
        request,
        "item.html",
        item=item,
        messages=messages,
        meta=loads(item.get("metadata_json"), {}),
        back_label=back_label,
        back_href=back_href,
    )


@app.post("/item/{item_id}/delete")
def item_delete(item_id: int) -> RedirectResponse:
    db.delete_item(item_id)
    return RedirectResponse("/library", status_code=303)


@app.post("/item/{item_id}/later")
def item_later(item_id: int) -> RedirectResponse:
    db.update_item(item_id, status="later", unread=1)
    return RedirectResponse(f"/item/{item_id}", status_code=303)


@app.get("/inbox", response_class=HTMLResponse)
def inbox_page(request: Request) -> HTMLResponse:
    items = db.list_items(kind="inbox", limit=100)
    items += [i for i in db.list_items(status="later", limit=50) if i["kind"] != "inbox"]
    return render(request, "inbox.html", items=items)


@app.post("/inbox")
def inbox_add(
    title: str = Form(""),
    note: str = Form(""),
) -> RedirectResponse:
    note = note.strip()
    title = title.strip()
    url = extract_http_url(f"{title}\n{note}")
    body = note
    summary = note
    source = "inbox"
    fetch_ok = True
    if url:
        rec = collect.ingest_public_url(url)
        source = rec.get("source") or "web"
        fetched = (rec.get("body") or "").strip()
        leftover = note
        if url in leftover:
            leftover = leftover.replace(url, "").strip()
        if leftover == title:
            leftover = ""
        parts: list[str] = []
        if leftover:
            parts.append(leftover)
        if rec.get("ok") and fetched:
            parts.append(fetched)
        elif rec.get("error"):
            fetch_ok = False
            parts.append(f"未能抽出正文（{rec.get('error')}）。原文链接仍可在追问时再抓。")
        body = "\n\n".join(parts) or note or url
        summary = leftover or rec.get("error") or truncate(fetched, 240) or url
        looks_like_url = bool(extract_http_url(title) == title or not title)
        if looks_like_url and rec.get("title"):
            title = rec["title"]
    if not title and note:
        title = note.splitlines()[0][:80]
    title = title or (url[:80] if url else "未命名灵感")
    rec = db.insert_item(
        {
            "kind": "inbox",
            "status": "inbox",
            "title": title[:180],
            "summary": truncate(summary, 240),
            "body_md": body,
            "source": source,
            "source_url": url,
            "external_id": f"inbox-{now_iso()}-{title[:20]}-{id(title)}",
            "metadata_json": dumps({"fetch_ok": fetch_ok, "ingested": bool(url)}),
        }
    )
    if url:
        return RedirectResponse(f"/item/{rec['id']}?from=inbox", status_code=303)
    return RedirectResponse("/inbox", status_code=303)


@app.post("/inbox/{item_id}/delete")
def inbox_delete(item_id: int) -> RedirectResponse:
    db.delete_item(item_id)
    return RedirectResponse("/inbox", status_code=303)


@app.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, confirm_delete: int = 0) -> HTMLResponse:
    from .official_sources import NEWS_FEEDS, OFFICIAL_SOURCES

    return render(
        request,
        "tasks.html",
        tasks=db.list_tasks(),
        trash=db.list_tasks(trashed=True),
        sources=db.list_sources(),
        confirm_delete=confirm_delete,
        official_names=[name for name, _kind, _url in OFFICIAL_SOURCES],
        news_names=[name for name, _kind, _url in NEWS_FEEDS],
        search_backends=["web（Brave / Tavily / DuckDuckGo）", "GitHub Search", "Hugging Face", "arXiv", "Semantic Scholar", "本地文库"],
        task_prompts=prompts_loader.TASK_PROMPTS,
        prompt_meta=prompts_loader.PROMPT_META,
        prompt_bodies=prompts_loader.bodies_for(
            "triage",
            "news",
            "github_star",
            "weekly",
            "research",
            "agentic_pick",
            "agentic",
        ),
    )


@app.post("/tasks")
def task_create(
    name: str = Form(...),
    type: str = Form("frontier_digest"),
    hour: str = Form("7"),
    minute: str = Form("0"),
    repeat: str = Form("daily"),
    cron: str = Form(""),
    explain_count: int = Form(8),
    min_score: float = Form(7),
    prefer_repo: str = Form(""),
) -> RedirectResponse:
    cfg: dict[str, Any] = {}
    if type in {"frontier_digest", "daily_briefing"}:
        cfg = {
            "explain_count": explain_count,
            "min_score": min_score,
            "include_hf_daily": False,
            "include_arxiv": False,
            "window_hours": 36,
        }
    elif type == "github_star":
        cfg = {"prefer_repo": prefer_repo.strip()}
    elif type == "agentic_reading":
        cfg = {"arxiv_max": 25}
    db.create_task(
        {
            "name": name,
            "type": type,
            "cron": cron_from_form(hour, minute, repeat, cron or "0 7 * * *"),
            "enabled": True,
            "config_json": dumps(cfg),
        }
    )
    scheduler.rebuild()
    return RedirectResponse("/tasks", status_code=303)


@app.post("/tasks/{task_id}/save")
def task_save(
    task_id: int,
    name: str = Form(...),
    hour: str = Form("7"),
    minute: str = Form("0"),
    repeat: str = Form("daily"),
    cron: str = Form(""),
    explain_count: int = Form(8),
    min_score: float = Form(7),
    prefer_repo: str = Form(""),
    prompt_triage: str | None = Form(None),
    prompt_news: str | None = Form(None),
    prompt_github_star: str | None = Form(None),
    prompt_weekly: str | None = Form(None),
    prompt_research: str | None = Form(None),
    prompt_agentic_pick: str | None = Form(None),
    prompt_agentic: str | None = Form(None),
) -> RedirectResponse:
    task = db.get_task(task_id)
    cfg = loads(task["config_json"] if task else "{}", {})
    typ = (task or {}).get("type") or ""
    if typ in {"frontier_digest", "daily_briefing"}:
        cfg.update({"explain_count": explain_count, "min_score": min_score})
    elif typ == "github_star":
        cfg.update({"prefer_repo": prefer_repo.strip()})
    db.update_task(
        task_id,
        {
            "name": name,
            "cron": cron_from_form(hour, minute, repeat, cron or (task or {}).get("cron") or "0 7 * * *"),
            "config_json": dumps(cfg),
        },
    )
    incoming = {
        "triage": prompt_triage,
        "news": prompt_news,
        "github_star": prompt_github_star,
        "weekly": prompt_weekly,
        "research": prompt_research,
        "agentic_pick": prompt_agentic_pick,
        "agentic": prompt_agentic,
    }
    for pname in prompts_loader.for_task(typ):
        if incoming.get(pname) is not None:
            db.set_setting(f"prompt_{pname}", incoming[pname])
    scheduler.rebuild()
    return RedirectResponse("/tasks", status_code=303)


@app.post("/tasks/pause-all")
def task_pause_all() -> RedirectResponse:
    for task in db.list_tasks():
        if task.get("enabled"):
            db.update_task(task["id"], {"enabled": False})
    scheduler.rebuild()
    return RedirectResponse("/tasks", status_code=303)


@app.post("/tasks/resume-all")
def task_resume_all() -> RedirectResponse:
    for task in db.list_tasks():
        if not task.get("enabled"):
            db.update_task(task["id"], {"enabled": True})
    scheduler.rebuild()
    return RedirectResponse("/tasks", status_code=303)


@app.post("/tasks/{task_id}/pause")
def task_pause(task_id: int) -> RedirectResponse:
    db.update_task(task_id, {"enabled": False})
    scheduler.rebuild()
    return RedirectResponse("/tasks", status_code=303)


@app.post("/tasks/{task_id}/resume")
def task_resume(task_id: int) -> RedirectResponse:
    db.update_task(task_id, {"enabled": True})
    scheduler.rebuild()
    return RedirectResponse("/tasks", status_code=303)


@app.post("/tasks/{task_id}/delete")
def task_delete(task_id: int, confirm: str = Form("")) -> RedirectResponse:
    if confirm != "yes":
        return RedirectResponse(f"/tasks?confirm_delete={task_id}", status_code=303)
    db.delete_task(task_id)
    scheduler.rebuild()
    return RedirectResponse("/tasks", status_code=303)


@app.post("/tasks/{task_id}/restore")
def task_restore(task_id: int) -> RedirectResponse:
    db.restore_task(task_id)
    scheduler.rebuild()
    return RedirectResponse("/tasks", status_code=303)


@app.post("/tasks/{task_id}/run")
def task_run(task_id: int) -> RedirectResponse:
    import threading

    task = db.get_task(task_id)
    if not task:
        return RedirectResponse("/tasks", status_code=303)
    run = db.create_run(task_id)

    def _go() -> None:
        scheduler._execute(task, run["id"])

    threading.Thread(target=_go, daemon=True).start()
    return RedirectResponse(f"/runs/{run['id']}", status_code=303)


@app.post("/sources/{source_id}/save")
def source_save(source_id: int, enabled: str = Form(""), name: str = Form(""), url: str = Form("")) -> RedirectResponse:
    src = next((s for s in db.list_sources() if s["id"] == source_id), None)
    cfg = loads(src["config_json"] if src else "{}", {})
    if url:
        cfg["url"] = url
    db.update_source(source_id, {"enabled": bool(enabled), "name": name or (src["name"] if src else ""), "config_json": dumps(cfg)})
    return RedirectResponse("/tasks", status_code=303)


@app.post("/sources")
def source_add(name: str = Form(...), url: str = Form(...)) -> RedirectResponse:
    from .db import connect

    with connect() as conn:
        conn.execute(
            "INSERT INTO sources(name, type, enabled, config_json) VALUES (?,?,1,?)",
            (name, "rss", dumps({"url": url})),
        )
    return RedirectResponse("/tasks", status_code=303)


@app.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, page: int = 1) -> HTMLResponse:
    per_page = 10
    total = db.count_runs()
    pages = max((total + per_page - 1) // per_page, 1) if total else 1
    page = min(max(page, 1), pages)
    offset = (page - 1) * per_page
    resp = render(
        request,
        "runs.html",
        runs=db.list_runs(limit=per_page, offset=offset),
        page=page,
        pages=pages,
        total=total,
    )
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: int) -> HTMLResponse:
    run = db.get_run(run_id)
    if not run:
        return render(request, "missing.html")
    resp = render(request, "run.html", run=run)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.get("/api/runs/{run_id}")
def api_run(run_id: int) -> JSONResponse:
    run = db.get_run(run_id)
    if not run:
        return JSONResponse({"error": "missing"}, status_code=404)
    return JSONResponse(run, headers={"Cache-Control": "no-store"})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    stored = db.all_settings()
    pw = request.query_params.get("pw") or ""
    notify_raw = request.query_params.get("notify") or ""
    pw_messages = {
        "ok": "口令已更新。",
        "bad": "当前口令不对。",
        "mismatch": "两次新口令不一致。",
        "weak": f"新口令至少 {auth.MIN_PASSWORD_LEN} 位，不要用常见弱口令。",
        "locked": "尝试过多，过几分钟再试。",
    }
    return render(
        request,
        "settings.html",
        stored=stored,
        env_model=settings.llm_model,
        env_base=settings.llm_base_url,
        has_llm_key=bool(db.merged_secret("llm_api_key", settings.llm_api_key, "llm_api_key")),
        has_github_token=bool(db.merged_secret("github_token", settings.github_token, "github_token")),
        has_brave_key=bool(db.merged_secret("brave_api_key", settings.brave_api_key, "brave_api_key")),
        has_tavily_key=bool(db.merged_secret("tavily_api_key", settings.tavily_api_key, "tavily_api_key")),
        has_wecom=bool(
            db.merged_secret("wecom_corpid", settings.wecom_corpid, "wecom_corpid")
            and db.merged_secret("wecom_secret", settings.wecom_secret, "wecom_secret")
            and db.merged_secret("wecom_agentid", settings.wecom_agentid, "wecom_agentid")
        ),
        github_username=db.merged_secret("github_username", settings.github_username, "github_username"),
        wecom_corpid=db.merged_secret("wecom_corpid", settings.wecom_corpid, "wecom_corpid"),
        wecom_agentid=db.merged_secret("wecom_agentid", settings.wecom_agentid, "wecom_agentid"),
        wecom_touser=db.merged_secret("wecom_touser", settings.wecom_touser, "wecom_touser") or "@all",
        public_base_url=db.merged_secret("public_base_url", settings.public_base_url, "public_base_url"),
        chat_thinking=db.flag("chat_thinking", True),
        chat_prompt=prompts_loader.load("chat"),
        pw_notice=pw_messages.get(pw, ""),
        pw_ok=pw == "ok",
        notify_notice=_notify_flash(notify_raw),
        notify_ok=notify_raw == "ok",
        wecom_notify_skips=db.flag("wecom_notify_skips", False),
    )


@app.post("/settings")
def settings_save(
    llm_base_url: str = Form(""),
    llm_model: str = Form(""),
    llm_api_key: str = Form(""),
    github_username: str = Form(""),
    github_token: str = Form(""),
    brave_api_key: str = Form(""),
    tavily_api_key: str = Form(""),
) -> RedirectResponse:
    mapping = {
        "llm_base_url": llm_base_url,
        "llm_model": llm_model,
        "github_username": github_username,
    }
    for k, v in mapping.items():
        if v.strip():
            db.set_setting(k, v.strip())
    if llm_api_key.strip():
        db.set_setting("llm_api_key", llm_api_key.strip())
    if github_token.strip():
        db.set_setting("github_token", github_token.strip())
    if brave_api_key.strip():
        db.set_setting("brave_api_key", brave_api_key.strip())
    if tavily_api_key.strip():
        db.set_setting("tavily_api_key", tavily_api_key.strip())
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/wecom")
def settings_wecom_save(
    wecom_corpid: str = Form(""),
    wecom_secret: str = Form(""),
    wecom_agentid: str = Form(""),
    wecom_touser: str = Form(""),
    public_base_url: str = Form(""),
    wecom_notify_skips: str = Form(""),
) -> RedirectResponse:
    mapping = {
        "wecom_corpid": wecom_corpid,
        "wecom_agentid": wecom_agentid,
        "wecom_touser": wecom_touser,
        "public_base_url": public_base_url,
    }
    for k, v in mapping.items():
        if v.strip():
            db.set_setting(k, v.strip())
    if wecom_secret.strip():
        db.set_setting("wecom_secret", wecom_secret.strip())
    db.set_setting("wecom_notify_skips", "1" if wecom_notify_skips.strip() else "0")
    notify.clear_token()
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/wecom-test")
def settings_wecom_test() -> RedirectResponse:
    result = notify.send_test()
    return RedirectResponse("/settings?notify=" + quote(result, safe=""), status_code=303)


@app.post("/settings/password")
def settings_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    new_password2: str = Form(""),
) -> RedirectResponse:
    ip = auth.client_ip(request)
    if auth.login_blocked(ip):
        return RedirectResponse("/settings?pw=locked", status_code=303)
    if not auth.password_ok(current_password):
        auth.login_fail(ip)
        return RedirectResponse("/settings?pw=bad", status_code=303)
    if new_password != new_password2:
        return RedirectResponse("/settings?pw=mismatch", status_code=303)
    err = auth.save_password(new_password)
    if err:
        return RedirectResponse("/settings?pw=weak", status_code=303)
    auth.login_ok(ip)
    resp = RedirectResponse("/settings?pw=ok", status_code=303)
    auth.attach_session(resp, request)
    return resp


@app.post("/settings/prompts")
def prompts_save(chat: str = Form(""), chat_thinking: str = Form("")) -> RedirectResponse:
    db.set_setting("prompt_chat", chat)
    db.set_setting("chat_thinking", "1" if chat_thinking.strip() else "0")
    return RedirectResponse("/settings", status_code=303)


class ChatIn(BaseModel):
    message: str = ""


def _reveal_pieces(text: str, width: int = 36) -> list[str]:
    pieces: list[str] = []
    lines = (text or "").split("\n")
    for index, line in enumerate(lines):
        newline = "\n" if index < len(lines) - 1 else ""
        if not line:
            if newline:
                pieces.append(newline)
            continue
        start = 0
        while start < len(line):
            stop = min(len(line), start + width)
            if stop < len(line):
                window = line[start:stop]
                cut = max(window.rfind(mark) for mark in "。！？；，、")
                if cut >= width // 2:
                    stop = start + cut + 1
            pieces.append(line[start:stop] + (newline if stop >= len(line) else ""))
            start = stop
    return [piece for piece in pieces if piece]


@app.post("/api/chat/{item_id}")
def chat(item_id: int, payload: ChatIn) -> StreamingResponse:
    item = db.get_item(item_id)
    if not item:
        return JSONResponse({"error": "missing"}, status_code=404)
    text = (payload.message or "").strip()
    if not text:
        return JSONResponse({"error": "empty"}, status_code=400)
    db.add_message(item_id, "user", text)
    prompt = prompts_loader.load("chat").replace("{{item}}", truncate(f"# {item['title']}\n\n{item.get('body_md') or item.get('summary') or ''}", 14000))
    history = []
    for m in db.list_messages(item_id)[-16:]:
        role = "assistant" if m["role"] == "assistant" else "user"
        history.append({"role": role, "content": m["content"]})

    def gen():
        import time

        usage = Usage()
        err = None
        full = ""
        try:
            from . import agent as agentmod

            full = agentmod.chat_reply(
                prompt,
                history,
                usage=usage,
                folder=f"chat/{item_id}",
                thinking=db.flag("chat_thinking", True),
            )
        except Exception as exc:  # noqa: BLE001
            err = type(exc).__name__
        title = (item.get("title") or "")[:40]
        line = f"#{item_id} {title} hit/miss/out={usage.hit}/{usage.miss}/{usage.out} cost≈{money_label(usage.cost_usd())}"
        if err:
            line += f" {err}"
        db.add_ad_hoc_spend(
            task_type="chat",
            task_name="对话",
            token_in_hit=usage.hit,
            token_in_miss=usage.miss,
            token_out=usage.out,
            cost_usd=usage.cost_usd(),
            log_line=line,
        )
        if err:
            yield f"data: {json.dumps({'error': err})}\n\n"
            return
        db.add_message(item_id, "assistant", full)
        shown = ""
        pieces = _reveal_pieces(full)
        for index, piece in enumerate(pieces):
            shown += piece
            yield f"data: {json.dumps({'delta': piece, 'html': chat_md_to_html(shown)}, ensure_ascii=False)}\n\n"
            if index + 1 < len(pieces):
                time.sleep(0.24 if piece.endswith("\n") else 0.12)
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
