"""
Cog xử lý lệnh /xem-deadline
Hiển thị danh sách deadline đang được giao cho user.
"""

import discord
from discord import app_commands
from discord.ext import commands

from database.queries import get_assigned_deadlines
from utils.embed_builder import create_deadline_list


class XemDeadline(commands.Cog):
    """Cog xử lý lệnh xem deadline."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="xem-dl",
        description="Xem tất cả deadline đang được giao cho bạn",
    )
    async def xem_deadline(self, interaction: discord.Interaction):
        """Xem danh sách deadline của user."""
        await interaction.response.defer()

        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"
        deadlines = await get_assigned_deadlines(user_id, guild_id=guild_id)

        embed = create_deadline_list(deadlines, interaction.user)
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(XemDeadline(bot))
