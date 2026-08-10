"""
Cog xử lý lệnh /thongke
Hiển thị 1 panel dashboard thống kê deadline tổng quan, quá hạn (bao gồm thu hồi kho) và chi tiết theo từng role cho admin.
"""

from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands

from config import (
    ROLE_CHOICES,
    THONGKE_STATUS_CHOICES,
    THONGKE_STATUS_LABELS,
    is_admin,
)
from database.queries import (
    get_role_detailed_deadlines,
    get_all_detailed_deadlines,
    get_overdue_details,
    get_active_drive_share_failures,
)
from utils.embed_builder import (
    create_thongke_panels,
    create_error_embed,
)
from utils.time_helper import get_now_str


def _build_stats_from_rows(rows: list[dict]) -> dict:
    """Build summary counts from the same filtered rows shown in the table."""
    stats = {
        "total": 0,
        "available": 0,
        "assigned": 0,
        "submitted": 0,
        "overdue": 0,
        "per_role": {},
    }
    now_str = get_now_str()

    for row in rows:
        status = row.get("status")
        role_type = row.get("role_type") or ""
        stats["total"] += 1
        if status in {"available", "assigned", "submitted"}:
            stats[status] += 1

        is_overdue = (
            status == "assigned"
            and row.get("deadline_at")
            and row["deadline_at"] < now_str
        )
        if is_overdue:
            stats["overdue"] += 1

        role_stats = stats["per_role"].setdefault(
            role_type,
            {
                "total": 0,
                "available": 0,
                "assigned": 0,
                "submitted": 0,
                "overdue": 0,
            },
        )
        role_stats["total"] += 1
        if status in {"available", "assigned", "submitted"}:
            role_stats[status] += 1
        if is_overdue:
            role_stats["overdue"] += 1

    return stats


def _filter_overdue_info(
    overdue_info: dict,
    role_type: Optional[str],
    status: Optional[str],
) -> dict:
    """Keep the overdue panel consistent with the selected filters."""
    active = list(overdue_info.get("active_overdue", []) if overdue_info else [])
    returned = list(overdue_info.get("auto_returned", []) if overdue_info else [])

    if role_type:
        active = [item for item in active if item.get("role_type") == role_type]
        returned = [item for item in returned if item.get("role_type") == role_type]

    if status:
        # Active overdue rows are assigned. Auto-returned rows are historical
        # and do not belong to any current status filter.
        active = active if status == "assigned" else []
        returned = []

    return {"active_overdue": active, "auto_returned": returned}


class ThongKe(commands.Cog):
    """Cog xử lý lệnh thống kê deadline."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="thongke",
        description="Xem thống kê deadline; có thể lọc theo role và trạng thái (Admin)",
    )
    @app_commands.describe(
        role="Lọc chi tiết deadline theo vị trí cụ thể (tùy chọn)"
    )
    @app_commands.describe(
        trang_thai="Lọc theo trạng thái: đang làm, đã nộp hoặc còn tồn",
    )
    @app_commands.choices(
        role=ROLE_CHOICES,
        trang_thai=THONGKE_STATUS_CHOICES,
    )
    async def thongke(
        self,
        interaction: discord.Interaction,
        role: Optional[app_commands.Choice[str]] = None,
        trang_thai: Optional[app_commands.Choice[str]] = None,
    ):
        """Lệnh xem thống kê deadline duy nhất trong 1 panel embed."""
        if not await is_admin(interaction):
            return await interaction.response.send_message(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        await interaction.response.defer()
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"

        overdue_info = await get_overdue_details(guild_id=guild_id)
        # Only show links currently being avoided. Expired records are history
        # and must not make a healthy/retryable link look broken in the panel.
        drive_failures = await get_active_drive_share_failures(guild_id=guild_id)

        role_type = role.value if role else None
        status = trang_thai.value if trang_thai else None

        if role_type is None:
            all_deadlines = await get_all_detailed_deadlines(guild_id=guild_id)
        else:
            all_deadlines = await get_role_detailed_deadlines(
                role_type,
                guild_id=guild_id,
            )

        if status:
            all_deadlines = [
                deadline
                for deadline in all_deadlines
                if deadline.get("status") == status
            ]

        stats = _build_stats_from_rows(all_deadlines)
        overdue_info = _filter_overdue_info(overdue_info, role_type, status)

        filter_parts = []
        if role:
            filter_parts.append(f"Role: {role.name}")
        if trang_thai:
            filter_parts.append(
                f"Trạng thái: {THONGKE_STATUS_LABELS.get(status, trang_thai.name)}"
            )
        filter_label = " • ".join(filter_parts) if filter_parts else "Tất cả"

        series_count = len({str(item.get("series_name") or "") for item in all_deadlines})
        print(
            f"[ThongKe] guild={guild_id} role={role_type or 'all'} "
            f"status={status or 'all'} rows={len(all_deadlines)} series={series_count}",
            flush=True,
        )

        panel_embeds = create_thongke_panels(
            stats=stats,
            all_deadlines=all_deadlines,
            overdue_info=overdue_info,
            drive_failures=drive_failures,
            filter_label=filter_label,
        )

        # Discord accepts at most 10 embeds per message. Send additional
        # pages as follow-ups so large pools do not silently lose series.
        for offset in range(0, len(panel_embeds), 10):
            await interaction.followup.send(embeds=panel_embeds[offset : offset + 10])


async def setup(bot: commands.Bot):
    await bot.add_cog(ThongKe(bot))
