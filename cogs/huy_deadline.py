"""
Cog xử lý lệnh /huy-deadline
Cho phép admin hủy deadline đã giao cho user.
"""

import discord
from discord import app_commands
from discord.ext import commands

from config import ADMIN_ROLE_ID, is_admin
from database.queries import cancel_deadline_admin
from utils.embed_builder import create_error_embed, create_success_embed



class HuyDeadline(commands.Cog):
    """Cog xử lý lệnh hủy deadline của user."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="huy-dl",
        description="Hủy deadline của một thành viên (Admin)",
    )
    @app_commands.describe(
        chap="Số chap cần hủy",
        user="Thành viên cần hủy deadline",
    )
    async def huy_deadline(
        self, interaction: discord.Interaction, chap: int, user: discord.Member
    ):
        """Lệnh hủy deadline dành cho admin."""
        if not is_admin(interaction):
            return await interaction.response.send_message(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        await interaction.response.defer()
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"

        success = await cancel_deadline_admin(chap, str(user.id), guild_id=guild_id)
        if success:
            await interaction.followup.send(
                embed=create_success_embed(
                    f"✅ Đã hủy deadline Chap {chap} của {user.display_name}"
                )
            )
        else:
            await interaction.followup.send(
                embed=create_error_embed(
                    f"Không tìm thấy deadline Chap {chap} của {user.display_name}!"
                )
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(HuyDeadline(bot))
