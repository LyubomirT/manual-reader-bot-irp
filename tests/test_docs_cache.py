from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from rtfm_bot.docs_cache import CachedDocPage, DocsCacheManager, DocsCacheSnapshot, DocsPage


class FakeResponse:
    def __init__(self, body: str, *, status: int = 200) -> None:
        self._body = body
        self.status = status

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def text(self) -> str:
        return self._body


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.requested_url: str | None = None

    def get(self, url: str) -> FakeResponse:
        self.requested_url = url
        return self._response


class DocsCacheManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.data_dir = Path(self._temp_dir.name)

    def _make_config(self) -> SimpleNamespace:
        return SimpleNamespace(
            app_version_url="https://example.com/version.json",
            docs_search_index_url="https://example.com/search.json",
            docs_search_index_file=None,
            cache_refresh_interval_seconds=60,
            docs_fetch_concurrency=1,
            docs_base_url="https://example.com/docs/",
            data_dir=self.data_dir,
            cache_file_path=self.data_dir / "docs_cache.json",
            database_file_path=self.data_dir / "reader.sqlite3",
        )

    async def test_fetch_upstream_version_parses_json_from_text_plain_response(self) -> None:
        manager = DocsCacheManager(self._make_config())
        session = FakeSession(
            FakeResponse(
                '{"version":"2.5.1-patch","aua":true,"severity":2}',
            )
        )

        version = await manager._fetch_upstream_version(session)

        self.assertEqual(version, "2.5.1-patch")
        self.assertEqual(session.requested_url, "https://example.com/version.json")

    async def test_maybe_refresh_keeps_existing_version_when_upstream_version_is_missing(self) -> None:
        manager = DocsCacheManager(self._make_config())
        manager._snapshot = DocsCacheSnapshot(
            version="2.5.0",
            fetched_at=datetime.now(UTC) - timedelta(hours=2),
            page_count=1,
        )
        manager._pages = [CachedDocPage(url="https://example.com/docs/page", title="Page", content="cached")]

        async def fake_fetch_upstream_version(_session) -> str | None:
            return None

        async def fake_fetch_search_index(_session) -> dict[str, object]:
            return {"items": []}

        def fake_group_pages(_payload: dict[str, object]) -> list[DocsPage]:
            return [
                DocsPage(
                    url="https://example.com/docs/page",
                    title="Page",
                    fallback_text="fresh",
                )
            ]

        async def fake_build_pages(_session, _pages: list[DocsPage]) -> list[CachedDocPage]:
            return [
                CachedDocPage(
                    url="https://example.com/docs/page",
                    title="Page",
                    content="fresh",
                )
            ]

        written_snapshot: DocsCacheSnapshot | None = None

        def fake_replace_pages_sync(_pages: list[CachedDocPage]) -> None:
            return None

        def fake_write_snapshot_to_disk(snapshot: DocsCacheSnapshot) -> None:
            nonlocal written_snapshot
            written_snapshot = snapshot

        manager._fetch_upstream_version = fake_fetch_upstream_version
        manager._fetch_search_index = fake_fetch_search_index
        manager._group_pages = fake_group_pages
        manager._build_pages = fake_build_pages
        manager._replace_pages_sync = fake_replace_pages_sync
        manager._write_snapshot_to_disk = fake_write_snapshot_to_disk

        result = await manager.maybe_refresh(object(), force=True)

        self.assertTrue(result.updated)
        self.assertEqual(result.version, "2.5.0")
        self.assertIsNotNone(written_snapshot)
        self.assertEqual(written_snapshot.version, "2.5.0")


if __name__ == "__main__":
    unittest.main()
