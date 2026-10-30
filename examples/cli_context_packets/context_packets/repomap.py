"""Call the entire original Aider RepoMap implementation without a cloud model."""

from __future__ import annotations

import hashlib
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

from .transport import ROOT

sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "packets/aider/source"))
from aider.repomap import RepoMap


class LocalTokenBudget:
    """Same explicitly approximate UTF-8 byte heuristic used by Codex: ceil(bytes/4)."""

    def token_count(self, text: str) -> int:
        return (len(text.encode()) + 3) // 4


class FileIO:
    def read_text(self, filename):
        try:
            return Path(filename).read_text(encoding="utf-8")
        except (UnicodeError, OSError) as error:
            self.tool_warning(f"RepoMap cannot read {filename}: {error}")
            return None

    def tool_output(self, text):
        print(text, file=sys.stderr)

    tool_warning = tool_error = tool_output


def collect(root: str, files: list[str], *, query: str, max_tokens: int = 4096) -> dict:
    target = Path(root).resolve(strict=True)
    for filename in files:
        if not Path(filename).resolve(strict=True).is_relative_to(target):
            raise ValueError(f"RepoMap input leaves root: {filename}")
    cache = ROOT / "var/aider-cache" / hashlib.sha256(str(target).encode()).hexdigest()
    cache.mkdir(parents=True, exist_ok=True)

    class IsolatedRepoMap(RepoMap):
        TAGS_CACHE_DIR = str(cache)

    logs = io.StringIO()
    with redirect_stdout(logs):
        mapper = IsolatedRepoMap(
            root=str(target),
            main_model=LocalTokenBudget(),
            io=FileIO(),
            map_tokens=max_tokens,
            refresh="always",
        )
        try:
            content = mapper.get_repo_map([], files, mentioned_idents=set(query.split())) or ""
        finally:
            if hasattr(mapper.TAGS_CACHE, "close"):
                mapper.TAGS_CACHE.close()
    return {
        "content": content,
        "files": files,
        "logs": logs.getvalue(),
        "token_budget": max_tokens,
        "tokenizer": "codex-compatible-approx-utf8-bytes/4",
    }
