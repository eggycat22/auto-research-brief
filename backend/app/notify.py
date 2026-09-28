from __future__ import annotations

import html
import time

import httpx

from . import db
from .config import settings

_token: dict = {"value": "", "exp": 0.0}

_ERR = {
    40001: "Secret 无效，或应用已被停用",
    40013: "CorpID 不对",
    40014: "access_token 无效，再试一次或核对 Secret",
    41001: "缺 access_token",
    42001: "access_token 过期",
    45009: "接口频率超限，稍后再发",
    48002: "应用没有发消息权限",
    60011: "没有权限发给这个接收人",
    60020: "企业可信 IP 未包含这台机器的出口地址",
    60111: "接收人 userid 不存在",
    81013: "接收人不在应用可见范围",
}


def _cred(name: str, env_val: str) -> str:
    return db.merged_secret(name, env_val, name)


def configured() -> bool:
    return bool(
        _cred("wecom_corpid", settings.wecom_corpid)
        and _cred("wecom_secret", settings.wecom_secret)
        and _cred("wecom_agentid", settings.wecom_agentid)
    )


def public_base() -> str:
    return (_cred("public_base_url", settings.public_base_url) or "").rstrip("/")


def notify_skips() -> bool:
    return db.flag("wecom_notify_skips", False)


def clear_token() -> None:
    _token["value"] = ""
    _token["exp"] = 0.0


def _clip_bytes(text: str, limit: int) -> str:
    raw = (text or "").encode("utf-8")
    if len(raw) <= limit:
        return text or ""
    cut = raw[: max(0, limit - 3)]
    while cut:
        try:
            return cut.decode("utf-8") + "…"
        except UnicodeDecodeError:
            cut = cut[:-1]
    return "…"


def _escape_line(line: str) -> str:
    return html.escape((line or "").strip(), quote=True)


def _description_html(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        lines = ["打开研读查看。"]
    parts = [f'<div class="normal">{_escape_line(ln)}</div>' for ln in lines[:8]]
    return _clip_bytes("".join(parts), 500)


def _decode_err(data: dict) -> str:
    code = int(data.get("errcode") or 0)
    hint = _ERR.get(code)
    extra = (data.get("errmsg") or "").strip()
    if hint:
        return f"errcode {code}：{hint}"
    if extra:
        return f"errcode {code}：{extra[:80]}"
    return f"errcode {code}"


def _access_token() -> str:
    now = time.time()
    if _token["value"] and _token["exp"] > now + 60:
        return _token["value"]
    corpid = _cred("wecom_corpid", settings.wecom_corpid)
    secret = _cred("wecom_secret", settings.wecom_secret)
    if not corpid or not secret:
        raise RuntimeError("missing wecom creds")
    with httpx.Client(timeout=20) as client:
        r = client.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": corpid, "corpsecret": secret},
        )
        r.raise_for_status()
        data = r.json()
    if data.get("errcode"):
        raise RuntimeError(_decode_err(data))
    _token["value"] = data.get("access_token") or ""
    _token["exp"] = now + int(data.get("expires_in") or 7200)
    return _token["value"]


def _agent_id() -> int | None:
    raw = str(_cred("wecom_agentid", settings.wecom_agentid) or "").strip()
    try:
        return int(raw)
    except ValueError:
        return None


def _post_message(payload: dict) -> str:
    try:
        token = _access_token()
    except Exception as exc:  # noqa: BLE001
        return f"token:{exc}"
    agent_n = _agent_id()
    if agent_n is None:
        return "AgentId 必须是数字"
    payload = {**payload, "agentid": agent_n, "touser": _cred("wecom_touser", settings.wecom_touser) or "@all"}
    with httpx.Client(timeout=20) as client:
        r = client.post(
            "https://qyapi.weixin.qq.com/cgi-bin/message/send",
            params={"access_token": token},
            json=payload,
        )
    if r.status_code >= 400:
        return f"http {r.status_code}"
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        return "bad-json"
    if data.get("errcode"):
        if int(data.get("errcode") or 0) in {40014, 42001}:
            clear_token()
        return _decode_err(data)
    return "ok"


def send_card(title: str, description: str, item_id: int | None = None) -> str:
    if not configured():
        return "skipped: no wecom"
    heading = _clip_bytes((title or "研读").strip() or "研读", 120)
    desc = (description or "").strip()
    base = public_base()
    url = ""
    if base:
        url = f"{base}/item/{item_id}" if item_id else base
    if url:
        return _post_message(
            {
                "msgtype": "textcard",
                "textcard": {
                    "title": heading,
                    "description": _description_html(desc),
                    "url": url,
                    "btntxt": "打开",
                },
            }
        )
    body = _clip_bytes(f"{heading}\n{desc}".strip(), 2000)
    return _post_message({"msgtype": "text", "text": {"content": body}})


def send_skip(title: str, description: str) -> str:
    if not notify_skips():
        return "skipped: mute-empty"
    return send_card(title, description, None)


def send_test() -> str:
    return send_card("研读已接通", "通知通道可用。点开可进网页。")


def send_report(title: str, summary: str, item_id: int | None = None) -> str:
    return send_card(title, summary, item_id)
