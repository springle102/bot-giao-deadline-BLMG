import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiosqlite

from cogs.xin_deadline import ConfirmDeadlineView
from database import queries
from utils.integrity_checker import DeadlineIntegrityChecker


class ExtensionLimitTests(unittest.IsolatedAsyncioTestCase):
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
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await db.executemany(
                """
                INSERT INTO deadlines
                    (id, guild_id, chapter_name, chapter_number, series_name,
                     role_type, batch_id, extension_hours, assigned_to,
                     deadline_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (1, "guild-1", "Chap 1", 1, "Series", "editfull", None, 0, "user-1", "2099-01-01 00:00:00", "assigned"),
                    (2, "guild-1", "Chap 2", 2, "Series", "editfull", "batch-1", 0, "user-1", "2099-01-01 00:00:00", "assigned"),
                    (3, "guild-1", "Chap 3", 3, "Series", "editfull", "batch-1", 0, "user-1", "2099-01-01 00:00:00", "assigned"),
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

    async def test_second_extension_cannot_exceed_twelve_hours(self):
        first = await queries.extend_deadline(
            1,
            "2099-01-01 12:00:00",
            "user-1",
            guild_id="guild-1",
            hours_extended=12,
        )
        second = await queries.extend_deadline(
            1,
            "2099-01-01 13:00:00",
            "user-1",
            guild_id="guild-1",
            hours_extended=1,
        )

        self.assertTrue(first["success"])
        self.assertFalse(second["success"])
        self.assertEqual(second["reason"], "extension_limit")
        self.assertEqual(second["current_hours"], 12)
        self.assertEqual(second["remaining_hours"], 0)

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT deadline_at, extension_hours FROM deadlines WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
            self.assertEqual(row[0], "2099-01-01 12:00:00")
            self.assertEqual(row[1], 12)

    async def test_batch_uses_one_shared_extension_budget(self):
        first = await queries.extend_deadline(
            2,
            "2099-01-01 06:00:00",
            "user-1",
            guild_id="guild-1",
            hours_extended=6,
            batch_id="batch-1",
        )
        rejected = await queries.extend_deadline(
            2,
            "2099-01-01 13:00:00",
            "user-1",
            guild_id="guild-1",
            hours_extended=7,
            batch_id="batch-1",
        )

        self.assertTrue(first["success"])
        self.assertFalse(rejected["success"])
        self.assertEqual(rejected["current_hours"], 6)

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT extension_hours, deadline_at FROM deadlines WHERE batch_id = 'batch-1' ORDER BY id"
            ) as cursor:
                rows = await cursor.fetchall()
            self.assertEqual([row[0] for row in rows], [6, 6])
            self.assertEqual([row[1] for row in rows], ["2099-01-01 06:00:00"] * 2)

    async def test_assignment_rollback_returns_rows_to_available(self):
        rolled_back = await queries.rollback_deadline_assignment(
            [1],
            "user-1",
            guild_id="guild-1",
            reason="assignment_failed_drive_share",
        )

        self.assertEqual([row["id"] for row in rolled_back], [1])
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT status, assigned_to, deadline_at, extension_hours FROM deadlines WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
            self.assertEqual(tuple(row), ("available", None, None, 0))

            async with db.execute(
                "SELECT action FROM assignment_log WHERE deadline_id = 1 ORDER BY id DESC LIMIT 1"
            ) as cursor:
                log_row = await cursor.fetchone()
            self.assertEqual(log_row[0], "assignment_failed_drive_share")


class ExtensionRepairTests(unittest.IsolatedAsyncioTestCase):
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
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await db.executemany(
                """
                INSERT INTO deadlines
                    (id, guild_id, chapter_name, chapter_number, series_name,
                     role_type, batch_id, extension_hours, assigned_to,
                     assigned_username, deadline_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (1, "guild-1", "Chap 1", 1, "Series", "editfull", None, 24,
                     "user-1", "Worker", "2099-01-02 12:00:00", "assigned"),
                    (2, "guild-1", "Chap 2", 2, "Series", "editfull", "batch-1", 24,
                     "user-2", "Worker", "2099-01-03 12:00:00", "assigned"),
                    (3, "guild-1", "Chap 3", 3, "Series", "editfull", "batch-1", 12,
                     "user-2", "Worker", "2099-01-03 12:00:00", "assigned"),
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

    async def test_single_deadline_is_capped_and_deadline_is_moved_back(self):
        repairs = await queries.repair_overextended_deadlines()

        repair = next(item for item in repairs if item["deadline_ids"] == [1])
        self.assertTrue(repair["repaired"])
        self.assertEqual(repair["previous_hours"], 24)
        self.assertEqual(repair["excess_hours"], 12)

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT deadline_at, extension_hours FROM deadlines WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
            self.assertEqual(tuple(row), ("2099-01-02 00:00:00", 12))

            async with db.execute(
                "SELECT action FROM assignment_log WHERE deadline_id = 1"
            ) as cursor:
                log_row = await cursor.fetchone()
            self.assertEqual(log_row[0], "extension_repair_removed_12h_capped_12h")

        self.assertEqual(await queries.repair_overextended_deadlines(), [])

    async def test_batch_is_repaired_as_one_shared_extension_budget(self):
        repairs = await queries.repair_overextended_deadlines()

        repair = next(item for item in repairs if item.get("batch_id") == "batch-1")
        self.assertEqual(repair["deadline_ids"], [2, 3])
        self.assertEqual(repair["excess_hours"], 12)

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """SELECT deadline_at, extension_hours FROM deadlines
                   WHERE batch_id = 'batch-1' ORDER BY id"""
            ) as cursor:
                rows = await cursor.fetchall()
            self.assertEqual(
                [tuple(row) for row in rows],
                [("2099-01-03 00:00:00", 12), ("2099-01-03 00:00:00", 12)],
            )


class ExtensionNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_extension_repair_is_announced_in_deadline_channel(self):
        channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(id=123, name="Test Guild")
        bot = SimpleNamespace(
            get_guild=Mock(return_value=guild),
            get_channel=Mock(return_value=None),
        )
        checker = DeadlineIntegrityChecker(bot)
        repair = {
            "repaired": True,
            "guild_id": "123",
            "user_id": "456",
            "deadline_ids": [10, 11],
            "batch_id": "batch-1",
            "previous_hours": 24,
            "current_hours": 12,
            "excess_hours": 12,
            "new_deadline_at": "2099-01-01 00:00:00",
        }

        with (
            patch(
                "utils.integrity_checker._find_deadline_channel",
                new=AsyncMock(return_value=channel),
            ),
            patch(
                "utils.integrity_checker.notify_all_admins",
                new=AsyncMock(),
            ) as notify_admins,
        ):
            await checker._notify_extension_repairs([repair])

        channel.send.assert_awaited_once()
        embed = channel.send.await_args.kwargs["embed"]
        self.assertIn("<@456>", embed.fields[0].value)
        self.assertIn("12h", embed.fields[0].value)
        notify_admins.assert_awaited_once()


class DriveBlacklistRecheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_recheck_clears_recovered_drive_once_for_multiple_guild_rows(self):
        checker = DeadlineIntegrityChecker(SimpleNamespace())
        rows = [
            {
                "guild_id": "guild-1",
                "drive_key": "id:AAAAAAAAAAAAAAAAAAAAAA",
                "drive_link": "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA",
            },
            {
                "guild_id": "guild-2",
                "drive_key": "id:AAAAAAAAAAAAAAAAAAAAAA",
                "drive_link": "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA?usp=sharing",
            },
        ]

        with (
            patch(
                "utils.integrity_checker.get_active_drive_share_failures",
                new=AsyncMock(return_value=rows),
            ),
            patch(
                "utils.integrity_checker.check_drive_sharing_capability",
                return_value=(True, "Bot vẫn có quyền chia sẻ link Drive.", None),
            ) as check_capability,
            patch(
                "utils.integrity_checker.resolve_drive_share_failure",
                new=AsyncMock(return_value=True),
            ) as resolve_failure,
        ):
            resolved = await checker._recheck_blocked_drive_links()

        self.assertEqual(resolved, 2)
        check_capability.assert_called_once()
        self.assertEqual(resolve_failure.await_count, 2)

    async def test_recheck_keeps_link_when_capability_is_still_false(self):
        checker = DeadlineIntegrityChecker(SimpleNamespace())
        rows = [
            {
                "guild_id": "guild-1",
                "drive_key": "id:AAAAAAAAAAAAAAAAAAAAAA",
                "drive_link": "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA",
            }
        ]

        with (
            patch(
                "utils.integrity_checker.get_active_drive_share_failures",
                new=AsyncMock(return_value=rows),
            ),
            patch(
                "utils.integrity_checker.check_drive_sharing_capability",
                return_value=(False, "Bot hiện không có khả năng chia sẻ link Drive này.", 403),
            ),
            patch(
                "utils.integrity_checker.resolve_drive_share_failure",
                new=AsyncMock(),
            ) as resolve_failure,
        ):
            resolved = await checker._recheck_blocked_drive_links()

        self.assertEqual(resolved, 0)
        resolve_failure.assert_not_awaited()


class DriveRollbackTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _interaction():
        response = SimpleNamespace(
            defer=AsyncMock(),
            send_message=AsyncMock(),
        )
        return SimpleNamespace(
            response=response,
            edit_original_response=AsyncMock(),
            user=SimpleNamespace(id=99, mention="<@99>", display_name="Worker"),
        )

    def _view(self):
        return ConfirmDeadlineView(
            deadline_ids=[10, 11],
            user_id=99,
            username="Worker",
            role_type="editfull",
            chap_count=2,
            chapters=[
                {"id": 10, "drive_link": "drive-link-1"},
                {"id": 11, "drive_link": "drive-link-2"},
            ],
            deadline_at=datetime(2099, 1, 1, 12, 0, 0),
            total_days=1,
            original_interaction=self._interaction(),
            guild_id="guild-1",
        )

    async def test_failed_second_drive_share_rolls_back_first_share_and_deadlines(self):
        interaction = self._interaction()
        view = self._view()
        grant = Mock(
            side_effect=[
                (True, "Đã cấp quyền cho email worker@example.com"),
                (False, "Google API 400: Bad Request"),
            ]
        )
        revoke = Mock(return_value=(True, "Đã thu hồi quyền"))
        rollback = AsyncMock(return_value=[{"id": 10}, {"id": 11}])

        with (
            patch("cogs.xin_deadline.get_user_email", new=AsyncMock(return_value="worker@example.com")),
            patch("cogs.xin_deadline.grant_drive_permission", new=grant),
            patch("cogs.xin_deadline.revoke_drive_permission", new=revoke),
            patch("cogs.xin_deadline.rollback_deadline_assignment", new=rollback),
            patch("cogs.xin_deadline.record_drive_share_failure", new=AsyncMock()) as record_failure,
            patch("cogs.xin_deadline.confirm_deadlines", new=AsyncMock()) as confirm,
        ):
            await ConfirmDeadlineView.confirm_btn(view, interaction, view.children[0])

        confirm.assert_not_awaited()
        rollback.assert_awaited_once_with(
            [10, 11],
            "99",
            guild_id="guild-1",
            reason="assignment_failed_drive_share",
        )
        revoke.assert_called_once_with("drive-link-1", "worker@example.com")
        record_failure.assert_awaited_once_with(
            "guild-1", "drive-link-2", "Yêu cầu chia sẻ Google Drive không hợp lệ."
        )
        interaction.edit_original_response.assert_awaited_once()
        error_embed = interaction.edit_original_response.await_args.kwargs["embed"]
        self.assertIn("Yêu cầu chia sẻ Google Drive không hợp lệ.", error_embed.description)
        self.assertNotIn("Google API 400", error_embed.description)

    async def test_all_drive_shares_successfully_confirm_assignment(self):
        interaction = self._interaction()
        view = self._view()
        grant = Mock(
            side_effect=[
                (True, "Đã cấp quyền cho email worker@example.com"),
                (True, "Đã cấp quyền cho email worker@example.com"),
            ]
        )
        confirm = AsyncMock(return_value=True)

        with (
            patch("cogs.xin_deadline.get_user_email", new=AsyncMock(return_value="worker@example.com")),
            patch("cogs.xin_deadline.grant_drive_permission", new=grant),
            patch("cogs.xin_deadline.confirm_deadlines", new=confirm),
            patch("cogs.xin_deadline.rollback_deadline_assignment", new=AsyncMock()) as rollback,
            patch("cogs.xin_deadline.revoke_drive_permission", new=Mock()) as revoke,
        ):
            await ConfirmDeadlineView.confirm_btn(view, interaction, view.children[0])

        confirm.assert_awaited_once()
        rollback.assert_not_awaited()
        revoke.assert_not_called()
        interaction.edit_original_response.assert_awaited_once()
        success_embed = interaction.edit_original_response.await_args.kwargs["embed"]
        self.assertTrue(success_embed.title.startswith("✅ Đã giao Deadline"))
        self.assertTrue(any("Link Drive" in field.value for field in success_embed.fields))
        self.assertTrue(any("Cấp Quyền Google Drive" in field.name for field in success_embed.fields))

    async def test_transient_sharing_quota_failure_does_not_blacklist_link(self):
        interaction = self._interaction()
        view = self._view()
        grant = Mock(return_value=(
            False,
            "Lỗi HTTP 403 [sharingRateLimitExceeded]: Rate Limit Exceeded",
        ))
        rollback = AsyncMock(return_value=[{"id": 10}, {"id": 11}])

        with (
            patch("cogs.xin_deadline.get_user_email", new=AsyncMock(return_value="worker@example.com")),
            patch("cogs.xin_deadline.grant_drive_permission", new=grant),
            patch("cogs.xin_deadline.rollback_deadline_assignment", new=rollback),
            patch("cogs.xin_deadline.record_drive_share_failure", new=AsyncMock()) as record_failure,
            patch("cogs.xin_deadline.revoke_drive_permission", new=Mock()),
        ):
            await ConfirmDeadlineView.confirm_btn(view, interaction, view.children[0])

        record_failure.assert_not_awaited()
        rollback.assert_awaited_once()

    async def test_localized_transient_failure_does_not_blacklist_link(self):
        interaction = self._interaction()
        view = self._view()
        grant = Mock(
            return_value=(
                False,
                "Google Drive \u0111ang gi\u1edbi h\u1ea1n ho\u1eb7c t\u1ea1m th\u1eddi g\u1eb7p l\u1ed7i. "
                "Vui l\u00f2ng th\u1eed l\u1ea1i sau.",
            )
        )
        rollback = AsyncMock(return_value=[{"id": 10}, {"id": 11}])

        with (
            patch("cogs.xin_deadline.get_user_email", new=AsyncMock(return_value="worker@example.com")),
            patch("cogs.xin_deadline.grant_drive_permission", new=grant),
            patch("cogs.xin_deadline.rollback_deadline_assignment", new=rollback),
            patch("cogs.xin_deadline.record_drive_share_failure", new=AsyncMock()) as record_failure,
            patch("cogs.xin_deadline.revoke_drive_permission", new=Mock()),
        ):
            await ConfirmDeadlineView.confirm_btn(view, interaction, view.children[0])

        record_failure.assert_not_awaited()
        rollback.assert_awaited_once()
        error_embed = interaction.edit_original_response.await_args.kwargs["embed"]
        self.assertIn("kh\u00f4ng b\u1ecb \u0111\u00e1nh d\u1ea5u", error_embed.description)


if __name__ == "__main__":
    unittest.main()
