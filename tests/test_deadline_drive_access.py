import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from cogs.nop_deadline import _revoke_drive_access_for_completed_deadlines
from cogs.xin_deadline import _add_drive_status_field
from utils.embed_builder import create_single_thongke_panel
from utils.google_drive import grant_drive_permission


class DeadlineDriveAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_revoke_completed_links_but_keep_links_with_active_deadlines(self):
        async def active_link(_user_id, link, guild_id):
            self.assertEqual(guild_id, "guild-1")
            return link == "https://drive.google.com/active-link"

        completed_deadlines = [
            {"drive_link": "https://drive.google.com/completed-link"},
            {"drive_link": "https://drive.google.com/active-link"},
        ]

        with (
            patch(
                "database.queries.get_user_email",
                new=AsyncMock(return_value="worker@example.com"),
            ),
            patch(
                "database.queries.check_user_active_drive_link",
                new=AsyncMock(side_effect=active_link),
            ),
            patch(
                "utils.google_drive.revoke_drive_permission",
                return_value=(True, "Đã thu hồi quyền"),
            ) as revoke_permission,
        ):
            status_lines = await _revoke_drive_access_for_completed_deadlines(
                "user-1",
                completed_deadlines,
                "guild-1",
            )

        self.assertEqual(revoke_permission.call_count, 1)
        self.assertTrue(any("Đã thu hồi quyền" in line for line in status_lines))
        self.assertTrue(any("Giữ quyền Drive" in line for line in status_lines))

    def test_drive_status_field_stays_within_discord_limit(self):
        embed = discord.Embed()

        _add_drive_status_field(embed, ["x" * 1100])

        self.assertLessEqual(len(embed.fields[0].value), 1024)
        self.assertIn("đã được rút gọn", embed.fields[0].value)

    def test_grant_verifies_access_when_google_returns_error_after_share(self):
        class Request:
            def __init__(self, error):
                self.error = error

            def execute(self):
                if self.error:
                    raise RuntimeError(self.error)
                return {"id": "permission-id"}

        errors = [
            "invalidSharingRequest: Bad Request",
            "request timed out after permission was applied",
        ]
        permissions = Mock()
        permissions.create.side_effect = lambda **_: Request(errors.pop(0))
        service = Mock()
        service.permissions.return_value = permissions

        with (
            patch(
                "utils.google_drive.get_drive_service",
                return_value=(service, None),
            ),
            patch(
                "utils.google_drive.check_drive_permission",
                return_value=(True, "Email có quyền writer", None),
            ) as verify_permission,
        ):
            success, message = grant_drive_permission(
                "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA",
                "worker@example.com",
            )

        self.assertTrue(success)
        self.assertIn("Đã xác minh quyền", message)
        verify_permission.assert_called_once()

    def test_grant_falls_back_without_notification_on_sharing_quota(self):
        class Request:
            def __init__(self, error=None):
                self.error = error

            def execute(self):
                if self.error:
                    raise self.error
                return {"id": "permission-id"}

        quota_error = RuntimeError("sharingRateLimitExceeded: Rate Limit Exceeded")
        quota_error.resp = SimpleNamespace(status=403)
        quota_error.content = json.dumps(
            {
                "error": {
                    "errors": [
                        {
                            "reason": "sharingRateLimitExceeded",
                            "message": "Rate Limit Exceeded",
                        }
                    ]
                }
            }
        ).encode()

        permissions = Mock()
        permissions.create.side_effect = [Request(quota_error), Request()]
        service = Mock()
        service.permissions.return_value = permissions

        with patch(
            "utils.google_drive.get_drive_service",
            return_value=(service, None),
        ):
            success, message = grant_drive_permission(
                "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA",
                "worker@example.com",
            )

        self.assertTrue(success)
        self.assertIn("Không gửi mail thông báo", message)
        first_kwargs = permissions.create.call_args_list[0].kwargs
        second_kwargs = permissions.create.call_args_list[1].kwargs
        self.assertTrue(first_kwargs["sendNotificationEmail"])
        self.assertFalse(second_kwargs["sendNotificationEmail"])
        self.assertNotIn("supportsTeamDrives", first_kwargs)

    def test_thongke_panel_lists_drive_share_failures(self):
        embed = create_single_thongke_panel(
            stats={},
            all_deadlines=[],
            overdue_info={},
            drive_failures=[
                {
                    "drive_link": "https://drive.google.com/drive/folders/broken-link",
                    "failure_count": 2,
                    "last_failed_at": "2026-08-07 10:30:00",
                    "last_error": "Google API 400: Bad Request",
                    "is_active": 1,
                }
            ],
        )

        failure_field = next(
            field for field in embed.fields if "link Google Drive bị lỗi" in field.name
        )
        self.assertIn("broken-link", failure_field.value)
        self.assertIn("Lỗi **2 lần**", failure_field.value)
        self.assertIn("Đang tạm tránh", failure_field.value)


if __name__ == "__main__":
    unittest.main()
