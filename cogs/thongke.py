"""
Cog xử lý lệnh /thongke
Hiển thị 1 panel dashboard thống kê deadline tổng quan, quá hạn (bao gồm thu hồi kho) và chi tiết theo từng role cho admin.
"""

from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands

from config import ROLE_CHOICES, is_admin
from database.queries import (
    get_stats,
    get_role_detailed_deadlines,
    get_all_detailed_deadlines,
    get_overdue_details,
    get_active_drive_share_failures,
)
from utils.embed_builder import (
    create_thongke_panels,
    create_error_embed,
)


class ThongKe(commands.Cog):
    """Cog xử lý lệnh thống kê deadline."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="thongke",
        description="Xem thống kê deadline tổng quan, quá hạn và chi tiết chap trong 1 panel duy nhất (Admin)",
    )
    @app_commands.describe(
        role="Lọc chi tiết deadline theo vị trí cụ thể (tùy chọn)"
    )
    @app_commands.choices(role=ROLE_CHOICES)
    async def thongke(
        self,
        interaction: discord.Interaction,
        role: Optional[app_commands.Choice[str]] = None,
    ):
        """Lệnh xem thống kê deadline duy nhất trong 1 panel embed."""
        if not await is_admin(interaction):
            return await interaction.response.send_message(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        await interaction.response.defer()
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"

        stats = await get_stats(guild_id=guild_id)
        overdue_info = await get_overdue_details(guild_id=guild_id)
        # Only show links currently being avoided. Expired records are history
        # and must not make a healthy/retryable link look broken in the panel.
        drive_failures = await get_active_drive_share_failures(guild_id=guild_id)

        if role is None:
            all_deadlines = await get_all_detailed_deadlines(guild_id=guild_id)
        else:
            role_type = role.value
            all_deadlines = await get_role_detailed_deadlines(role_type, guild_id=guild_id)

        series_count = len({str(item.get("series_name") or "") for item in all_deadlines})
        print(
            f"[ThongKe] guild={guild_id} role={role.value if role else 'all'} "
            f"rows={len(all_deadlines)} series={series_count}",
            flush=True,
        )

        panel_embeds = create_thongke_panels(
            stats=stats,
            all_deadlines=all_deadlines,
            overdue_info=overdue_info,
            drive_failures=drive_failures,
        )

        # Discord accepts at most 10 embeds per message. Send additional
        # pages as follow-ups so large pools do not silently lose series.
        for offset in range(0, len(panel_embeds), 10):
            await interaction.followup.send(embeds=panel_embeds[offset : offset + 10])


async def setup(bot: commands.Bot):
    await bot.add_cog(ThongKe(bot))
