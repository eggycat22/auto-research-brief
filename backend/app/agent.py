"""Bounded tool-calling loop. Daily jobs stay on a workflow; this is only the research/chat hop."""

from __future__ import annotations

from typing import Any

from . import llm, prompts_loader, tools
from .cost import Usage
from .util import dumps


def run(
    goal: str,
    *,
    usage: Usage,
    folder: str,
    max_rounds: int = 10,
    extra_system: str = "",
    budget: tools.Budget | None = None,
) -> dict[str, Any]:
    prompt = prompts_loader.load("research")
    if extra_system:
        prompt = prompt + "\n\n" + extra_system
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": goal},
    ]
    budget = budget or tools.Budget()
    log: list[str] = []
    figures: list[dict[str, Any]] = []
    for _ in range(max_rounds):
        turn = llm.chat_turn(
            messages,
            thinking=False,
            usage=usage,
            temperature=0.15,
            timeout=90,
            tools=tools.SCHEMAS,
        )
        calls = turn.get("tool_calls") or []
        if not calls:
            return {"notes": turn.get("content") or "", "figures": figures, "log": log, "rounds": len(log)}
        messages.append(_assistant_with_tools(turn))
        for call in calls:
            fn = ((call.get("function") or {}).get("name")) or ""
            args = tools.parse_args((call.get("function") or {}).get("arguments") or "{}")
            result, extra = tools.dispatch(fn, args, folder=folder, budget=budget)
            figures.extend(extra)
            log.append(f"{fn} {dumps(args)[:180]}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or "call",
                    "content": result[:8000],
                }
            )
    messages.append({"role": "user", "content": "工具轮次已用完。根据已收集证据写出笔记，不要再调用工具。材料没有的写未读。"})
    turn = llm.chat_turn(messages, thinking=False, usage=usage, temperature=0.2, timeout=120)
    return {"notes": turn.get("content") or "", "figures": figures, "log": log, "rounds": len(log)}


def chat_reply(system: str, history: list[dict[str, str]], *, usage: Usage, folder: str, thinking: bool = True) -> str:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in history:
        messages.append({"role": m["role"], "content": m["content"]})
    budget = tools.Budget(
        search_web=3,
        search_github=2,
        search_hf=2,
        search_arxiv=2,
        search_s2=1,
        search_library=4,
        fetch=6,
        github=4,
        pdf=1,
        image=2,
    )
    figures: list[dict[str, Any]] = []
    timeout = 180 if thinking else 90
    for _ in range(6):
        turn = llm.chat_turn(
            messages,
            thinking=thinking,
            usage=usage,
            temperature=0.3,
            timeout=timeout,
            tools=tools.SCHEMAS,
        )
        calls = turn.get("tool_calls") or []
        if not calls:
            return turn.get("content") or ""
        messages.append(_assistant_with_tools(turn))
        for call in calls:
            fn = ((call.get("function") or {}).get("name")) or ""
            args = tools.parse_args((call.get("function") or {}).get("arguments") or "{}")
            result, extra = tools.dispatch(fn, args, folder=folder, budget=budget)
            figures.extend(extra)
            messages.append({"role": "tool", "tool_call_id": call.get("id") or "call", "content": result[:6000]})
    messages.append({"role": "user", "content": "根据已有工具结果直接回答读者，不要再调用工具。"})
    turn = llm.chat_turn(messages, thinking=thinking, usage=usage, temperature=0.35, timeout=180)
    return turn.get("content") or ""


def _assistant_with_tools(turn: dict[str, Any]) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": turn.get("content") or None, "tool_calls": turn.get("tool_calls")}
    if turn.get("reasoning_content"):
        msg["reasoning_content"] = turn["reasoning_content"]
    return msg
