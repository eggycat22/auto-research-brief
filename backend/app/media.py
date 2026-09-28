"""Persist official figures next to reports. Never generate replacements."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from .config import settings
from .util import url_is_safe

UA = "YanduStudyAssistant/0.1 (personal research digest)"
IMG_MD = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMG_HTML = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)
EXT = {".png": "png", ".jpg": "jpg", ".jpeg": "jpg", ".gif": "gif", ".webp": "webp", ".svg": "svg"}


def media_root() -> Path:
    path = settings.data_dir / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def public_path(rel: str) -> str:
    return "/media/" + rel.replace("\\", "/").lstrip("/")


def _ext_of(url: str, content_type: str) -> str:
    path = urlparse(url).path.lower()
    for suffix, ext in EXT.items():
        if path.endswith(suffix):
            return ext
    if "png" in content_type:
        return "png"
    if "jpeg" in content_type or "jpg" in content_type:
        return "jpg"
    if "gif" in content_type:
        return "gif"
    if "webp" in content_type:
        return "webp"
    if "svg" in content_type:
        return "svg"
    return "bin"


def extract_image_urls(markdown_or_html: str, base_url: str = "") -> list[str]:
    found: list[str] = []
    for match in IMG_MD.findall(markdown_or_html or ""):
        found.append(match.strip())
    for match in IMG_HTML.findall(markdown_or_html or ""):
        found.append(match.strip())
    out: list[str] = []
    seen: set[str] = set()
    for raw in found:
        if raw.startswith("data:"):
            continue
        url = urljoin(base_url, raw) if base_url else raw
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out[:8]


def github_raw_url(url: str, full_name: str, branch: str) -> str:
    if url.startswith("https://github.com/") and "/blob/" in url:
        return url.replace("https://github.com/", "https://raw.githubusercontent.com/").replace("/blob/", "/")
    if url.startswith("/") or not url.startswith("http"):
        return f"https://raw.githubusercontent.com/{full_name}/{branch}/{url.lstrip('/')}"
    return url


def save_remote_image(folder: str, url: str, index: int) -> dict:
    rec = {"source_url": url, "local_path": "", "saved": False, "note": ""}
    if not url_is_safe(url):
        rec["note"] = "地址不安全，未下载"
        return rec
    dest_dir = media_root() / folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(timeout=httpx.Timeout(40, connect=15), headers={"User-Agent": UA}, follow_redirects=True) as client:
            r = client.get(url)
            if r.status_code >= 400:
                rec["note"] = f"下载失败 HTTP {r.status_code}"
                return rec
            ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            if not any(x in ctype for x in ("image/", "octet-stream", "svg")):
                rec["note"] = f"不是图片（{ctype or 'unknown'}）"
                return rec
            if len(r.content) > 6 * 1024 * 1024:
                rec["note"] = "图片超过 6MB，未保存"
                return rec
            ext = _ext_of(url, ctype)
            digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
            name = f"{index:02d}-{digest}.{ext}"
            path = dest_dir / name
            path.write_bytes(r.content)
            rec["saved"] = True
            rec["local_path"] = public_path(f"{folder}/{name}")
            rec["note"] = f"{len(r.content)} bytes"
            return rec
    except httpx.HTTPError as exc:
        rec["note"] = f"下载失败 {type(exc).__name__}"
        return rec
