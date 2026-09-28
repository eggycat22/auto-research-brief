from __future__ import annotations

import json
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .config import settings


def now_iso() -> str:
    return datetime.now(ZoneInfo(settings.tz)).isoformat(timespec="seconds")


def today_str() -> str:
    return datetime.now(ZoneInfo(settings.tz)).date().isoformat()


def is_off_peak(when: datetime | None = None) -> bool:
    dt = when or datetime.now(ZoneInfo("UTC"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(settings.tz)).astimezone(ZoneInfo("UTC"))
    else:
        dt = dt.astimezone(ZoneInfo("UTC"))
    if dt.weekday() >= 5:
        return True
    minutes = dt.hour * 60 + dt.minute
    peak = (1 * 60 <= minutes < 4 * 60) or (6 * 60 <= minutes < 10 * 60)
    return not peak


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(text: str, default: Any) -> Any:
    try:
        return json.loads(text or "")
    except json.JSONDecodeError:
        return default


def extract_json(text: str) -> Any:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            raise
        return json.loads(match.group(0))


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "nav", "footer"}:
            self._skip = True
        if tag in {"p", "br", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "footer"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str, limit: int = 12000) -> str:
    parser = _TextExtractor()
    parser.feed(html or "")
    text = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", "".join(parser.parts))).strip()
    return text[:limit]


def url_is_safe(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return False
    if host.endswith(".local") or host.startswith("10.") or host.startswith("192.168."):
        return False
    if re.match(r"^172\.(1[6-9]|2\d|3[0-1])\.", host):
        return False
    if host.startswith("169.254.") or host == "metadata.google.internal":
        return False
    return True


def truncate(text: str, n: int) -> str:
    text = text or ""
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def extract_http_url(text: str) -> str:
    found = re.search(r"https?://[^\s<>\"')\]]+", text or "")
    if not found:
        return ""
    return found.group(0).rstrip(")。,，；;.")


_STAR_PICK_REASONS = {
    "继续消化历史队列",
    "消化历史收藏，避免长期饥饿",
    "新收藏优先",
    "队列中的下一个",
    "无待分析项目",
}


def looks_like_pick_reason(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if raw in _STAR_PICK_REASONS:
        return True
    return raw.startswith("你指定优先")


def one_line_blurb(text: str, limit: int = 72) -> str:
    raw = text or ""
    raw = re.sub(r"^#+\s.*$", "", raw, flags=re.M)
    raw = re.sub(r"!\[.*?\]\(.*?\)", "", raw)
    raw = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", raw)
    scene = ("假设你", "如果你", "想象", "假如", "试着")
    found: list[str] = []
    for part in re.split(r"[。！？\n]", raw):
        line = re.sub(r"\s+", " ", part).strip(" *-#>`")
        if len(line) >= 12:
            found.append(line)
    for line in found:
        if not line.startswith(scene):
            return truncate(line, limit)
    if found:
        return truncate(found[0], limit)
    compact = re.sub(r"\s+", " ", raw).strip()
    return truncate(compact, limit) if compact else ""


TASK_LABELS = {
    "frontier_digest": "前沿快讯",
    "daily_briefing": "前沿快讯",
    "github_star": "GitHub Star 精讲",
    "agentic_reading": "Agentic 精读",
    "chat": "对话",
}

_DOW = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]


def task_label(typ: str) -> str:
    return TASK_LABELS.get(typ or "", typ or "任务")


def cron_explain(cron: str) -> str:
    parts = (cron or "").split()
    if len(parts) != 5:
        return cron or "未设置"
    minute, hour, _dom, _month, dow = parts
    try:
        hhmm = f"{int(hour):02d}:{int(minute):02d}"
    except ValueError:
        hhmm = f"{hour}:{minute}"
    if dow in {"*", "?"}:
        return f"每天 {hhmm}"
    if dow in {"1-5", "1,2,3,4,5"}:
        return f"周一到周五 {hhmm}"
    if dow in {"0,6", "6,0"}:
        return f"周六和周日 {hhmm}"
    days = []
    for bit in dow.split(","):
        bit = bit.strip()
        if bit.isdigit() and 0 <= int(bit) <= 6:
            days.append(_DOW[int(bit)])
    if days:
        return f"{'、'.join(days)} {hhmm}"
    return f"{hhmm}（{cron}）"


def fields_to_cron(hour: int | str, minute: int | str, dow: str = "*") -> str:
    try:
        h = max(0, min(23, int(hour)))
        m = max(0, min(59, int(minute)))
    except (TypeError, ValueError):
        h, m = 7, 0
    day = (dow or "*").strip() or "*"
    return f"{m} {h} * * {day}"


def cron_parts(cron: str) -> tuple[str, str, str]:
    parts = (cron or "0 7 * * *").split()
    if len(parts) != 5:
        return "7", "0", "*"
    return parts[1], parts[0], parts[4]


REPEAT_OPTIONS = [
    ("daily", "每天", "*"),
    ("weekdays", "周一到周五", "1-5"),
    ("weekend", "周六和周日", "0,6"),
    ("sat", "仅周六", "6"),
    ("sun", "仅周日", "0"),
]


def repeat_from_dow(dow: str) -> str:
    mapping = {opt[2]: opt[0] for opt in REPEAT_OPTIONS}
    return mapping.get((dow or "*").strip(), "daily")


def cron_from_form(hour: str, minute: str, repeat: str, fallback: str = "0 7 * * *") -> str:
    mapping = {opt[0]: opt[2] for opt in REPEAT_OPTIONS}
    dow = mapping.get((repeat or "daily").strip(), "*")
    if hour == "" and minute == "":
        return fallback or "0 7 * * *"
    return fields_to_cron(hour or 7, minute or 0, dow)


# DeepSeek 账单是美元。展示用固定汇率，不跟市价。
USD_CNY = 6.7


def usd_to_cny(usd: float | None) -> float:
    return float(usd or 0) * USD_CNY


def format_cny(cny: float) -> str:
    if cny <= 0:
        return "¥0"
    if cny < 0.01:
        return f"¥{cny:.4f}"
    if cny < 1:
        return f"¥{cny:.3f}"
    return f"¥{cny:.2f}"


def money_label(run: dict[str, Any] | float | None, status: str | None = None) -> str:
    if isinstance(run, dict):
        usd = float(run.get("cost_usd") or 0)
        status = run.get("status") or status
    else:
        usd = float(run or 0)
    if status == "running" and usd <= 0:
        return "结算中"
    if usd <= 0:
        return "—"
    return format_cny(usd_to_cny(usd))


def spend_label(usd: float | None) -> str:
    return format_cny(usd_to_cny(usd))


def to_simplified(text: str) -> str:
    raw = text or ""
    if not raw.strip():
        return raw
    try:
        from zhconv import convert

        return convert(raw, "zh-cn")
    except Exception:  # noqa: BLE001
        return raw
