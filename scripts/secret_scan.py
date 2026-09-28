"""Scan the tree for strings that must not go into a public repo."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "data",
    "backups",
    "canvases",
    "_api_docs_extract",
    "_api_test_output",
    "node_modules",
}
SKIP_SUFFIX = {".db", ".db-wal", ".db-shm", ".zip", ".jpg", ".png", ".webp", ".pyc", ".key"}
PATTERNS = [
    ("openai-like key", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("private key block", re.compile(r"BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY")),
    ("bearer token", re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*")),
]

def iter_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIX:
            continue
        if path.name in {".env", "dotenv.env"} or path.name.endswith(".secret"):
            print(f"SKIP_SECRET_FILE {path.relative_to(ROOT)}")
            continue
        out.append(path)
    return out


def main() -> int:
    hits = 0
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(ROOT)
        for label, pat in PATTERNS:
            for match in pat.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                print(f"{rel}:{line}: {label}")
                hits += 1
    if hits:
        print(f"found {hits} possible secret(s); do not publish until cleaned")
        return 1
    print("no obvious secrets in tracked-like files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
