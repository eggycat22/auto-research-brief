from __future__ import annotations

from . import db

PROMPT_META: dict[str, dict[str, str]] = {
    "triage": {"title": "筛选", "step": "判断哪些新闻值得写成快讯"},
    "news": {"title": "快讯", "step": "把入选材料写成独立报道"},
    "github_star": {"title": "Star 精讲", "step": "把选中的仓库写成图文解读"},
    "weekly": {"title": "周回顾", "step": "周日若已有多篇精讲，再写一篇短回顾"},
    "research": {"title": "检索笔记", "step": "写稿前用工具核实仓库/论文，不直接成文"},
    "agentic_pick": {"title": "精读选题", "step": "从候选里只选一篇 Agentic 工作"},
    "agentic": {"title": "Agentic 精读", "step": "把选定工作写成问题驱动的解读"},
    "chat": {"title": "追问", "step": "点开文章后，右侧讨论的人设"},
}

TASK_PROMPTS: dict[str, tuple[str, ...]] = {
    "frontier_digest": ("triage", "news"),
    "daily_briefing": ("triage", "news"),
    "github_star": ("github_star", "weekly", "research"),
    "agentic_reading": ("agentic_pick", "agentic", "research"),
}


def for_task(typ: str) -> tuple[str, ...]:
    return TASK_PROMPTS.get(typ or "", ())


def bodies_for(*names: str) -> dict[str, str]:
    return {name: load(name) for name in names}


def load(name: str) -> str:
    override = db.prompt_override(name)
    if override.strip():
        return override
    path = db.prompts_dir() / f"{name}.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")
