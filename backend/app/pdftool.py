"""Bounded PDF text + figure extraction. Never render an entire paper at high DPI."""

from __future__ import annotations

import io
from typing import Any

import httpx

from . import media
from .util import truncate, url_is_safe

UA = "YanduStudyAssistant/0.1 (personal research digest)"
MAX_BYTES = 15 * 1024 * 1024
MAX_PAGES_TEXT = 8
MAX_FIGURES = 4


def read_pdf(url: str, *, folder: str, pages: str = "", extract_figures: bool = True) -> dict[str, Any]:
    if not url_is_safe(url):
        return {"ok": False, "error": "地址不安全"}
    try:
        with httpx.Client(timeout=httpx.Timeout(45, connect=15), headers={"User-Agent": UA}, follow_redirects=True) as client:
            r = client.get(url)
        if r.status_code >= 400:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        ctype = (r.headers.get("content-type") or "").lower()
        if "pdf" not in ctype and not url.lower().endswith(".pdf") and not r.content.startswith(b"%PDF"):
            return {"ok": False, "error": f"不是 PDF（{ctype or 'unknown'}）"}
        if len(r.content) > MAX_BYTES:
            return {"ok": False, "error": f"超过 {MAX_BYTES} bytes"}
        blob = r.content
    except httpx.HTTPError as exc:
        return {"ok": False, "error": type(exc).__name__}

    try:
        import fitz
    except ImportError:
        return {"ok": False, "error": "未安装 PyMuPDF"}

    try:
        doc = fitz.open(stream=io.BytesIO(blob), filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"open {type(exc).__name__}"}

    try:
        n = doc.page_count
        wanted = _page_list(pages, n)
        texts: list[str] = []
        for i in wanted[:MAX_PAGES_TEXT]:
            page = doc.load_page(i)
            block = page.get_text("blocks")
            if isinstance(block, list) and block:
                lines = sorted(block, key=lambda b: (round(b[1] / 8), b[0]))
                texts.append(f"[page {i + 1}]\n" + "\n".join(str(b[4]) for b in lines if len(b) > 4))
            else:
                texts.append(f"[page {i + 1}]\n" + page.get_text("text"))
        figures: list[dict[str, Any]] = []
        if extract_figures:
            figures = _figures(doc, folder, wanted)
        return {
            "ok": True,
            "url": url,
            "pages": n,
            "read_pages": [i + 1 for i in wanted[:MAX_PAGES_TEXT]],
            "text": truncate("\n\n".join(texts), 12000),
            "figures": figures,
            "note": "未渲染全文；矢量图可能抽不到，此时 figures 为空。",
        }
    finally:
        doc.close()


def _page_list(pages: str, n: int) -> list[int]:
    if not pages.strip():
        return list(range(min(n, MAX_PAGES_TEXT)))
    out: list[int] = []
    for part in pages.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError:
                continue
            for i in range(start, end + 1):
                if 1 <= i <= n:
                    out.append(i - 1)
        else:
            try:
                i = int(part)
            except ValueError:
                continue
            if 1 <= i <= n:
                out.append(i - 1)
    return out[:MAX_PAGES_TEXT] or list(range(min(n, 3)))


def _figures(doc: Any, folder: str, wanted: list[int]) -> list[dict[str, Any]]:
    import fitz

    saved: list[dict[str, Any]] = []
    seen: set[int] = set()
    for i in wanted:
        page = doc.load_page(i)
        for img in page.get_images(full=True):
            xref = int(img[0])
            if xref in seen:
                continue
            seen.add(xref)
            try:
                pix = fitz.Pixmap(doc, xref)
                if pix.n > 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                if pix.width < 80 or pix.height < 80:
                    continue
                if pix.width * pix.height > 12_000_000:
                    continue
                raw = pix.tobytes("png")
            except Exception:
                continue
            if len(raw) > 5 * 1024 * 1024:
                continue
            dest = media.media_root() / folder
            dest.mkdir(parents=True, exist_ok=True)
            name = f"pdf-p{i + 1}-{xref}.png"
            (dest / name).write_bytes(raw)
            saved.append(
                {
                    "saved": True,
                    "local_path": media.public_path(f"{folder}/{name}"),
                    "source_url": f"pdf page {i + 1} xref {xref}",
                    "note": f"{pix.width}x{pix.height}",
                }
            )
            if len(saved) >= MAX_FIGURES:
                return saved
    if saved:
        return saved
    # Vector figures: low-DPI page renders of the first two requested pages.
    for i in wanted[:2]:
        page = doc.load_page(i)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False)
        raw = pix.tobytes("jpeg")
        if len(raw) > 5 * 1024 * 1024:
            continue
        dest = media.media_root() / folder
        dest.mkdir(parents=True, exist_ok=True)
        name = f"pdf-page-{i + 1}.jpg"
        (dest / name).write_bytes(raw)
        saved.append(
            {
                "saved": True,
                "local_path": media.public_path(f"{folder}/{name}"),
                "source_url": f"pdf render page {i + 1}",
                "note": "page render fallback; not a cropped figure",
            }
        )
        if len(saved) >= 2:
            break
    return saved
