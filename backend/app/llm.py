from __future__ import annotations

from collections.abc import Iterator

import httpx

from . import db
from .config import settings
from .cost import Usage


def _base() -> str:
    return (db.merged_secret("llm_base_url", settings.llm_base_url) or "https://api.deepseek.com").rstrip("/")


def _model() -> str:
    return db.merged_secret("llm_model", settings.llm_model) or "deepseek-flash"


def _key() -> str:
    return db.merged_secret("llm_api_key", settings.llm_api_key, "llm_api_key")


def chat_url() -> str:
    base = _base()
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _uses_deepseek() -> bool:
    blob = f"{_base()} {_model()}".lower()
    return "deepseek" in blob


def _payload(messages: list[dict], *, thinking: bool, temperature: float, extra: dict | None = None) -> dict:
    payload: dict = {
        "model": _model(),
        "messages": messages,
        "temperature": temperature,
    }
    if _uses_deepseek():
        payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        if thinking:
            payload["reasoning_effort"] = "high"
    if extra:
        payload.update(extra)
    return payload


def _headers() -> dict[str, str]:
    key = _key()
    if not key:
        raise RuntimeError("未配置 LLM_API_KEY")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _post(payload: dict, timeout: float):
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=20)) as client:
        return client.post(chat_url(), headers=_headers(), json=payload)


def _drop_vendor_fields(payload: dict) -> dict:
    slim = dict(payload)
    slim.pop("thinking", None)
    slim.pop("reasoning_effort", None)
    slim.pop("tools", None)
    slim.pop("tool_choice", None)
    return slim


def chat(
    messages: list[dict],
    *,
    thinking: bool,
    usage: Usage | None = None,
    temperature: float = 0.4,
    timeout: float = 180,
) -> str:
    payload = _payload(messages, thinking=thinking, temperature=temperature)
    r = _post(payload, timeout)
    if r.status_code == 400 and ("thinking" in payload or "reasoning_effort" in payload):
        r = _post(_drop_vendor_fields(payload), timeout)
    if r.status_code == 401:
        raise RuntimeError("LLM 401：密钥被拒绝")
    if r.status_code >= 400:
        raise RuntimeError(f"LLM HTTP {r.status_code}")
    data = r.json()
    if usage is not None:
        usage.add_from_api(data)
    return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def chat_turn(
    messages: list[dict],
    *,
    thinking: bool,
    usage: Usage | None = None,
    temperature: float = 0.2,
    timeout: float = 120,
    tools: list[dict] | None = None,
) -> dict:
    extra = {}
    if tools:
        extra["tools"] = tools
        extra["tool_choice"] = "auto"
    payload = _payload(messages, thinking=thinking, temperature=temperature, extra=extra or None)
    r = _post(payload, timeout)
    if r.status_code == 400:
        retry = _drop_vendor_fields(payload)
        if tools:
            retry["tools"] = tools
            retry["tool_choice"] = "auto"
        r = _post(retry, timeout)
        if r.status_code == 400 and tools:
            r = _post(_drop_vendor_fields(payload), timeout)
    if r.status_code == 401:
        raise RuntimeError("LLM 401：密钥被拒绝")
    if r.status_code >= 400:
        raise RuntimeError(f"LLM HTTP {r.status_code}")
    data = r.json()
    if usage is not None:
        usage.add_from_api(data)
    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    return {
        "role": "assistant",
        "content": msg.get("content") or "",
        "tool_calls": msg.get("tool_calls") or [],
        "reasoning_content": msg.get("reasoning_content"),
    }


def chat_stream(
    messages: list[dict],
    *,
    thinking: bool,
    usage: Usage | None = None,
    temperature: float = 0.5,
) -> Iterator[str]:
    payload = _payload(
        messages,
        thinking=thinking,
        temperature=temperature,
        extra={"stream": True, "stream_options": {"include_usage": True}},
    )
    headers = _headers()
    with httpx.Client(timeout=httpx.Timeout(180, connect=20)) as client:
        with client.stream("POST", chat_url(), headers=headers, json=payload) as r:
            if r.status_code == 401:
                raise RuntimeError("LLM 401：密钥被拒绝")
            if r.status_code >= 400:
                raise RuntimeError(f"LLM HTTP {r.status_code}")
            for line in r.iter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    line = line[6:]
                if line.strip() == "[DONE]":
                    break
                import json

                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if usage is not None and chunk.get("usage"):
                    usage.add_from_api(chunk)
                delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content")
                if delta:
                    yield delta
