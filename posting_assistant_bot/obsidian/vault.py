from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ObsidianSearchResult:
    path: str
    snippet: str


class ObsidianVault:
    def __init__(self, root_path: str | None) -> None:
        self._root_path = Path(root_path).expanduser().resolve() if root_path else None

    def is_configured(self) -> bool:
        return self._root_path is not None

    async def search(self, query: str, limit: int = 5) -> list[ObsidianSearchResult]:
        return await asyncio.to_thread(self._search_sync, query, limit)

    async def read(self, relative_path: str) -> str:
        if self._root_path is None:
            raise ValueError("Obsidian vault is not configured")
        absolute_path = (self._root_path / relative_path).resolve()
        return await asyncio.to_thread(absolute_path.read_text, "utf-8")

    def _search_sync(self, query: str, limit: int) -> list[ObsidianSearchResult]:
        if self._root_path is None or not query.strip():
            return []

        normalized_query = query.lower()
        matches: list[tuple[int, ObsidianSearchResult]] = []

        for file_path in _collect_files(self._root_path):
            if file_path.suffix.lower() not in {".md", ".txt"}:
                continue

            content = _safe_read_text(file_path)
            if not content:
                continue

            relative_path = file_path.relative_to(self._root_path).as_posix()
            haystack = f"{relative_path}\n{content}".lower()
            if normalized_query not in haystack:
                continue

            score = _score_match(relative_path, content, normalized_query)
            matches.append(
                (
                    score,
                    ObsidianSearchResult(
                        path=relative_path,
                        snippet=_build_snippet(content, normalized_query),
                    ),
                )
            )

        matches.sort(key=lambda item: item[0], reverse=True)
        return [result for _, result in matches[:limit]]


def _collect_files(root_path: Path) -> list[Path]:
    result: list[Path] = []

    for current_root, dirs, files in root_path.walk():
        dirs[:] = [directory for directory in dirs if not directory.startswith(".")]

        for filename in files:
            if filename.startswith("."):
                continue
            result.append(current_root / filename)

    return result


def _safe_read_text(file_path: Path) -> str | None:
    try:
        return file_path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _score_match(relative_path: str, content: str, query: str) -> int:
    path_score = 5 if query in relative_path.lower() else 0
    content_score = content.lower().count(query)
    return path_score + content_score


def _build_snippet(content: str, query: str) -> str:
    lower = content.lower()
    index = lower.find(query)
    if index == -1:
        return " ".join(content[:240].split()).strip()

    start = max(0, index - 100)
    end = min(len(content), index + len(query) + 140)
    return " ".join(content[start:end].split()).strip()
