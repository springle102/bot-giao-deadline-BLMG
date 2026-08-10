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


_ALL_FILTER_VALUE = "__all__"


def _get_role_label(role_type: Optional[str]) -> str:
    if not role_type:
        return "Tất cả role"
    return next(
        (choice.name for choice in ROLE_CHOICES if choice.value == role_type),
        role_type,
    )


def _build_filter_label(role_type: Optional[str], status: Optional[str]) -> str:
    parts = []
    if role_type:
        parts.append(f"Role: {_get_role_label(role_type)}")
    if status:
        parts.append(
            f"Trạng thái: {THONGKE_STATUS_LABELS.get(status, status)}"
        )
    return " • ".join(parts) if parts else "Tất cả"


async def _load_thongke_panels(
    guild_id: str,
    role_type: Optional[str] = None,
    status: Optional[str] = None,
) -> list[discord.Embed]:
    overdue_info = await get_overdue_details(guild_id=guild_id)
    drive_failures = await get_active_drive_share_failures(guild_id=guild_id)

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
    filter_label = _build_filter_label(role_type, status)
    series_count = len({str(item.get("series_name") or "") for item in all_deadlines})
    print(
        f"[ThongKe] guild={guild_id} role={role_type or 'all'} "
        f"status={status or 'all'} rows={len(all_deadlines)} series={series_count}",
        flush=True,
    )

    return create_thongke_panels(
        stats=stats,
        all_deadlines=all_deadlines,
        overdue_info=overdue_info,
        drive_failures=drive_failures,
        filter_label=filter_label,
    )


def _chunk_embeds(embeds: list[discord.Embed]) -> list[list[discord.Embed]]:
    """Split embeds into Discord's maximum of 10 embeds per message."""
    return [embeds[offset : offset + 10] for offset in range(0, len(embeds), 10)]


class _ThongKeRoleSelect(discord.ui.Select):
    def __init__(self, parent_view: "ThongKeFilterView"):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(
                label="Tất cả role",
                value=_ALL_FILTER_VALUE,
                default=True,
            )
        ]
        options.extend(
            discord.SelectOption(label=choice.name, value=choice.value)
            for choice in ROLE_CHOICES
        )
        super().__init__(
            placeholder="Lọc theo role...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        self.parent_view.role_type = (
            None if selected == _ALL_FILTER_VALUE else selected
        )
        self.parent_view.sync_defaults()
        await self.parent_view.refresh(interaction)


class _ThongKeStatusSelect(discord.ui.Select):
    def __init__(self, parent_view: "ThongKeFilterView"):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(
                label="Tất cả trạng thái",
                value=_ALL_FILTER_VALUE,
                default=True,
            )
        ]
        options.extend(
            discord.SelectOption(
                label=choice.name,
                value=choice.value,
            )
            for choice in THONGKE_STATUS_CHOICES
        )
        super().__init__(
            placeholder="Lọc theo trạng thái...",
            min_values=1,
            max_values=1,
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        self.parent_view.status = (
            None if selected == _ALL_FILTER_VALUE else selected
        )
        self.parent_view.sync_defaults()
        await self.parent_view.refresh(interaction)


class ThongKeFilterView(discord.ui.View):
    """Interactive role/status filters shown below the statistics dashboard."""

    def __init__(self, guild_id: str):
        super().__init__(timeout=900)
        self.guild_id = guild_id
        self.role_type: Optional[str] = None
        self.status: Optional[str] = None
        self.messages: list = []
        self.role_select = _ThongKeRoleSelect(self)
        self.status_select = _ThongKeStatusSelect(self)
        self.add_item(self.role_select)
        self.add_item(self.status_select)

    def sync_defaults(self):
        for option in self.role_select.options:
            option.default = (
                option.value
                == (self.role_type if self.role_type else _ALL_FILTER_VALUE)
            )
        for option in self.status_select.options:
            option.default = (
                option.value
                == (self.status if self.status else _ALL_FILTER_VALUE)
            )

    async def refresh(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            embeds = await _load_thongke_panels(
                guild_id=self.guild_id,
                role_type=self.role_type,
                status=self.status,
            )
            chunks = _chunk_embeds(embeds)

            for index, chunk in enumerate(chunks):
                if index < len(self.messages):
                    await self.messages[index].edit(
                        embeds=chunk,
                        view=self if index == 0 else None,
                    )
                else:
                    message = await interaction.followup.send(
                        embeds=chunk,
                        view=self if index == 0 else None,
                        wait=True,
                    )
                    self.messages.append(message)

            for message in self.messages[len(chunks) :]:
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
            self.messages = self.messages[: len(chunks)]
        except Exception as exc:
            print(f"[ThongKe] filter refresh failed: {exc}", flush=True)
            await interaction.followup.send(
                embed=create_error_embed(
                    "Không thể tải lại thống kê. Vui lòng thử lại sau ít phút."
                ),
                ephemeral=True,
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await is_admin(interaction):
            return True
        await interaction.response.send_message(
            embed=create_error_embed("Bạn không có quyền dùng bộ lọc thống kê!"),
            ephemeral=True,
        )
        return False

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.messages:
            try:
                await self.messages[0].edit(view=self)
            except discord.NotFound:
                pass


class ThongKe(commands.Cog):
    """Cog xử lý lệnh thống kê deadline."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="thongke",
        description="Xem thống kê deadline với bộ lọc role và trạng thái (Admin)",
    )
    async def thongke(self, interaction: discord.Interaction):
        """Lệnh xem thống kê deadline duy nhất trong 1 panel embed."""
        if not await is_admin(interaction):
            return await interaction.response.send_message(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        await interaction.response.defer()
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"
        view = ThongKeFilterView(guild_id)
        panel_embeds = await _load_thongke_panels(guild_id)
        chunks = _chunk_embeds(panel_embeds)

        # Discord accepts at most 10 embeds per message. Send additional
        # pages as follow-ups so large pools do not silently lose series.
        for index, chunk in enumerate(chunks):
            message = await interaction.followup.send(
                embeds=chunk,
                view=view if index == 0 else None,
                wait=True,
            )
            view.messages.append(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(ThongKe(bot))
