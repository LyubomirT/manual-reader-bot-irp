from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import aiohttp

from rtfm_bot.config import BotConfig

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "be",
    "by",
    "do",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "what",
    "with",
    "you",
    "your",
}


def tokenize(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 1 and token not in STOP_WORDS
    ]


@dataclass(slots=True)
class DocEntry:
    title: str
    location: str
    path: str
    url: str
    text: str
    tags: tuple[str, ...]
    title_tokens: frozenset[str] = field(init=False, repr=False)
    path_tokens: frozenset[str] = field(init=False, repr=False)
    text_tokens: frozenset[str] = field(init=False, repr=False)
    tag_tokens: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.title_tokens = frozenset(tokenize(f"{self.title} {self.location}"))
        self.path_tokens = frozenset(tokenize(self.path))
        self.text_tokens = frozenset(tokenize(self.text))
        self.tag_tokens = frozenset(tokenize(" ".join(self.tags)))

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "location": self.location,
            "path": self.path,
            "url": self.url,
            "text": self.text,
            "tags": list(self.tags),
        }


@dataclass(slots=True)
class RetrievedDoc:
    title: str
    url: str
    snippet: str
    score: float


@dataclass(slots=True)
class DocsCacheSnapshot:
    version: str | None
    fetched_at: datetime
    entries: list[DocEntry]


@dataclass(slots=True)
class RefreshResult:
    updated: bool
    version: str | None
    entry_count: int
    error: str | None = None


