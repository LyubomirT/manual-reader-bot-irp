from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import aiohttp

from rtfm_bot.config import BotConfig

LOGGER = logging.getLogger(__name__)

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

NOISE_PARAGRAPHS = {
    "contents",
    "table of contents",
    "edit on github",
    "view page source",
    "previous",
    "next",
    "navigation",
}


def tokenize(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 1 and token not in STOP_WORDS
    ]


def strip_inline_html(value: str) -> str:
    no_tags = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()


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
    page_count: int
    chunk_count: int


@dataclass(slots=True)
class RefreshResult:
    updated: bool
    version: str | None
    entry_count: int
    error: str | None = None


@dataclass(slots=True)
class DocsPage:
    url: str
    title: str
    fallback_text: str


class DocsHtmlTextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "article",
        "blockquote",
        "br",
        "code",
        "dd",
        "div",
        "dt",
        "figcaption",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
    SKIP_TAGS = {"aside", "footer", "form", "header", "nav", "noscript", "script", "style", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capture_markers: list[str] = []
        self._skip_depth = 0
        self._parts: list[str] = []
        self._captured_any = False

    @classmethod
    def extract(cls, source_html: str) -> str:
        parser = cls()
        parser.feed(source_html)
        parser.close()

        extracted = parser.get_text()
        if extracted.strip():
            return extracted

        cleaned = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", source_html)
        cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
        return html.unescape(cleaned)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name: value or "" for name, value in attrs}

        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return

        if self._should_capture(tag, attr_map):
            self._capture_markers.append(tag)
            self._captured_any = True

        if self._is_capturing and tag in self.BLOCK_TAGS:
            self._append_break()

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return

        if self._is_capturing and tag in self.BLOCK_TAGS:
            self._append_break()

        if self._capture_markers and tag == self._capture_markers[-1]:
            self._capture_markers.pop()

    def handle_data(self, data: str) -> None:
        if not self._is_capturing:
            return

        if data.strip():
            self._parts.append(data)

    def get_text(self) -> str:
        if not self._captured_any:
            return ""
        return "".join(self._parts)

    @property
    def _is_capturing(self) -> bool:
        return not self._skip_depth and bool(self._capture_markers)

    def _append_break(self) -> None:
        if self._parts and self._parts[-1] != "\n":
            self._parts.append("\n")

    def _should_capture(self, tag: str, attrs: dict[str, str]) -> bool:
        if tag in {"article", "main"}:
            return True

        class_attr = f" {attrs.get('class', '').casefold()} "
        return (
            attrs.get("role", "").casefold() == "main"
            or attrs.get("itemprop", "").casefold() == "articlebody"
            or " document " in class_attr
            or " rst-content " in class_attr
            or " wy-nav-content " in class_attr
        )


