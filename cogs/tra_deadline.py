"""
Cog xử lý lệnh /tra-deadline
Cho phép user trả lại deadline đã nhận về pool.
"""

import discord
from discord import app_commands
from discord.ext import commands

from config import ROLE_TYPES
from database.queries import return_deadline, get_deadline_by_chap_and_user
from utils.embed_builder import create_error_embed, create_success_embed


class TraDeadline(commands.Cog):
    """Cog xử lý việc trả deadline về pool."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="tra-dl",
        description="Trả lại deadline đã nhận về pool",
    )
    @app_commands.describe(
        chap="Số chap muốn trả",
        truyen="Tên bộ truyện (tùy chọn, cần thiết nếu bạn nhận trùng số chap ở nhiều bộ)"
    )
    async def tra_deadline(self, interaction: discord.Interaction, chap: int, truyen: str = None):
        """Lệnh cho phép người dùng trả deadline."""
        await interaction.response.defer()

        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"
        
        if not truyen:
            from database.queries import get_assigned_deadlines_by_chap
            matches = await get_assigned_deadlines_by_chap(chap, user_id, guild_id=guild_id)
            if len(matches) > 1:
                series_list = ", ".join(f"**{m['series_name']}**" for m in matches)
                return await interaction.followup.send(
                    embed=create_error_embed(
                        f"Bạn đang nhận **Chap {chap}** ở nhiều bộ truyện khác nhau ({series_list})!\n"
                        f"Vui lòng điền thêm ô `truyen` trong lệnh để trả đúng bộ. Ví dụ:\n"
                        f"`/tra-dl chap:{chap} truyen:{matches[0]['series_name']}`"
                    )
                )
            deadline = matches[0] if matches else None
        else:
            deadline = await get_deadline_by_chap_and_user(chap, user_id, series_name=truyen, guild_id=guild_id)

        if not deadline:
            search_info = f" bộ **{truyen}**" if truyen else ""
            return await interaction.followup.send(
                embed=create_error_embed(
                    f"Không tìm thấy Chap {chap}{search_info} trong deadline của bạn!"
                )
            )

        deadline_id = deadline.get("id")
        role_type = deadline.get("role_type", "")
        role_name = ROLE_TYPES.get(role_type, {}).get("name", role_type)

        success = await return_deadline(deadline_id, user_id, guild_id=guild_id)
        if success:
            await interaction.followup.send(
                embed=create_success_embed(
                    f"↩️ Đã trả Chap {chap} - {role_name} về pool"
                )
            )
        else:
            await interaction.followup.send(
                embed=create_error_embed("Có lỗi xảy ra khi trả deadline!")
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(TraDeadline(bot))
