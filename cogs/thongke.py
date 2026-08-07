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
    get_drive_share_failures,
)
from utils.embed_builder import (
    create_single_thongke_panel,
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
        drive_failures = await get_drive_share_failures(guild_id=guild_id)

        if role is None:
            all_deadlines = await get_all_detailed_deadlines(guild_id=guild_id)
        else:
            role_type = role.value
            all_deadlines = await get_role_detailed_deadlines(role_type, guild_id=guild_id)

        panel_embed = create_single_thongke_panel(
            stats=stats,
            all_deadlines=all_deadlines,
            overdue_info=overdue_info,
            drive_failures=drive_failures,
        )

        await interaction.followup.send(embed=panel_embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ThongKe(bot))