class DocsCacheManager:
    def __init__(self, config: BotConfig) -> None:
        self._config = config
        self._snapshot: DocsCacheSnapshot | None = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self._snapshot is not None and self._snapshot.chunk_count > 0

    @property
    def snapshot(self) -> DocsCacheSnapshot | None:
        return self._snapshot

    async def initialize(self, session: aiohttp.ClientSession) -> RefreshResult:
        self._config.data_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._initialize_database)

        async with self._lock:
            snapshot = await asyncio.to_thread(self._load_snapshot_from_disk)
            chunk_count = await asyncio.to_thread(self._count_chunks_sync)
            self._snapshot = snapshot if snapshot is not None and chunk_count > 0 else None

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
                snapshot = await asyncio.to_thread(self._load_snapshot_from_disk)
                chunk_count = await asyncio.to_thread(self._count_chunks_sync)
                self._snapshot = snapshot if snapshot is not None and chunk_count > 0 else None

            snapshot = self._snapshot
            upstream_version: str | None

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
                    entry_count=snapshot.chunk_count,
                )

            try:
                payload = await self._fetch_search_index(session)
                pages = self._group_pages(payload)
                chunks = await self._build_chunks(session, pages)
                if not chunks:
                    raise ValueError("No documentation chunks could be built.")

                new_snapshot = DocsCacheSnapshot(
                    version=upstream_version,
                    fetched_at=datetime.now(UTC),
                    page_count=len(pages),
                    chunk_count=len(chunks),
                )
                await asyncio.to_thread(self._replace_chunks_sync, chunks)
                await asyncio.to_thread(self._write_snapshot_to_disk, new_snapshot)
                self._snapshot = new_snapshot
                return RefreshResult(
                    updated=True,
                    version=new_snapshot.version,
                    entry_count=new_snapshot.chunk_count,
                )
            except Exception as exc:
                if snapshot is not None:
                    self._snapshot = snapshot
                    return RefreshResult(
                        updated=False,
                        version=snapshot.version,
                        entry_count=snapshot.chunk_count,
                        error=str(exc),
                    )

                return RefreshResult(
                    updated=False,
                    version=upstream_version,
                    entry_count=0,
                    error=str(exc),
                )

    def search(self, query: str, *, limit: int = 5) -> list[RetrievedDoc]:
        if not self.available:
            return []

        tokens = tokenize(query)
        if not tokens:
            return []

        query_phrase = query.casefold().strip()
        fts_query = " OR ".join(f"{token}*" for token in tokens[:12])
        if not fts_query:
            return []

        with self._connect() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT
                        page_url,
                        title,
                        chunk_text,
                        snippet(docs_chunks_fts, 2, '', '', ' ... ', 28) AS snippet,
                        bm25(docs_chunks_fts, 8.0, 1.0) AS rank
                    FROM docs_chunks_fts
                    WHERE docs_chunks_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, limit * 6),
                ).fetchall()
            except sqlite3.OperationalError:
                LOGGER.exception("FTS search failed for query: %s", query)
                return []

        rescored: list[RetrievedDoc] = []
        page_counts: Counter[str] = Counter()
        for row in rows:
            url = row["page_url"]
            if page_counts[url] >= 2:
                continue

            title = row["title"]
            chunk_text = row["chunk_text"]
            snippet = (row["snippet"] or "").strip() or self._build_snippet(chunk_text, tokens)
            score = self._rescore_match(
                title=title,
                chunk_text=chunk_text,
                query_phrase=query_phrase,
                query_tokens=tokens,
                rank=float(row["rank"]),
            )

            rescored.append(
                RetrievedDoc(
                    title=title,
                    url=url,
                    snippet=snippet,
                    score=score,
                )
            )
            page_counts[url] += 1

        rescored.sort(key=lambda item: item.score, reverse=True)
        return rescored[:limit]

    async def _fetch_upstream_version(self, session: aiohttp.ClientSession) -> str | None:
        async with session.get(self._config.app_version_url) as response:
            response.raise_for_status()
            payload = await response.json()
        return str(payload.get("version") or "").strip() or None

    async def _fetch_search_index(self, session: aiohttp.ClientSession) -> dict[str, object]:
        if self._config.docs_search_index_file is not None:
            return await asyncio.to_thread(self._read_local_search_index, self._config.docs_search_index_file)

        async with session.get(self._config.docs_search_index_url) as response:
            response.raise_for_status()
            return await response.json()

    def _read_local_search_index(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _group_pages(self, payload: dict[str, object]) -> list[DocsPage]:
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("Unexpected search index format: missing 'items' list.")

        page_map: dict[str, dict[str, object]] = {}
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue

            location = str(raw_item.get("location") or "").strip()
            page_location = location.split("#", 1)[0]
            page_url = urljoin(self._config.docs_base_url, page_location)
            title = self._title_from_item(raw_item)
            snippet = strip_inline_html(str(raw_item.get("text") or ""))

            bucket = page_map.setdefault(
                page_url,
                {"title": title, "parts": [], "seen": set()},
            )

            if bucket["title"] == "Untitled" and title != "Untitled":
                bucket["title"] = title

            if snippet and snippet not in bucket["seen"]:
                bucket["parts"].append(snippet)
                bucket["seen"].add(snippet)

        pages = []
        for page_url, bucket in page_map.items():
            fallback_text = "\n\n".join(bucket["parts"]).strip()
            pages.append(
                DocsPage(
                    url=page_url,
                    title=str(bucket["title"]),
                    fallback_text=fallback_text,
                )
            )

        if not pages:
            raise ValueError("The documentation search index did not contain any pages.")

        return pages

    async def _build_chunks(
        self,
        session: aiohttp.ClientSession,
        pages: list[DocsPage],
    ) -> list[tuple[str, str, str]]:
        semaphore = asyncio.Semaphore(self._config.docs_fetch_concurrency)

        async def build_for_page(page: DocsPage) -> list[tuple[str, str, str]]:
            async with semaphore:
                fetched_text = await self._fetch_page_text(session, page.url)

            source_text = self._normalize_text(fetched_text or page.fallback_text)
            if not source_text:
                return []

            chunks = self._chunk_text(source_text)
            return [(page.url, page.title, chunk) for chunk in chunks]

        nested_chunks = await asyncio.gather(*(build_for_page(page) for page in pages))
        return [chunk for page_chunks in nested_chunks for chunk in page_chunks]

    async def _fetch_page_text(self, session: aiohttp.ClientSession, page_url: str) -> str | None:
        try:
            async with session.get(page_url) as response:
                response.raise_for_status()
                page_html = await response.text()
        except Exception:
            return None

        extracted = DocsHtmlTextExtractor.extract(page_html)
        normalized = self._normalize_text(extracted)
        return normalized or None

    def _title_from_item(self, raw_item: dict[str, object]) -> str:
        raw_title = strip_inline_html(str(raw_item.get("title") or ""))
        if raw_title:
            return raw_title

        raw_path = raw_item.get("path")
        if isinstance(raw_path, list):
            parts = [strip_inline_html(str(part)) for part in raw_path if str(part).strip()]
            if parts:
                return " / ".join(parts)

        return "Untitled"

    def _normalize_text(self, value: str) -> str:
        if not value:
            return ""

        text = html.unescape(value).replace("\r", "")
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]

        paragraphs: list[str] = []
        current: list[str] = []
        seen: set[str] = set()

        for line in lines:
            lowered = line.casefold()
            if not line:
                if current:
                    paragraph = " ".join(current).strip()
                    if paragraph and paragraph.casefold() not in seen:
                        paragraphs.append(paragraph)
                        seen.add(paragraph.casefold())
                    current = []
                continue

            if lowered in NOISE_PARAGRAPHS or lowered.startswith("skip to content"):
                continue

            current.append(line)

        if current:
            paragraph = " ".join(current).strip()
            if paragraph and paragraph.casefold() not in seen:
                paragraphs.append(paragraph)

        return "\n\n".join(paragraphs)

    def _chunk_text(self, text: str) -> list[str]:
        paragraphs = [paragraph for paragraph in text.split("\n\n") if paragraph.strip()]
        if not paragraphs:
            return []

        normalized_paragraphs: list[str] = []
        for paragraph in paragraphs:
            if len(paragraph) <= self._config.rag_chunk_size_chars:
                normalized_paragraphs.append(paragraph)
                continue

            normalized_paragraphs.extend(self._split_long_paragraph(paragraph))

        chunks: list[str] = []
        current: list[str] = []
        current_size = 0

        for paragraph in normalized_paragraphs:
            addition = len(paragraph) + (2 if current else 0)
            if current and current_size + addition > self._config.rag_chunk_size_chars:
                chunk = "\n\n".join(current).strip()
                if chunk:
                    chunks.append(chunk)
                current = self._overlap_tail(current)
                current_size = len("\n\n".join(current)) if current else 0

            current.append(paragraph)
            current_size = len("\n\n".join(current))

        if current:
            chunk = "\n\n".join(current).strip()
            if chunk:
                chunks.append(chunk)

        return chunks

    def _split_long_paragraph(self, paragraph: str) -> list[str]:
        pieces: list[str] = []
        remaining = paragraph.strip()
        limit = self._config.rag_chunk_size_chars

        while remaining:
            if len(remaining) <= limit:
                pieces.append(remaining)
                break

            split_at = max(
                remaining.rfind(". ", 0, limit),
                remaining.rfind("; ", 0, limit),
                remaining.rfind(", ", 0, limit),
                remaining.rfind(" ", 0, limit),
            )
            if split_at <= 0:
                split_at = limit

            piece = remaining[:split_at].strip()
            if piece:
                pieces.append(piece)
            remaining = remaining[split_at:].lstrip()

        return pieces

    def _overlap_tail(self, paragraphs: list[str]) -> list[str]:
        overlap_target = self._config.rag_chunk_overlap_chars
        if overlap_target <= 0:
            return []

        tail: list[str] = []
        consumed = 0
        for paragraph in reversed(paragraphs):
            paragraph_length = len(paragraph) + (2 if tail else 0)
            if tail and consumed + paragraph_length > overlap_target:
                break

            tail.insert(0, paragraph)
            consumed += paragraph_length
            if consumed >= overlap_target:
                break

        return tail

    def _rescore_match(
        self,
        *,
        title: str,
        chunk_text: str,
        query_phrase: str,
        query_tokens: list[str],
        rank: float,
    ) -> float:
        lowered_title = title.casefold()
        lowered_text = chunk_text.casefold()
        score = -rank

        if query_phrase and query_phrase in lowered_title:
            score += 24.0
        if query_phrase and query_phrase in lowered_text:
            score += 8.0

        matched_tokens = 0
        for token in query_tokens:
            if token in lowered_title:
                score += 6.0
                matched_tokens += 1
            elif token in lowered_text:
                score += 1.5
                matched_tokens += 1

        score += (matched_tokens / len(query_tokens)) * 10.0
        return score

    def _build_snippet(self, text: str, query_tokens: list[str], *, max_length: int = 320) -> str:
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

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS docs_chunks_fts
                USING fts5(
                    page_url UNINDEXED,
                    title,
                    chunk_text,
                    tokenize = 'unicode61'
                )
                """
            )
            connection.commit()

    def _replace_chunks_sync(self, chunks: list[tuple[str, str, str]]) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM docs_chunks_fts")
            connection.executemany(
                """
                INSERT INTO docs_chunks_fts (page_url, title, chunk_text)
                VALUES (?, ?, ?)
                """,
                chunks,
            )
            connection.commit()

    def _count_chunks_sync(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS chunk_count FROM docs_chunks_fts"
            ).fetchone()
        return int(row["chunk_count"]) if row is not None else 0

    def _load_snapshot_from_disk(self) -> DocsCacheSnapshot | None:
        cache_path = self._config.cache_file_path
        if not cache_path.exists():
            return None

        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        fetched_at_raw = str(payload.get("fetched_at") or "")
        if not fetched_at_raw:
            return None

        return DocsCacheSnapshot(
            version=str(payload.get("version") or "").strip() or None,
            fetched_at=datetime.fromisoformat(fetched_at_raw),
            page_count=int(payload.get("page_count") or 0),
            chunk_count=int(payload.get("chunk_count") or 0),
        )

    def _write_snapshot_to_disk(self, snapshot: DocsCacheSnapshot) -> None:
        payload = {
            "version": snapshot.version,
            "fetched_at": snapshot.fetched_at.isoformat(),
            "page_count": snapshot.page_count,
            "chunk_count": snapshot.chunk_count,
        }
        self._config.cache_file_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._config.database_file_path)
        connection.row_factory = sqlite3.Row
        return connection
