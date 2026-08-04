"""
Cog xử lý lệnh /xin-tre-dl
Cho phép member xin gia hạn/trễ deadline (tối đa 12 tiếng).
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta

from config import MAX_EXTENSION_HOURS, ROLE_TYPES, COLOR_SUCCESS
from database.queries import (
    get_assigned_deadlines_by_chap,
    get_deadline_by_chap_and_user,
    extend_deadline,
)
from utils.embed_builder import create_error_embed
from utils.time_helper import format_deadline, format_remaining
from utils.chapter_helper import parse_chapter_input, chapter_number_to_display


class XinTreDeadline(commands.Cog):
    """Cog xử lý xin gia hạn/trễ deadline."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="xin-tre-dl",
        description="Xin gia hạn/trễ deadline (tối đa 12 tiếng)",
    )
    @app_commands.describe(
        chap="Số chap cần xin trễ deadline (ví dụ: 10 hoặc NT1 cho ngoại truyện)",
        so_gio="Số giờ xin trễ (tính bằng giờ, tối đa 12 tiếng)",
        truyen="Tên bộ truyện (tùy chọn, cần thiết nếu bạn nhận trùng số chap ở nhiều bộ)",
    )
    async def xin_tre_deadline(
        self,
        interaction: discord.Interaction,
        chap: str,
        so_gio: int,
        truyen: str = None,
    ):
        """Lệnh xin trễ deadline."""
        await interaction.response.defer()

        # 1. Kiểm tra giới hạn số giờ xin trễ (Tối đa 12 tiếng)
        if so_gio > MAX_EXTENSION_HOURS or so_gio < 1:
            return await interaction.followup.send(
                embed=create_error_embed(
                    f"❌ Thời gian xin trễ không hợp lệ! "
                    f"Bạn chỉ được cho phép trễ lâu nhất là **{MAX_EXTENSION_HOURS} tiếng** (1 - {MAX_EXTENSION_HOURS} giờ)."
                )
            )

        parsed = parse_chapter_input(chap)
        if parsed is None:
            return await interaction.followup.send(
                embed=create_error_embed(
                    "Số chương không hợp lệ! Nhập số (ví dụ: `10`) hoặc ngoại truyện (ví dụ: `NT1`)."
                )
            )
        chapter_number, chapter_display = parsed

        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"

        # 2. Tìm deadline tương ứng của user
        if not truyen:
            matches = await get_assigned_deadlines_by_chap(chapter_number, user_id, guild_id=guild_id)
            if len(matches) > 1:
                series_list = ", ".join(f"**{m['series_name']}**" for m in matches)
                return await interaction.followup.send(
                    embed=create_error_embed(
                        f"Bạn đang nhận **{chapter_display}** ở nhiều bộ truyện khác nhau ({series_list})!\n"
                        f"Vui lòng điền thêm ô `truyen` trong lệnh để xin trễ đúng bộ. Ví dụ:\n"
                        f"`/xin-tre-dl chap:{chap} so_gio:{so_gio} truyen:{matches[0]['series_name']}`"
                    )
                )
            deadline = matches[0] if matches else None
        else:
            deadline = await get_deadline_by_chap_and_user(
                chapter_number, user_id, series_name=truyen, guild_id=guild_id
            )

        if not deadline:
            search_info = f" bộ **{truyen}**" if truyen else ""
            return await interaction.followup.send(
                embed=create_error_embed(
                    f"Không tìm thấy {chapter_display}{search_info} trong danh sách deadline đang nhận của bạn!"
                )
            )

        deadline_id = deadline.get("id")
        batch_id = deadline.get("batch_id")
        current_deadline_str = deadline.get("deadline_at")

        if not current_deadline_str:
            return await interaction.followup.send(
                embed=create_error_embed("Deadline này chưa có mốc thời gian hạn nộp!")
            )

        try:
            current_dt = datetime.fromisoformat(current_deadline_str)
        except ValueError:
            current_dt = datetime.strptime(current_deadline_str, "%Y-%m-%d %H:%M:%S")

        # 3. Tính thời gian mới (Cộng thêm so_gio tiếng)
        new_dt = current_dt + timedelta(hours=so_gio)
        new_deadline_str = new_dt.strftime("%Y-%m-%d %H:%M:%S")

        # 4. Cập nhật vào Cơ sở dữ liệu
        success = await extend_deadline(
            deadline_id=deadline_id,
            new_deadline_at=new_deadline_str,
            user_id=user_id,
            username=interaction.user.display_name,
            guild_id=guild_id,
            hours_extended=so_gio,
            batch_id=batch_id,
        )

        if not success:
            return await interaction.followup.send(
                embed=create_error_embed("Có lỗi xảy ra khi cập nhật gia hạn deadline!")
            )

        # 5. Xóa khỏi cache scheduler _already_reminded để nhắc lại khi còn 1 tiếng đối với hạn mới
        if hasattr(self.bot, "scheduler") and self.bot.scheduler:
            if batch_id:
                # Nếu là batch, xóa tất cả deadline IDs thuộc batch
                from database.queries import get_batch_progress
                progress = await get_batch_progress(batch_id, guild_id=guild_id)
                if progress and progress.get("all_deadlines"):
                    batch_ids = [d["id"] for d in progress["all_deadlines"]]
                    self.bot.scheduler.clear_reminded(batch_ids)
            else:
                self.bot.scheduler.clear_reminded([deadline_id])

        # 6. Tạo Embed thông báo gia hạn thành công
        role_type = deadline.get("role_type", "")
        role_name = ROLE_TYPES.get(role_type, {}).get("name", role_type)
        series_name = deadline.get("series_name", "Không xác định")

        embed = discord.Embed(
            title=f"⏰ Xin Trễ Deadline Thành Công - {chapter_display}",
            color=COLOR_SUCCESS,
        )
        embed.add_field(name="📚 Truyện", value=series_name, inline=True)
        embed.add_field(name="👤 Thành viên", value=interaction.user.mention, inline=True)
        embed.add_field(name="💼 Vị trí", value=role_name, inline=True)
        embed.add_field(name="⏱️ Gia hạn thêm", value=f"**+{so_gio} giờ**", inline=False)
        embed.add_field(name="📅 Hạn cũ", value=format_deadline(current_dt), inline=True)
        embed.add_field(
            name="📅 Hạn mới",
            value=f"**{format_deadline(new_dt)}**\n(Còn **{format_remaining(new_dt)}**)",
            inline=True,
        )

        if batch_id:
            embed.set_footer(text="📦 Gia hạn này áp dụng cho toàn bộ batch chap bạn đang nhận.")
        else:
            embed.set_footer(text="Chúc bạn sớm hoàn thành công việc nhé! 💪")

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(XinTreDeadline(bot))
