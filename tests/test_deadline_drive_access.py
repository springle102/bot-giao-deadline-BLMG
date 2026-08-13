import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from cogs.nop_deadline import _revoke_drive_access_for_completed_deadlines
from cogs.thongke import (
    ThongKe,
    ThongKeFilterView,
    _build_stats_from_rows,
    _filter_overdue_info,
)
from cogs.xin_deadline import _add_drive_status_field
from utils.scheduler import DeadlineScheduler
from utils.embed_builder import (
    create_deadline_list,
    create_deadline_pages,
    create_single_thongke_panel,
    create_thongke_panels,
)
from utils.google_drive import (
    check_drive_permission,
    friendly_drive_error,
    grant_drive_permission,
    is_transient_drive_error,
    revoke_drive_permission,
    should_block_drive_link,
)


class DeadlineDriveAccessTests(unittest.IsolatedAsyncioTestCase):
    def test_personal_deadline_panel_shows_each_status(self):
        embed = create_deadline_list(
            [
                {
                    "series_name": "Series A",
                    "chapter_name": "Chap 1",
                    "role_type": "editfull",
                    "status": "assigned",
                    "deadline_at": "2099-01-01 00:00:00",
                },
                {
                    "series_name": "Series A",
                    "chapter_name": "Chap 2",
                    "role_type": "editfull",
                    "status": "submitted",
                    "deadline_at": "2099-01-02 00:00:00",
                },
                {
                    "series_name": "Series B",
                    "chapter_name": "Chap 3",
                    "role_type": "clean",
                    "status": "assigned",
                    "deadline_at": "2000-01-01 00:00:00",
                },
            ],
            SimpleNamespace(display_name="Worker"),
        )

        self.assertIn("Tổng:** 3 chap", embed.description)
        self.assertIn("Đang làm:** 1", embed.description)
        self.assertIn("Đã nộp:** 1", embed.description)
        self.assertIn("Quá hạn:** 1", embed.description)
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("Chap 1", fields["🟡 Đang làm"])
        self.assertIn("Chap 2", fields["✅ Đã nộp"])
        self.assertIn("Chap 3", fields["🔴 Quá hạn"])

    def test_personal_deadline_panel_keeps_submission_time_without_repeating_status(self):
        pages = create_deadline_pages(
            [
                {
                    "series_name": "Series A",
                    "chapter_name": "Chap 2",
                    "role_type": "editfull",
                    "status": "submitted",
                    "submitted_at": "2026-08-10 12:34:56",
                }
            ],
            SimpleNamespace(display_name="Worker"),
        )

        submitted_value = next(
            field.value
            for field in pages[0].fields
            if field.name == "✅ Đã nộp"
        )
        self.assertIn("Hoàn thành lúc: `10/08/2026 12:34`", submitted_value)
        self.assertNotIn("✅ Đã nộp", submitted_value)

    def test_personal_deadline_pages_keep_all_chapters_and_number_them(self):
        deadlines = [
            {
                "series_name": "Series A",
                "chapter_name": f"Chap {index}",
                "role_type": "editfull",
                "status": "submitted" if index % 2 == 0 else "assigned",
                "deadline_at": "2099-01-01 00:00:00",
                "submitted_at": "2026-08-10 12:34:56",
            }
            for index in range(1, 81)
        ]

        pages = create_deadline_pages(deadlines, SimpleNamespace(display_name="Worker"))
        all_values = "\n".join(
            field.value for page in pages for field in page.fields
        )
        numbered_entries = [
            int(line.split(".", 1)[0])
            for line in all_values.splitlines()
            if ". **" in line and line.split(".", 1)[0].isdigit()
        ]

        self.assertGreater(len(pages), 1)
        self.assertEqual(numbered_entries, list(range(1, 81)))
        for index in range(1, 81):
            self.assertIn(f"Chap {index}", all_values)
        self.assertLessEqual(
            max(len(field.value) for page in pages for field in page.fields),
            1024,
        )
        self.assertTrue(
            all("(tiếp)" not in field.name for page in pages for field in page.fields)
        )
        self.assertTrue(
            all(
                sum(field.name == "✅ Đã nộp" for field in page.fields) <= 1
                for page in pages
            )
        )

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

    async def test_scheduler_revokes_drive_permission_after_overdue_return(self):
        bot = SimpleNamespace(
            fetch_user=AsyncMock(return_value=None),
            get_guild=Mock(return_value=None),
            get_channel=Mock(return_value=None),
        )
        scheduler = DeadlineScheduler(bot)
        overdue = [
            {
                "id": 10,
                "assigned_to": "user-1",
                "assigned_username": "Worker",
                "guild_id": "guild-1",
                "role_type": "editfull",
                "chapter_name": "Chap 10",
                "series_name": "Series",
                "drive_link": "https://drive.google.com/drive/folders/overdue-link",
            }
        ]

        with (
            patch(
                "database.queries.auto_return_overdue_deadlines",
                new=AsyncMock(return_value=overdue),
            ),
            patch(
                "database.queries.get_user_email",
                new=AsyncMock(return_value="worker@example.com"),
            ),
            patch(
                "database.queries.check_user_active_drive_link",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "utils.google_drive.revoke_drive_permission",
                return_value=(True, "Đã thu hồi quyền Drive"),
            ) as revoke_permission,
        ):
            await scheduler._check_overdue_deadlines()

        revoke_permission.assert_called_once_with(
            "https://drive.google.com/drive/folders/overdue-link",
            "worker@example.com",
        )

    def test_revoke_retries_temporary_google_api_error(self):
        class Request:
            def __init__(self, error=None, response=None):
                self.error = error
                self.response = response

            def execute(self):
                if self.error:
                    raise self.error
                return self.response or {}

        quota_error = RuntimeError("sharingRateLimitExceeded")
        quota_error.resp = SimpleNamespace(status=403)
        quota_error.content = json.dumps(
            {
                "error": {
                    "errors": [
                        {"reason": "sharingRateLimitExceeded"}
                    ]
                }
            }
        ).encode()

        permissions = Mock()
        permissions.list.side_effect = [
            Request(error=quota_error),
            Request(
                response={
                    "permissions": [
                        {"id": "permission-id", "emailAddress": "worker@example.com"}
                    ]
                }
            ),
        ]
        permissions.delete.return_value = Request()
        service = Mock()
        service.permissions.return_value = permissions

        with (
            patch("utils.google_drive.get_drive_service", return_value=(service, None)),
            patch("utils.google_drive.time.sleep"),
        ):
            success, message = revoke_drive_permission(
                "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA",
                "worker@example.com",
            )

        self.assertTrue(success)
        self.assertIn("Đã thu hồi quyền Drive", message)
        self.assertEqual(permissions.list.call_count, 2)

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

    def test_localized_transient_message_is_not_treated_as_link_failure(self):
        message = (
            "Google Drive \u0111ang gi\u1edbi h\u1ea1n ho\u1eb7c t\u1ea1m th\u1eddi g\u1eb7p l\u1ed7i. "
            "Vui l\u00f2ng th\u1eed l\u1ea1i sau."
        )

        self.assertTrue(is_transient_drive_error(message))
        self.assertFalse(should_block_drive_link(message))

    def test_recipient_or_policy_error_does_not_blacklist_healthy_link(self):
        messages = (
            "HttpError 403 when requesting permissions.create: cannotInviteNonGoogleUser",
            "HttpError 403 when requesting permissions.create: cannotShareAcrossDomains",
            "HttpError 403 when requesting permissions.create: domainPolicy",
            "HttpError 400 when requesting permissions.create: invalidSharingRequest",
        )

        for message in messages:
            with self.subTest(message=message):
                self.assertFalse(should_block_drive_link(message))

    def test_invalid_sharing_request_is_not_labeled_editor_error_without_proof(self):
        error = RuntimeError(
            "HttpError 400 when requesting permissions.create: "
            "invalidSharingRequest: ACL change not allowed"
        )
        error.resp = SimpleNamespace(status=400)
        error.content = json.dumps(
            {
                "error": {
                    "errors": [
                        {
                            "reason": "invalidSharingRequest",
                            "message": "ACL change not allowed.",
                        }
                    ]
                }
            }
        ).encode()

        message = friendly_drive_error(
            error,
            email="worker@example.com",
            drive_url="https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA",
        )

        self.assertIn("từ chối thay đổi quyền chia sẻ", message)
        self.assertNotIn("không có quyền Editor", message)

    def test_invalid_sharing_request_respects_capability_probe(self):
        from utils.google_drive import _classify_link_failure

        error = RuntimeError(
            "HttpError 400 when requesting permissions.create: "
            "invalidSharingRequest: ACL change not allowed"
        )
        error.resp = SimpleNamespace(status=400)
        error.content = json.dumps(
            {
                "error": {
                    "errors": [
                        {
                            "reason": "invalidSharingRequest",
                            "message": "ACL change not allowed.",
                        }
                    ]
                }
            }
        ).encode()

        class Request:
            def __init__(self, response):
                self.response = response

            def execute(self):
                return self.response

        service = Mock()
        service.files.return_value.get.return_value = Request(
            {"id": "drive-id", "capabilities": {"canShare": True}}
        )

        self.assertFalse(_classify_link_failure(service, "drive-id", error))

        service.files.return_value.get.return_value = Request(
            {"id": "drive-id", "capabilities": {"canShare": False}}
        )
        self.assertTrue(_classify_link_failure(service, "drive-id", error))

    def test_grant_preflight_reports_inaccessible_drive_before_acl_mutation(self):
        class Request:
            def __init__(self, error):
                self.error = error

            def execute(self):
                raise self.error

        not_found = RuntimeError("File not found")
        not_found.resp = SimpleNamespace(status=404)
        not_found.content = json.dumps(
            {
                "error": {
                    "errors": [{"reason": "notFound"}],
                }
            }
        ).encode()

        service = Mock()
        service.files.return_value.get.return_value = Request(not_found)

        with patch(
            "utils.google_drive.get_drive_service",
            return_value=(service, None),
        ):
            success, message = grant_drive_permission(
                "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA",
                "worker@example.com",
            )

        self.assertFalse(success)
        self.assertIn("không truy cập được", message)
        self.assertTrue(getattr(message, "link_blocked", False))
        service.permissions.return_value.create.assert_not_called()

    def test_permission_error_does_not_blacklist_when_bot_can_still_share(self):
        class Request:
            def __init__(self, error=None, response=None):
                self.error = error
                self.response = response or {}

            def execute(self):
                if self.error:
                    raise self.error
                return self.response

        permission_error = RuntimeError(
            "HttpError 403 when requesting permissions.create: insufficientFilePermissions"
        )
        permission_error.resp = SimpleNamespace(status=403)
        permission_error.content = json.dumps(
            {
                "error": {
                    "errors": [
                        {"reason": "insufficientFilePermissions"}
                    ]
                }
            }
        ).encode()

        permissions = Mock()
        permissions.create.side_effect = [
            Request(error=permission_error),
            Request(error=permission_error),
        ]
        service = Mock()
        service.permissions.return_value = permissions
        service.files.return_value.get.return_value = Request(
            response={"id": "drive-id", "capabilities": {"canShare": True}}
        )

        with (
            patch("utils.google_drive.get_drive_service", return_value=(service, None)),
            patch(
                "utils.google_drive.check_drive_permission",
                return_value=(False, "Không tìm thấy quyền", 403),
            ),
        ):
            success, message = grant_drive_permission(
                "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA",
                "worker@example.com",
            )

        self.assertFalse(success)
        self.assertFalse(should_block_drive_link(message))
        service.files.return_value.get.assert_called_once()

    def test_existing_reader_permission_is_upgraded_to_writer(self):
        class Request:
            def __init__(self, response=None, error=None):
                self.response = response or {}
                self.error = error

            def execute(self):
                if self.error:
                    raise self.error
                return self.response

        permissions = Mock()
        permissions.create.return_value = Request(
            error=RuntimeError("permission already exists")
        )
        permissions.list.return_value = Request(
            response={
                "permissions": [
                    {
                        "id": "permission-id",
                        "type": "user",
                        "emailAddress": "worker@example.com",
                        "role": "reader",
                    }
                ]
            }
        )
        permissions.update.return_value = Request(
            response={"id": "permission-id", "role": "writer"}
        )
        service = Mock()
        service.permissions.return_value = permissions

        with (
            patch("utils.google_drive.get_drive_service", return_value=(service, None)),
            patch(
                "utils.google_drive.check_drive_permission",
                return_value=(True, "Email worker@example.com c\u00f3 quy\u1ec1n writer", None),
            ),
        ):
            success, message = grant_drive_permission(
                "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA",
                "worker@example.com",
            )

        self.assertTrue(success)
        self.assertIn("n\u00e2ng quy\u1ec1n", message)
        permissions.update.assert_called_once()
        self.assertEqual(
            permissions.update.call_args.kwargs["permissionId"],
            "permission-id",
        )
        self.assertEqual(
            permissions.update.call_args.kwargs["body"],
            {"role": "writer"},
        )

    def test_grant_polls_after_ambiguous_write_until_permission_is_visible(self):
        permissions = Mock()
        permissions.create.side_effect = RuntimeError(
            "request timed out after permission was applied"
        )
        service = Mock()
        service.permissions.return_value = permissions

        with (
            patch("utils.google_drive.get_drive_service", return_value=(service, None)),
            patch(
                "utils.google_drive.check_drive_permission",
                side_effect=[
                    (False, "Kh\u00f4ng t\u00ecm th\u1ea5y quy\u1ec1n", None),
                    (False, "Kh\u00f4ng t\u00ecm th\u1ea5y quy\u1ec1n", None),
                    (True, "Email worker@example.com c\u00f3 quy\u1ec1n writer", None),
                ],
            ) as verify_permission,
            patch("utils.google_drive.time.sleep"),
        ):
            success, message = grant_drive_permission(
                "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA",
                "worker@example.com",
            )

        self.assertTrue(success)
        self.assertIn("\u0110\u00e3 x\u00e1c minh quy\u1ec1n", message)
        self.assertEqual(verify_permission.call_count, 3)

    def test_check_permission_does_not_accept_reader_for_writer(self):
        class Request:
            def execute(self):
                return {
                    "permissions": [
                        {
                            "type": "user",
                            "emailAddress": "worker@example.com",
                            "role": "reader",
                        }
                    ]
                }

        permissions = Mock()
        permissions.list.return_value = Request()
        service = Mock()
        service.permissions.return_value = permissions

        with patch("utils.google_drive.get_drive_service", return_value=(service, None)):
            success, message, status = check_drive_permission(
                "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA",
                "worker@example.com",
            )

        self.assertFalse(success)
        self.assertIn("reader", message)
        self.assertIsNone(status)

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

    def test_grant_returns_short_message_for_non_google_recipient(self):
        class Request:
            def __init__(self, error):
                self.error = error

            def execute(self):
                raise self.error

        non_google_error = RuntimeError(
            "HttpError 403 when requesting permissions.create: cannotInviteNonGoogleUser"
        )
        non_google_error.resp = SimpleNamespace(status=403)
        non_google_error.content = json.dumps(
            {
                "error": {
                    "errors": [
                        {
                            "reason": "cannotInviteNonGoogleUser",
                            "message": "User does not have a Google Account",
                        }
                    ]
                }
            }
        ).encode()

        permissions = Mock()
        permissions.create.side_effect = [
            Request(non_google_error),
            Request(non_google_error),
        ]
        service = Mock()
        service.permissions.return_value = permissions

        with (
            patch(
                "utils.google_drive.get_drive_service",
                return_value=(service, None),
            ),
            patch(
                "utils.google_drive.check_drive_permission",
                return_value=(False, "Không có quyền", 403),
            ),
        ):
            success, message = grant_drive_permission(
                "https://drive.google.com/drive/folders/AAAAAAAAAAAAAAAAAAAAAA",
                "myyen.contact01@gmail.com",
            )

        self.assertFalse(success)
        self.assertIn("chưa có tài khoản Google", message)
        self.assertIn("myyen.contact01@gmail.com", message)
        self.assertNotIn("HttpError", message)
        self.assertNotIn("googleapis.com", message)

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
            field
            for field in embed.fields
            if "link Google Drive đang bị tạm tránh" in field.name
        )
        self.assertIn("broken-link", failure_field.value)
        self.assertIn("Lỗi **2 lần**", failure_field.value)
        self.assertIn("Đang tạm tránh", failure_field.value)


    def test_thongke_pages_keep_all_series_beyond_embed_limits(self):
        deadlines = [
            {
                "series_name": f"Series {index:02d}",
                "role_type": "editfull",
                "status": "available",
                "chapter_number": 1,
            }
            for index in range(40)
        ]
        pages = create_thongke_panels(
            stats={
                "total": 40,
                "available": 40,
                "assigned": 0,
                "submitted": 0,
                "overdue": 0,
                "per_role": {},
            },
            all_deadlines=deadlines,
        )

        rendered = "\n".join(
            field.name + field.value
            for page in pages
            for field in page.fields
        )
        self.assertGreater(len(pages), 1)
        for index in range(40):
            self.assertIn(f"Series {index:02d}", rendered)
        self.assertTrue(all(len(page.fields) <= 25 for page in pages))

        def embed_size(page):
            size = len(page.title or "") + len(page.description or "")
            size += len(page.footer.text or "") if page.footer else 0
            return size + sum(len(field.name) + len(field.value) for field in page.fields)

        self.assertTrue(all(embed_size(page) <= 6000 for page in pages))

    def test_thongke_filtered_summary_uses_only_selected_rows(self):
        stats = _build_stats_from_rows(
            [
                {"role_type": "editfull", "status": "assigned", "deadline_at": "2099-01-01 00:00:00"},
                {"role_type": "editfull", "status": "submitted", "deadline_at": None},
                {"role_type": "clean", "status": "available", "deadline_at": None},
            ]
        )

        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["assigned"], 1)
        self.assertEqual(stats["submitted"], 1)
        self.assertEqual(stats["available"], 1)
        self.assertEqual(stats["per_role"]["editfull"]["total"], 2)

    def test_thongke_overdue_panel_matches_selected_status(self):
        overdue_info = {
            "active_overdue": [
                {"role_type": "editfull"},
                {"role_type": "clean"},
            ],
            "auto_returned": [{"role_type": "editfull"}],
        }

        assigned = _filter_overdue_info(overdue_info, "editfull", "assigned")
        available = _filter_overdue_info(overdue_info, "editfull", "available")

        self.assertEqual(len(assigned["active_overdue"]), 1)
        self.assertEqual(len(assigned["auto_returned"]), 0)
        self.assertEqual(available["active_overdue"], [])
        self.assertEqual(available["auto_returned"], [])

    def test_thongke_uses_two_interactive_filter_dropdowns(self):
        self.assertEqual(list(ThongKe.thongke._params), [])

        view = ThongKeFilterView("guild-1")
        self.assertEqual(len(view.children), 2)
        self.assertEqual(view.role_select.placeholder, "Lọc theo role...")
        self.assertEqual(view.status_select.placeholder, "Lọc theo trạng thái...")
        self.assertEqual(len(view.role_select.options), 5)  # all + 4 roles
        self.assertEqual(len(view.status_select.options), 4)  # all + 3 statuses


if __name__ == "__main__":
    unittest.main()
