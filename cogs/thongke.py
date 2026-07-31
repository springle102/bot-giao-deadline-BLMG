"""
Cog xử lý lệnh /thongke
Hiển thị dashboard thống kê deadline cho admin.
"""

from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands

from config import ADMIN_ROLE_ID, ROLE_CHOICES, is_admin
from database.queries import get_stats, get_role_detailed_deadlines
from utils.embed_builder import (
    create_stats_embed,
    create_role_detail_embeds,
    create_error_embed,
)



class ThongKe(commands.Cog):
    """Cog xử lý lệnh thống kê deadline."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="thongke",
        description="Xem thống kê deadline (Admin)",
    )
    @app_commands.describe(
        role="Vị trí muốn xem chi tiết chap đã giao và còn tồn (tùy chọn)"
    )
    @app_commands.choices(role=ROLE_CHOICES)
    async def thongke(
        self,
        interaction: discord.Interaction,
        role: Optional[app_commands.Choice[str]] = None,
    ):
        """Lệnh xem thống kê tổng quan hoặc chi tiết theo vị trí."""
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        await interaction.response.defer()
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"

        if role is None:
            # Xem thống kê tổng quan
            stats = await get_stats(guild_id=guild_id)
            embed = create_stats_embed(stats)
            await interaction.followup.send(embed=embed)
        else:
            # Xem chi tiết theo role
            role_type = role.value
            role_name = role.name
            deadlines = await get_role_detailed_deadlines(role_type, guild_id=guild_id)
            embeds = create_role_detail_embeds(role_type, role_name, deadlines)
            await interaction.followup.send(embeds=embeds[:10])


async def setup(bot: commands.Bot):
    await bot.add_cog(ThongKe(bot))

