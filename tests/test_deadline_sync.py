import asyncio
import tempfile
import unittest
import unicodedata
from datetime import datetime, timedelta
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
                    extension_hours INTEGER NOT NULL DEFAULT 0,
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

    async def test_user_deadlines_include_submitted_but_not_other_users_or_available(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(
                """INSERT INTO deadlines
                   (id, guild_id, chapter_name, chapter_number, series_name,
                    role_type, assigned_to, status, deadline_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (4, "123", "Chap 20", 20, "Mine", "editfull", "worker", "assigned", "2099-01-01 00:00:00"),
                    (5, "123", "Chap 21", 21, "Mine", "editfull", "worker", "submitted", "2099-01-02 00:00:00"),
                    (6, "123", "Chap 22", 22, "Other", "editfull", "other", "submitted", "2099-01-03 00:00:00"),
                    (7, "123", "Chap 23", 23, "Available", "editfull", None, "available", None),
                ],
            )
            await db.commit()

        rows = await queries.get_user_deadlines("worker", guild_id="123")
        self.assertEqual([row["id"] for row in rows], [4, 5])
        self.assertEqual([row["status"] for row in rows], ["assigned", "submitted"])

    async def test_active_drive_link_matches_url_variants_by_drive_id(self):
        drive_id = "AAAAAAAAAAAAAAAAAAAAAA"
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO deadlines
                   (id, guild_id, chapter_name, chapter_number, series_name,
                    role_type, drive_link, assigned_to, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    4,
                    "global",
                    "Chap 13",
                    13,
                    "Legacy",
                    "editfull",
                    f"https://drive.google.com/drive/folders/{drive_id}?usp=sharing",
                    "worker",
                    "assigned",
                ),
            )
            await db.commit()

        self.assertTrue(
            await queries.check_user_active_drive_link(
                "worker",
                f"https://drive.google.com/drive/folders/{drive_id}",
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


class DriveShareFailureSelectionTests(unittest.IsolatedAsyncioTestCase):
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
                    chapter_number INTEGER NOT NULL,
                    series_name TEXT NOT NULL,
                    role_type TEXT NOT NULL,
                    drive_link TEXT,
                    status TEXT
                );
                CREATE TABLE drive_share_failures (
                    guild_id TEXT NOT NULL,
                    drive_key TEXT NOT NULL,
                    drive_link TEXT NOT NULL,
                    failure_count INTEGER NOT NULL DEFAULT 1,
                    last_error TEXT,
                    last_failed_at TEXT,
                    blocked_until TEXT,
                    PRIMARY KEY (guild_id, drive_key)
                );
                INSERT INTO deadlines
                    (id, guild_id, chapter_name, chapter_number, series_name,
                     role_type, drive_link, status)
                VALUES
                    (1, 'guild-1', 'Chap 1', 1, 'A', 'editfull',
                     'https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA?usp=sharing', 'available'),
                    (2, 'guild-1', 'Chap 2', 2, 'B', 'editfull',
                     'https://drive.google.com/drive/folders/BBBBBBBBBBBBBBBBBBBBBB', 'available');
                """
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

    async def test_failed_drive_id_is_excluded_from_selection_and_count(self):
        await queries.record_drive_share_failure(
            "guild-1",
            "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA",
            "Google API 400",
        )

        selected = await queries.get_available_deadlines(
            "editfull", 1, guild_id="guild-1"
        )

        self.assertEqual([row["id"] for row in selected], [2])
        self.assertEqual(
            await queries.count_available_deadlines("editfull", guild_id="guild-1"),
            1,
        )

        failures = await queries.get_drive_share_failures(guild_id="guild-1")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["drive_link"].split("?")[0][-22:], "AAAAAAAAAAAAAAAAAAAAAA")
        self.assertEqual(failures[0]["is_active"], 1)
        active_failures = await queries.get_active_drive_share_failures()
        self.assertEqual(len(active_failures), 1)
        self.assertEqual(active_failures[0]["drive_key"], "id:AAAAAAAAAAAAAAAAAAAAAA")
        scoped_active_failures = await queries.get_active_drive_share_failures(
            guild_id="guild-1"
        )
        self.assertEqual(len(scoped_active_failures), 1)

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT failure_count, drive_key, last_failed_at, blocked_until "
                "FROM drive_share_failures"
            ) as cursor:
                row = await cursor.fetchone()
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], "id:AAAAAAAAAAAAAAAAAAAAAA")
        last_failed_at = datetime.strptime(row[2], "%Y-%m-%d %H:%M:%S")
        blocked_until = datetime.strptime(row[3], "%Y-%m-%d %H:%M:%S")
        self.assertGreaterEqual(blocked_until - last_failed_at, timedelta(hours=4))
        self.assertLess(blocked_until - last_failed_at, timedelta(hours=4, minutes=1))

        # Once the recorded cooldown has elapsed, the affected chapter is
        # selectable again for members.
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE drive_share_failures SET last_failed_at = ?, blocked_until = ?",
                ("2000-01-01 00:00:00", "2000-01-01 04:00:00"),
            )
            await db.commit()

        selected_after_expiry = await queries.get_available_deadlines(
            "editfull", 2, guild_id="guild-1"
        )
        self.assertEqual({row["id"] for row in selected_after_expiry}, {1, 2})
        self.assertEqual(
            await queries.count_available_deadlines("editfull", guild_id="guild-1"),
            2,
        )

    async def test_legacy_24_hour_block_uses_current_four_hour_policy(self):
        failed_at = queries.get_now() - timedelta(hours=5)
        legacy_blocked_until = failed_at + timedelta(hours=24)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO drive_share_failures
                   (guild_id, drive_key, drive_link, failure_count,
                    last_error, last_failed_at, blocked_until)
                   VALUES (?, ?, ?, 1, ?, ?, ?)""",
                (
                    "guild-1",
                    "id:BBBBBBBBBBBBBBBBBBBBBB",
                    "https://drive.google.com/drive/folders/BBBBBBBBBBBBBBBBBBBBBB",
                    "Legacy error",
                    failed_at.strftime("%Y-%m-%d %H:%M:%S"),
                    legacy_blocked_until.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            await db.commit()

        selected = await queries.get_available_deadlines(
            "editfull", 2, guild_id="guild-1"
        )
        self.assertEqual({row["id"] for row in selected}, {1, 2})

        failures = await queries.get_drive_share_failures(guild_id="guild-1")
        legacy_failure = next(
            failure for failure in failures
            if failure["drive_key"] == "id:BBBBBBBBBBBBBBBBBBBBBB"
        )
        self.assertEqual(legacy_failure["is_active"], 0)

    async def test_successful_share_resolves_stale_drive_failure(self):
        drive_link = "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA"
        await queries.record_drive_share_failure(
            "guild-1",
            drive_link,
            "Recipient-specific share error",
        )

        self.assertTrue(
            await queries.resolve_drive_share_failure(
                "guild-1",
                f"{drive_link}?usp=sharing",
            )
        )
        self.assertEqual(
            await queries.get_drive_share_failures(guild_id="guild-1"),
            [],
        )
        selected = await queries.get_available_deadlines(
            "editfull", 2, guild_id="guild-1"
        )
        self.assertEqual({row["id"] for row in selected}, {1, 2})


if __name__ == "__main__":
    unittest.main()
