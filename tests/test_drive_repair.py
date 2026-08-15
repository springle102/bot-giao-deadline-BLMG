import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from utils.integrity_checker import DeadlineIntegrityChecker


class DriveRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_repairs_missing_current_email_once_for_shared_drive(self):
        checker = DeadlineIntegrityChecker(SimpleNamespace())
        link = "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA"
        rows = [
            {
                "id": 10,
                "guild_id": "guild-1",
                "assigned_to": "user-1",
                "drive_link": link,
            },
            {
                "id": 11,
                "guild_id": "guild-1",
                "assigned_to": "user-1",
                "drive_link": f"{link}?usp=sharing",
            },
        ]

        with (
            patch(
                "utils.integrity_checker.get_assigned_deadlines_for_drive_check",
                new=AsyncMock(return_value=rows),
            ),
            patch(
                "utils.integrity_checker.get_user_email",
                new=AsyncMock(return_value="new@example.com"),
            ),
            patch(
                "utils.integrity_checker.check_drive_permission",
                return_value=(False, "Không tìm thấy quyền", None),
            ) as check_permission,
            patch(
                "utils.integrity_checker.grant_drive_permission",
                return_value=(True, "Đã cấp quyền cho email new@example.com"),
            ) as grant_permission,
            patch(
                "utils.integrity_checker.resolve_drive_share_failure",
                new=AsyncMock(),
            ) as resolve_failure,
        ):
            repaired = await checker._repair_missing_drive_access()

        self.assertEqual(repaired, 1)
        check_permission.assert_called_once_with(link, "new@example.com")
        grant_permission.assert_called_once_with(
            link,
            "new@example.com",
            "writer",
            True,
        )
        resolve_failure.assert_awaited_once_with("guild-1", link)

    async def test_does_not_share_when_current_email_already_has_access(self):
        checker = DeadlineIntegrityChecker(SimpleNamespace())
        link = "https://drive.google.com/drive/folders/BBBBBBBBBBBBBBBBBBBBBB"
        rows = [
            {
                "id": 20,
                "guild_id": "guild-1",
                "assigned_to": "user-1",
                "drive_link": link,
            }
        ]

        with (
            patch(
                "utils.integrity_checker.get_assigned_deadlines_for_drive_check",
                new=AsyncMock(return_value=rows),
            ),
            patch(
                "utils.integrity_checker.get_user_email",
                new=AsyncMock(return_value="new@example.com"),
            ),
            patch(
                "utils.integrity_checker.check_drive_permission",
                return_value=(True, "Email có quyền writer", None),
            ),
            patch(
                "utils.integrity_checker.grant_drive_permission",
                return_value=(True, "Không nên được gọi"),
            ) as grant_permission,
        ):
            repaired = await checker._repair_missing_drive_access()

        self.assertEqual(repaired, 0)
        grant_permission.assert_not_called()


if __name__ == "__main__":
    unittest.main()