class DocsCacheManager:
    def __init__(self, config: BotConfig) -> None:
        self._config = config
        self._snapshot: DocsCacheSnapshot | None = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self._snapshot is not None and bool(self._snapshot.entries)

    @property
    def snapshot(self) -> DocsCacheSnapshot | None:
        return self._snapshot

    async def initialize(self, session: aiohttp.ClientSession) -> RefreshResult:
        self._config.data_dir.mkdir(parents=True, exist_ok=True)

        async with self._lock:
            self._snapshot = await asyncio.to_thread(self._load_snapshot_from_disk)

        force = self._snapshot is None
        return await self.maybe_refresh(session, force=force)

    async def maybe_refresh(
        self,
        session: aiohttp.ClientSession,
        *,
        force: bool = False,
    ) -> RefreshResult:
        async with self._lock:
            if self._snapshot is None:
                self._snapshot = await asyncio.to_thread(self._load_snapshot_from_disk)

            snapshot = self._snapshot
            upstream_version: str | None = None

            try:
                upstream_version = await self._fetch_upstream_version(session)
            except Exception:
                upstream_version = snapshot.version if snapshot is not None else None

            is_stale = snapshot is None or (
                datetime.now(UTC) - snapshot.fetched_at
                >= timedelta(seconds=self._config.cache_refresh_interval_seconds)
            )
            version_changed = snapshot is None or snapshot.version != upstream_version

            if not force and snapshot is not None and not is_stale and not version_changed:
                return RefreshResult(
                    updated=False,
                    version=snapshot.version,
                    entry_count=len(snapshot.entries),
                )

            try:
                payload = await self._fetch_search_index(session)
                new_snapshot = self._build_snapshot(
                    payload=payload,
                    version=upstream_version,
                    fetched_at=datetime.now(UTC),
                )
                await asyncio.to_thread(self._write_snapshot_to_disk, new_snapshot)
                self._snapshot = new_snapshot
                return RefreshResult(
                    updated=True,
                    version=new_snapshot.version,
                    entry_count=len(new_snapshot.entries),
                )
            except Exception as exc:
                if snapshot is not None:
                    self._snapshot = snapshot
                    return RefreshResult(
                        updated=False,
                        version=snapshot.version,
                        entry_count=len(snapshot.entries),
                        error=str(exc),
                    )

                return RefreshResult(
                    updated=False,
                    version=upstream_version,
                    entry_count=0,
                    error=str(exc),
                )

    def search(self, query: str, *, limit: int = 5) -> list[RetrievedDoc]:
        snapshot = self._snapshot
        if snapshot is None:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        query_phrase = query.casefold().strip()
        results: list[RetrievedDoc] = []

        for entry in snapshot.entries:
            score = self._score_entry(entry, query_tokens, query_phrase)
            if score <= 0:
                continue

            results.append(
                RetrievedDoc(
                    title=entry.title,
                    url=entry.url,
                    snippet=self._build_snippet(entry.text, query_tokens),
                    score=score,
                )
            )

        results.sort(key=lambda item: item.score, reverse=True)
        return results[:limit]

    async def _fetch_upstream_version(self, session: aiohttp.ClientSession) -> str | None:
        async with session.get(self._config.app_version_url) as response:
            response.raise_for_status()
            payload = await response.json()
        return str(payload.get("version") or "").strip() or None

    async def _fetch_search_index(self, session: aiohttp.ClientSession) -> dict[str, object]:
        async with session.get(self._config.docs_search_index_url) as response:
            response.raise_for_status()
            return await response.json()

    def _build_snapshot(
        self,
        *,
        payload: dict[str, object],
        version: str | None,
        fetched_at: datetime,
    ) -> DocsCacheSnapshot:
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("Unexpected search index format: missing 'items' list.")

        entries: list[DocEntry] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue

            location = str(raw_item.get("location") or "").lstrip("/")
            path = str(raw_item.get("path") or "").lstrip("/")
            title = str(raw_item.get("title") or path or location or "Untitled").strip()
            text = str(raw_item.get("text") or "").strip()

            raw_tags = raw_item.get("tags") or []
            if isinstance(raw_tags, list):
                tags = tuple(str(tag).strip() for tag in raw_tags if str(tag).strip())
            else:
                tags = tuple()

            url = urljoin(self._config.docs_base_url, location or path)
            entries.append(
                DocEntry(
                    title=title,
                    location=location,
                    path=path,
                    url=url,
                    text=text,
                    tags=tags,
                )
            )

        if not entries:
            raise ValueError("The documentation search index did not contain any entries.")

        return DocsCacheSnapshot(version=version, fetched_at=fetched_at, entries=entries)

    def _load_snapshot_from_disk(self) -> DocsCacheSnapshot | None:
        cache_path = self._config.cache_file_path
        if not cache_path.exists():
            return None

        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            return None

        fetched_at_raw = str(payload.get("fetched_at") or "")
        if not fetched_at_raw:
            return None

        entries = [
            DocEntry(
                title=str(item.get("title") or "Untitled"),
                location=str(item.get("location") or ""),
                path=str(item.get("path") or ""),
                url=str(item.get("url") or self._config.docs_base_url),
                text=str(item.get("text") or ""),
                tags=tuple(item.get("tags") or []),
            )
            for item in raw_entries
            if isinstance(item, dict)
        ]
        if not entries:
            return None

        return DocsCacheSnapshot(
            version=str(payload.get("version") or "").strip() or None,
            fetched_at=datetime.fromisoformat(fetched_at_raw),
            entries=entries,
        )

    def _write_snapshot_to_disk(self, snapshot: DocsCacheSnapshot) -> None:
        payload = {
            "version": snapshot.version,
            "fetched_at": snapshot.fetched_at.isoformat(),
            "entries": [entry.to_dict() for entry in snapshot.entries],
        }
        self._config.cache_file_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

    def _score_entry(
        self,
        entry: DocEntry,
        query_tokens: list[str],
        query_phrase: str,
    ) -> float:
        score = 0.0
        title = entry.title.casefold()
        location = entry.location.casefold()
        path = entry.path.casefold()
        text = entry.text.casefold()

        if query_phrase and query_phrase in title:
            score += 24.0
        if query_phrase and (query_phrase in location or query_phrase in path):
            score += 14.0
        if query_phrase and query_phrase in text:
            score += 6.0

        matched_tokens = 0
        for token in query_tokens:
            matched_here = False

            if token in entry.title_tokens:
                score += 8.0
                matched_here = True
            if token in entry.path_tokens:
                score += 5.0
                matched_here = True
            if token in entry.tag_tokens:
                score += 4.0
                matched_here = True
            if token in entry.text_tokens:
                score += 1.5
                matched_here = True

            if matched_here:
                matched_tokens += 1

        score += (matched_tokens / len(query_tokens)) * 10.0
        return score

    def _build_snippet(self, text: str, query_tokens: list[str], *, max_length: int = 320) -> str:
        if not text:
            return "No snippet available."

        lowered = text.casefold()
        start_index = 0

        for token in query_tokens:
            found_index = lowered.find(token)
            if found_index != -1:
                start_index = max(0, found_index - 80)
                break

        snippet = text[start_index : start_index + max_length].strip()
        snippet = re.sub(r"\s+", " ", snippet)

        if start_index > 0:
            snippet = f"... {snippet}"
        if start_index + max_length < len(text):
            snippet = f"{snippet} ..."
        return snippet

