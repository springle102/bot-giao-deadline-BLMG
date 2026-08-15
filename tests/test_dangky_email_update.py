import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from cogs.dangky import DangKy


class DangKyEmailUpdateTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _interaction():
        return SimpleNamespace(
            response=SimpleNamespace(
                defer=AsyncMock(),
                send_message=AsyncMock(),
            ),
            followup=SimpleNamespace(send=AsyncMock()),
            user=SimpleNamespace(id=99, display_name="Worker"),
            guild_id="guild-1",
        )

    @staticmethod
    def _deadlines():
        return [
            {"id": 10, "status": "assigned", "drive_link": "drive-link-1"},
            # Same Drive item must only be shared/revoked once.
            {"id": 11, "status": "assigned", "drive_link": "drive-link-1"},
            {"id": 12, "status": "assigned", "drive_link": "drive-link-2"},
        ]

    async def test_email_update_reshares_active_deadlines_and_saves_new_email(self):
        interaction = self._interaction()
        grant = Mock(
            side_effect=[
                (True, "Đã cấp quyền cho email new@example.com"),
                (True, "Đã cấp quyền cho email new@example.com"),
            ]
        )
        revoke = Mock(
            side_effect=[
                (True, "Đã thu hồi quyền Drive của email old@example.com"),
                (True, "Đã thu hồi quyền Drive của email old@example.com"),
            ]
        )

        with (
            patch("cogs.dangky.get_user_email", new=AsyncMock(return_value="old@example.com")),
            patch("cogs.dangky.get_assigned_deadlines", new=AsyncMock(return_value=self._deadlines())),
            patch("cogs.dangky.grant_drive_permission", new=grant),
            patch("cogs.dangky.revoke_drive_permission", new=revoke),
            patch("cogs.dangky.save_user_email", new=AsyncMock()) as save_email,
        ):
            await DangKy.dangky.callback(
                DangKy(Mock()),
                interaction,
                "new@example.com",
            )

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        save_email.assert_awaited_once_with(
            "99",
            "Worker",
            "new@example.com",
            guild_id="guild-1",
        )
        grant.assert_has_calls(
            [
                call("drive-link-1", "new@example.com", "writer", True),
                call("drive-link-2", "new@example.com", "writer", True),
            ]
        )
        revoke.assert_has_calls(
            [
                call("drive-link-1", "old@example.com"),
                call("drive-link-2", "old@example.com"),
            ]
        )
        self.assertEqual(grant.call_count, 2)
        self.assertEqual(revoke.call_count, 2)
        success_embed = interaction.followup.send.await_args.kwargs["embed"]
        self.assertIn("share lại quyền Drive thành công", success_embed.description)
        self.assertIn("new@example.com", success_embed.description)

    async def test_failed_new_share_keeps_old_email_and_old_permission(self):
        interaction = self._interaction()
        grant = Mock(
            side_effect=[
                (True, "Đã cấp quyền cho email new@example.com"),
                (False, "Google Drive không thể chia sẻ email này"),
            ]
        )
        revoke = Mock(return_value=(True, "Đã dọn quyền mới"))

        with (
            patch("cogs.dangky.get_user_email", new=AsyncMock(return_value="old@example.com")),
            patch("cogs.dangky.get_assigned_deadlines", new=AsyncMock(return_value=self._deadlines())),
            patch("cogs.dangky.grant_drive_permission", new=grant),
            patch("cogs.dangky.revoke_drive_permission", new=revoke),
            patch("cogs.dangky.save_user_email", new=AsyncMock()) as save_email,
        ):
            await DangKy.dangky.callback(
                DangKy(Mock()),
                interaction,
                "new@example.com",
            )

        save_email.assert_not_awaited()
        # Only the permission created before the failure is compensated.
        revoke.assert_called_once_with("drive-link-1", "new@example.com")
        error_embed = interaction.followup.send.await_args.kwargs["embed"]
        self.assertIn("Email cũ vẫn được giữ", error_embed.description)
        self.assertIn("old@example.com", error_embed.description)


if __name__ == "__main__":
    unittest.main()
