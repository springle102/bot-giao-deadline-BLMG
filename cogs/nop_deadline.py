"""
Cog xử lý lệnh /nop-deadline và /nop-deadline-all
Cho phép user nộp deadline đã hoàn thành.
"""

import discord
from discord.ext import commands
from discord import app_commands

from database.queries import mark_submitted, mark_all_submitted, get_deadline_by_chap_and_user
from utils.embed_builder import create_success_embed, create_error_embed


class NopDeadline(commands.Cog):
    """Cog xử lý lệnh nộp deadline."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="nop-dl",
        description="Nộp deadline một chương cụ thể",
    )
    @app_commands.describe(
        chap="Số chương cần nộp",
        truyen="Tên bộ truyện (tùy chọn, cần thiết nếu bạn có trùng số chap ở nhiều bộ truyện)"
    )
    async def nop_deadline(self, interaction: discord.Interaction, chap: int, truyen: str = None):
        """Nộp 1 chương cụ thể."""
        await interaction.response.defer()

        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"
        
        # Nếu không cung cấp tên truyện, kiểm tra xem người dùng có bị trùng chap X ở nhiều bộ không
        if not truyen:
            from database.queries import get_assigned_deadlines_by_chap
            matches = await get_assigned_deadlines_by_chap(chap, user_id, guild_id=guild_id)
            if len(matches) > 1:
                series_list = ", ".join(f"**{m['series_name']}**" for m in matches)
                return await interaction.followup.send(
                    embed=create_error_embed(
                        f"Bạn đang nhận **Chap {chap}** ở nhiều bộ truyện khác nhau ({series_list})!\n"
                        f"Vui lòng điền thêm ô `truyen` trong lệnh để nộp đúng bộ. Ví dụ:\n"
                        f"`/nop-dl chap:{chap} truyen:{matches[0]['series_name']}`"
                    )
                )
            deadline = matches[0] if matches else None
        else:
            deadline = await get_deadline_by_chap_and_user(chap, user_id, series_name=truyen, guild_id=guild_id)

        if not deadline:
            search_info = f" bộ **{truyen}**" if truyen else ""
            return await interaction.followup.send(
                embed=create_error_embed(
                    f"Không tìm thấy deadline Chap {chap}{search_info} được giao cho bạn "
                    f"hoặc đã nộp rồi!"
                )
            )

        batch_id = deadline.get("batch_id")
        success = await mark_submitted(deadline["id"], user_id, guild_id=guild_id)
        if success:
            if batch_id:
                from database.queries import get_batch_progress
                from utils.time_helper import format_remaining, format_deadline
                from datetime import datetime

                progress = await get_batch_progress(batch_id, guild_id=guild_id)
                if progress:
                    total = progress["total"]
                    submitted = progress["submitted"]
                    remaining_items = progress["remaining"]
                    dl_at = progress["deadline_at"]

                    embed = discord.Embed(
                        title=f"📝 Đã nộp thành công Chap {chap}!",
                        color=0x00FF88,
                    )
                    embed.add_field(
                        name="📊 Tiến độ batch",
                        value=f"**{submitted}/{total}** chap đã nộp",
                        inline=False,
                    )

                    if remaining_items:
                        chap_names = ", ".join(c["chapter_name"] for c in remaining_items)
                        embed.add_field(
                            name="📖 Các chap còn lại",
                            value=chap_names,
                            inline=False,
                        )
                        if dl_at:
                            dt = datetime.fromisoformat(dl_at)
                            embed.add_field(
                                name="⏰ Hạn nộp chung",
                                value=f"{format_deadline(dt)} (Còn **{format_remaining(dt)}**)",
                                inline=False,
                            )
                    else:
                        embed.description = "🎉 **Chúc mừng! Bạn đã hoàn thành tất cả các chap trong batch này!**"

                    return await interaction.followup.send(embed=embed)

            await interaction.followup.send(
                embed=create_success_embed(f"📝 Đã nộp thành công Chap {chap}!")
            )
        else:
            await interaction.followup.send(
                embed=create_error_embed("Có lỗi xảy ra khi nộp deadline!")
            )

    @app_commands.command(
        name="nop-dl-all",
        description="Nộp tất cả deadline hiện có của bạn",
    )
    async def nop_deadline_all(self, interaction: discord.Interaction):
        """Nộp tất cả deadline."""
        await interaction.response.defer()

        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"
        count = await mark_all_submitted(user_id, guild_id=guild_id)

        if count > 0:
            await interaction.followup.send(
                embed=create_success_embed(
                    f"📝 Đã nộp thành công **{count}** deadline!"
                )
            )
        else:
            await interaction.followup.send(
                embed=create_error_embed("Bạn không có deadline nào cần nộp!")
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(NopDeadline(bot))
