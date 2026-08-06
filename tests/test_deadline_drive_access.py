import unittest
from unittest.mock import AsyncMock, patch

from cogs.nop_deadline import _revoke_drive_access_for_completed_deadlines


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


if __name__ == "__main__":
    unittest.main()
