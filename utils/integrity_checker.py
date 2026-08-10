"""Background integrity checks for deadline assignments."""

import asyncio
import hashlib
from collections import defaultdict
from typing import Any, Dict, List, Tuple

import discord

from config import COLOR_ERROR, COLOR_WARNING, DEADLINE_CHANNEL_ID
from database.queries import (
    get_assigned_deadlines_for_drive_check,
    get_active_drive_share_failures,
    get_user_email,
    record_self_check_finding,
    repair_overextended_deadlines,
    resolve_drive_share_failure,
    resolve_self_check_finding,
)
from utils.admin_notifier import _find_deadline_channel, notify_all_admins
from utils.google_drive import (
    check_drive_permission,
    check_drive_sharing_capability,
    extract_drive_id,
)


class DeadlineIntegrityChecker:
    """Repair legacy extension corruption and audit Drive access."""

    def __init__(self, bot: discord.Client, drive_concurrency: int = 2):
        self.bot = bot
        self._drive_semaphore = asyncio.Semaphore(drive_concurrency)

    async def run(self) -> Dict[str, int]:
        repairs = await repair_overextended_deadlines()
        await self._notify_extension_repairs(repairs)

        drive_notifications = await self._check_drive_access()
        drive_failure_resolutions = await self._recheck_blocked_drive_links()
        return {
            "extension_repairs": sum(1 for item in repairs if item.get("repaired")),
            "drive_notifications": len(drive_notifications),
            "drive_failure_resolutions": drive_failure_resolutions,
        }

    async def _recheck_blocked_drive_links(self) -> int:
        """Re-check active blacklisted Drive IDs and clear recovered ones."""
        rows = await get_active_drive_share_failures()
        if not rows:
            return 0

        grouped: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            drive_link = str(row.get("drive_link") or "").strip()
            if not drive_link:
                continue
            drive_key = str(
                row.get("drive_key")
                or extract_drive_id(drive_link)
                or drive_link.lower().rstrip("/")
            )
            item = grouped.setdefault(
                drive_key,
                {"drive_link": drive_link, "rows": []},
            )
            item["rows"].append(row)

        async def recheck(item: Dict[str, Any]) -> int:
            try:
                async with self._drive_semaphore:
                    can_share, _message, _status = await asyncio.to_thread(
                        check_drive_sharing_capability,
                        item["drive_link"],
                    )
                if can_share is not True:
                    # False means the link is still genuinely blocked. None
                    # means the check was inconclusive/transient; both remain
                    # in the failure table for the next scheduled pass.
                    return 0

                resolved = 0
                for row in item["rows"]:
                    if await resolve_drive_share_failure(
                        str(row.get("guild_id") or "global"),
                        str(row["drive_link"]),
                    ):
                        resolved += 1
                return resolved
            except Exception as error:
                print(
                    f"[SelfCheck] Drive blacklist recheck failed for "
                    f"{extract_drive_id(item['drive_link']) or item['drive_link']}: {error!s}"
                )
                return 0

        resolved_counts = await asyncio.gather(
            *(recheck(item) for item in grouped.values())
        )
        resolved = sum(resolved_counts)
        if resolved:
            print(
                f"[SelfCheck] Đã gỡ {resolved} blacklist Drive sau khi "
                "xác minh bot vẫn có thể share."
            )
        return resolved

    async def _notify_extension_repairs(self, repairs: List[Dict[str, Any]]) -> None:
        repaired = [item for item in repairs if item.get("repaired")]
        invalid = [item for item in repairs if not item.get("repaired")]
        if invalid:
            print(f"[SelfCheck] invalid extension groups: {len(invalid)}")

        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for item in repaired:
            grouped[str(item.get("guild_id") or "global")].append(item)

        for guild_id, items in grouped.items():
            guild = self._get_guild(guild_id)
            if not guild:
                print(f"[SelfCheck] repaired over-limit extensions for guild {guild_id}: {items}")
                continue

            embed = discord.Embed(
                title="⚠️ Self-check đã khấu trừ gia hạn vượt giới hạn",
                color=COLOR_WARNING,
                description=(
                    "Đã phát hiện và tự động đưa các deadline về tối đa "
                    "**12 giờ gia hạn**. Số giờ dư đã bị trừ khỏi thời hạn nộp."
                ),
            )
            for item in items[:10]:
                ids = ", ".join(str(value) for value in item["deadline_ids"])
                user_id = str(item.get("user_id") or "unknown")
                user_label = f"<@{user_id}>" if user_id.isdigit() else f"`{user_id}`"
                embed.add_field(
                    name="\u200b",
                    value=(
                        f"Người dùng: {user_label}\n"
                        f"ID: `{ids}`\n"
                        f"Cũ: **{item['previous_hours']}h** → mới: **{item['current_hours']}h**\n"
                        f"Đã trừ: **{item['excess_hours']}h**\n"
                        f"Hạn mới: `{item['new_deadline_at']}`"
                    ),
                    inline=False,
                )
            if len(items) > 10:
                embed.set_footer(text=f"Còn {len(items) - 10} nhóm được ghi trong log.")

            try:
                channel = await self._get_deadline_channel(guild)
                if channel:
                    await channel.send(embed=embed)
                else:
                    print(f"[SelfCheck] Deadline channel not found for guild {guild_id}")
            except Exception as e:
                print(f"[SelfCheck] Public extension notification failed: {e!s}")

            await notify_all_admins(guild, embed)

    async def _check_drive_access(self) -> List[Dict[str, Any]]:
        rows = await get_assigned_deadlines_for_drive_check()
        grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            key = (
                str(row.get("guild_id") or "global"),
                str(row.get("assigned_to") or ""),
                str(row.get("drive_link") or "").strip(),
            )
            grouped[key].append(row)

        notifications: List[Dict[str, Any]] = []
        for (guild_id, user_id, drive_link), linked_rows in grouped.items():
            fingerprint_base = self._drive_fingerprint(guild_id, user_id, drive_link)
            email = await get_user_email(user_id, guild_id=guild_id)
            if not email:
                should_notify = await record_self_check_finding(
                    guild_id,
                    f"{fingerprint_base}:missing-email",
                    "missing_user_email",
                    "warning",
                    user_id,
                    {"deadline_ids": [row["id"] for row in linked_rows]},
                )
                if should_notify:
                    notifications.append(
                        {
                            "guild_id": guild_id,
                            "title": "Thiếu email Drive",
                            "details": (
                                f"User `{user_id}` có deadline đang assigned "
                                "nhưng chưa có email đăng ký."
                            ),
                        }
                    )
                continue

            await resolve_self_check_finding(f"{fingerprint_base}:missing-email")

            async with self._drive_semaphore:
                ok, message, status = await asyncio.to_thread(
                    check_drive_permission, drive_link, email
                )

            missing_fingerprint = f"{fingerprint_base}:missing"
            error_fingerprint = f"{fingerprint_base}:error"
            if ok:
                # The assigned member's permission is the strongest evidence
                # that this Drive item is healthy again. Clear any stale share
                # blacklist even when the assignment predates the latest fix.
                await resolve_drive_share_failure(guild_id, drive_link)
                await resolve_self_check_finding(missing_fingerprint)
                await resolve_self_check_finding(error_fingerprint)
                continue

            issue_type = "drive_share_missing" if status is None else "drive_api_error"
            severity = "critical" if status in (400, 403, 404) or status is None else "warning"
            fingerprint = missing_fingerprint if status is None else error_fingerprint
            await resolve_self_check_finding(
                error_fingerprint if status is None else missing_fingerprint
            )
            should_notify = await record_self_check_finding(
                guild_id,
                fingerprint,
                issue_type,
                severity,
                f"{user_id}:{extract_drive_id(drive_link) or drive_link}",
                {
                    "user_id": user_id,
                    "email": email,
                    "drive_link": drive_link,
                    "deadline_ids": [row["id"] for row in linked_rows],
                    "status": status,
                    "message": message[:500],
                },
            )
            if should_notify:
                notifications.append(
                    {
                        "guild_id": guild_id,
                        "title": "Share Drive không hợp lệ",
                        "details": (
                            f"User `{user_id}` · deadline `"
                            f"{', '.join(str(row['id']) for row in linked_rows)}`\n"
                            f"Link: `{drive_link}`\nLỗi: {message[:500]}"
                        ),
                    }
                )

        await self._notify_drive_findings(notifications)
        return notifications

    async def _notify_drive_findings(self, findings: List[Dict[str, Any]]) -> None:
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for finding in findings:
            grouped[str(finding["guild_id"])].append(finding)

        for guild_id, items in grouped.items():
            guild = self._get_guild(guild_id)
            if not guild:
                print(f"[SelfCheck] Drive findings for guild {guild_id}: {items}")
                continue
            embed = discord.Embed(
                title="🚨 Self-check phát hiện lỗi quyền Drive",
                color=COLOR_ERROR,
                description="Có deadline đã ghi nhận nhưng quyền Drive cần được kiểm tra.",
            )
            for item in items[:10]:
                embed.add_field(
                    name=item["title"],
                    value=item["details"][:1024],
                    inline=False,
                )
            if len(items) > 10:
                embed.set_footer(text=f"Còn {len(items) - 10} lỗi khác được lưu trong DB.")
            await notify_all_admins(guild, embed)

    def _get_guild(self, guild_id: str):
        if guild_id == "global" or not guild_id.isdigit():
            return None
        return self.bot.get_guild(int(guild_id))

    async def _get_deadline_channel(self, guild):
        if guild:
            channel = await _find_deadline_channel(guild)
            if channel:
                return channel

        if DEADLINE_CHANNEL_ID and str(DEADLINE_CHANNEL_ID).strip().isdigit():
            return self.bot.get_channel(int(DEADLINE_CHANNEL_ID))
        return None

    @staticmethod
    def _drive_fingerprint(guild_id: str, user_id: str, drive_link: str) -> str:
        value = f"{guild_id}|{user_id}|{extract_drive_id(drive_link) or drive_link}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
