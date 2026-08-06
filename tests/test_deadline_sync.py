import asyncio
import tempfile
import unittest
import unicodedata
from pathlib import Path
from unittest.mock import patch

import aiosqlite

from database import queries
from utils.chapter_helper import (
    normalize_chapter_number,
    normalize_series_name,
    series_names_match,
)


class DeadlineSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = Path(handle.name)

        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(
                """
                CREATE TABLE deadlines (
                    id INTEGER PRIMARY KEY,
                    guild_id TEXT,
                    chapter_name TEXT NOT NULL,
                    chapter_number,
                    series_name TEXT NOT NULL,
                    role_type TEXT NOT NULL,
                    drive_link TEXT,
                    batch_id TEXT,
                    assigned_to TEXT,
                    assigned_username TEXT,
                    assigned_at TEXT,
                    deadline_at TEXT,
                    status TEXT,
                    created_at TEXT
                );
                CREATE TABLE assignment_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT,
                    deadline_id INTEGER,
                    user_id TEXT,
                    username TEXT,
                    action TEXT,
                    timestamp TEXT
                );
                """
            )
            await db.executemany(
                """
                INSERT INTO deadlines
                    (id, guild_id, chapter_name, chapter_number, series_name,
                     role_type, status, deadline_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (1, "123", "Chap 11", "11", "Truyện A", "editfull", "available", None),
                    (2, "global", "Chap 12", 12, "Legacy", "editfull", "available", None),
                    (3, "123", "Chap 11", 11, "Truyện A", "editfull", "assigned", "2099-01-01 00:00:00"),
                ],
            )
            await db.commit()

        async def fake_get_db():
            db = await aiosqlite.connect(self.db_path)
            db.row_factory = aiosqlite.Row
            return db

        self.original_get_db = queries.get_db
        queries.get_db = fake_get_db

    async def asyncTearDown(self):
        queries.get_db = self.original_get_db
        self.db_path.unlink(missing_ok=True)

    async def test_stats_and_delete_share_available_population(self):
        stats_before = await queries.get_stats(guild_id="123")
        self.assertEqual(stats_before["total"], 3)
        self.assertEqual(stats_before["available"], 2)

        requested_name = "\u200b" + unicodedata.normalize("NFD", "Truyện A") + "\u200b"
        result = await queries.delete_available_deadlines_admin(
            [(requested_name, "011")],
            guild_id="123",
        )

        self.assertEqual(len(result["success"]), 1)
        self.assertEqual(result["failed"], [])

        stats_after = await queries.get_stats(guild_id="123")
        self.assertEqual(stats_after["total"], 2)
        self.assertEqual(stats_after["available"], 1)

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT status FROM deadlines WHERE id = 1") as cursor:
                row = await cursor.fetchone()
            self.assertIsNone(row)

    async def test_delete_reports_not_available_reason(self):
        result = await queries.delete_available_deadlines_admin(
            [("Truyện A", 11)],
            guild_id="123",
            role_type="clean",
        )

        self.assertEqual(result["success"], [])
        self.assertEqual(result["failed"], [("Truyện A", 11)])
        self.assertEqual(result["diagnostics"][0]["reason"], "role_not_match")

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE deadlines SET status = 'assigned' WHERE id = 1")
            await db.commit()

        result = await queries.delete_available_deadlines_admin(
            [("Truyện A", 11)],
            guild_id="123",
        )
        self.assertEqual(result["success"], [])
        self.assertEqual(result["diagnostics"][0]["reason"], "not_available")

    async def test_count_available_deadlines_is_scoped_by_role_and_guild(self):
        self.assertEqual(
            await queries.count_available_deadlines("editfull", guild_id="123"),
            2,
        )
        self.assertEqual(
            await queries.count_available_deadlines("clean", guild_id="123"),
            0,
        )

    async def test_active_drive_link_includes_legacy_global_deadlines(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO deadlines
                   (id, guild_id, chapter_name, chapter_number, series_name,
                    role_type, drive_link, assigned_to, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (4, "global", "Chap 13", 13, "Legacy", "editfull", "shared-link", "worker", "assigned"),
            )
            await db.commit()

        self.assertTrue(
            await queries.check_user_active_drive_link(
                "worker",
                "shared-link",
                guild_id="123",
            )
        )


class ChapterNormalizationTests(unittest.TestCase):
    def test_unicode_and_chapter_normalization(self):
        self.assertTrue(series_names_match("\u200bTRUYỆN   A", "Truyện A"))
        self.assertEqual(normalize_series_name(" Truyện\u00a0A "), "truyện a")
        self.assertEqual(normalize_chapter_number("011"), 11)
        self.assertEqual(normalize_chapter_number("NT2"), -2)


class AvailableSelectionTests(unittest.TestCase):
    def test_two_requested_chapters_use_two_random_series(self):
        rows = [
            {"id": 1, "series_name": "A", "chapter_number": 5},
            {"id": 2, "series_name": "A", "chapter_number": 2},
            {"id": 3, "series_name": "B", "chapter_number": 8},
            {"id": 4, "series_name": "B", "chapter_number": 1},
        ]

        with patch("database.queries.random.sample", side_effect=lambda values, count: values[:count]):
            selected = queries.select_available_deadlines(rows, 2)

        self.assertEqual([row["id"] for row in selected], [2, 4])
        self.assertEqual({row["series_name"] for row in selected}, {"A", "B"})

    def test_one_series_uses_smallest_chapters_in_order(self):
        rows = [
            {"id": 1, "series_name": "A", "chapter_number": 10},
            {"id": 2, "series_name": "A", "chapter_number": 3},
            {"id": 3, "series_name": "A", "chapter_number": 7},
        ]

        with patch("database.queries.random.sample", side_effect=lambda values, count: values[:count]):
            selected = queries.select_available_deadlines(rows, 2)

        self.assertEqual([row["chapter_number"] for row in selected], [3, 7])

    def test_same_numeric_chapter_in_two_series_is_allowed(self):
        rows = [
            {"id": 1, "series_name": "A", "chapter_number": 1},
            {"id": 2, "series_name": "B", "chapter_number": 1},
        ]

        with patch("database.queries.random.sample", side_effect=lambda values, count: values[:count]):
            selected = queries.select_available_deadlines(rows, 2)

        self.assertEqual({row["id"] for row in selected}, {1, 2})


if __name__ == "__main__":
    unittest.main()
