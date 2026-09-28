"""OpenAI-style tool schemas and a bounded dispatcher."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import collect, media, pdftool, search
from .util import dumps, truncate, url_is_safe

SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search one backend. Snippets are clues, not confirmation. After a useful hit, fetch_url or read_pdf.",
            "parameters": {
                "type": "object",
                "properties": {
                    "backend": {
                        "type": "string",
                        "enum": ["web", "github", "huggingface", "arxiv", "s2", "library"],
                    },
                    "query": {"type": "string"},
                },
                "required": ["backend", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a public http(s) page as text. Use this to verify search hits.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_tree",
            "description": "List files in a GitHub repo path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "full_name": {"type": "string", "description": "owner/repo"},
                    "path": {"type": "string"},
                    "branch": {"type": "string"},
                },
                "required": ["full_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_file",
            "description": "Read a text file from a GitHub repo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "full_name": {"type": "string"},
                    "path": {"type": "string"},
                    "branch": {"type": "string"},
                },
                "required": ["full_name", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_pdf",
            "description": "Download a PDF and extract limited pages plus a few figures. Prefer arXiv / author PDFs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "pages": {"type": "string", "description": "e.g. 1-4 or 1,3,8"},
                    "extract_figures": {"type": "boolean"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_image",
            "description": "Save an official remote image into local /media for the report. Do not generate images.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}, "index": {"type": "integer"}},
                "required": ["url"],
            },
        },
    },
]


@dataclass
class Budget:
    search_web: int = 6
    search_github: int = 4
    search_hf: int = 3
    search_arxiv: int = 3
    search_s2: int = 3
    search_library: int = 4
    fetch: int = 12
    github: int = 10
    pdf: int = 2
    image: int = 6
    used: dict[str, int] = field(default_factory=dict)

    def take(self, key: str, limit: int) -> str | None:
        n = self.used.get(key, 0) + 1
        if n > limit:
            return f"budget exceeded for {key} ({limit})"
        self.used[key] = n
        return None


def dispatch(name: str, args: dict[str, Any], *, folder: str, budget: Budget) -> tuple[str, list[dict[str, Any]]]:
    figs: list[dict[str, Any]] = []
    try:
        if name == "search":
            backend = str(args.get("backend") or "web")
            key = "search_" + ("web" if backend in {"web", "ddg"} else backend if backend != "huggingface" else "hf")
            limits = {
                "search_web": budget.search_web,
                "search_github": budget.search_github,
                "search_hf": budget.search_hf,
                "search_arxiv": budget.search_arxiv,
                "search_s2": budget.search_s2,
                "search_library": budget.search_library,
            }
            err = budget.take(key, limits.get(key, 3))
            if err:
                return err, figs
            return dumps(search.search(backend, str(args.get("query") or ""))), figs
        if name == "fetch_url":
            err = budget.take("fetch", budget.fetch)
            if err:
                return err, figs
            url = str(args.get("url") or "")
            if not url_is_safe(url):
                return "不允许抓取该地址", figs
            text = collect.fetch_url_text(url)
            return truncate(text, 8000), figs
        if name == "github_tree":
            err = budget.take("github", budget.github)
            if err:
                return err, figs
            names = collect.fetch_github_tree(
                str(args.get("full_name") or ""),
                str(args.get("branch") or "main"),
                str(args.get("path") or ""),
            )
            return dumps(names), figs
        if name == "github_file":
            err = budget.take("github", budget.github)
            if err:
                return err, figs
            text = collect.fetch_github_file(
                str(args.get("full_name") or ""),
                str(args.get("path") or ""),
                str(args.get("branch") or "main"),
            )
            return truncate(text or "(empty or missing)", 8000), figs
        if name == "read_pdf":
            err = budget.take("pdf", budget.pdf)
            if err:
                return err, figs
            rec = pdftool.read_pdf(
                str(args.get("url") or ""),
                folder=folder,
                pages=str(args.get("pages") or ""),
                extract_figures=bool(args.get("extract_figures", True)),
            )
            figs.extend([f for f in (rec.get("figures") or []) if f.get("saved")])
            slim = dict(rec)
            if slim.get("figures"):
                slim["figures"] = [
                    {"saved": f.get("saved"), "local_path": f.get("local_path"), "source_url": f.get("source_url"), "note": f.get("note")}
                    for f in slim["figures"]
                ]
            return dumps(slim), figs
        if name == "save_image":
            err = budget.take("image", budget.image)
            if err:
                return err, figs
            rec = media.save_remote_image(folder, str(args.get("url") or ""), int(args.get("index") or budget.used.get("image", 1)))
            if rec.get("saved"):
                figs.append(rec)
            return dumps(rec), figs
        return f"unknown tool {name}", figs
    except Exception as exc:  # noqa: BLE001
        return f"tool error {type(exc).__name__}: {exc}", figs


def parse_args(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
