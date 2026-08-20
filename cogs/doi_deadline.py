"""
Cog xử lý lệnh /doi-dl.
Cho phép admin dời thêm hạn cho một batch mà member đã xin trễ trước đó.
"""

import asyncio
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from config import COLOR_SUCCESS, ROLE_TYPES, is_admin
from database.queries import (
    extend_deadline_admin,
    get_assigned_deadlines_by_chap,
    get_batch_progress,
    get_deadline_by_chap_and_user,
)
from utils.admin_notifier import notify_all_admins
from utils.chapter_helper import chapter_number_to_display, parse_chapter_input
from utils.embed_builder import create_error_embed
from utils.time_helper import format_deadline, format_remaining


class DoiDeadline(commands.Cog):
    """Cog xử lý việc admin dời thêm deadline cho member."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="doi-dl",
        description="Admin dời thêm hạn cho batch đã được member xin trễ",
    )
    @app_commands.describe(
        user="Member cần dời thêm deadline",
        chap="Một chap thuộc batch (ví dụ: 10 hoặc NT1)",
        so_gio="Số giờ admin muốn dời thêm",
        truyen="Tên bộ truyện (tùy chọn nếu chap không bị trùng)",
    )
    async def doi_deadline(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        chap: str,
        so_gio: int,
        truyen: str = None,
    ):
        """Dời thêm thời hạn cho toàn bộ phần còn lại của một batch."""
        await interaction.response.defer(ephemeral=True)

        if not await is_admin(interaction):
            return await interaction.followup.send(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        if so_gio < 1:
            return await interaction.followup.send(
                embed=create_error_embed("Số giờ dời thêm phải từ **1 giờ** trở lên."),
                ephemeral=True,
            )

        parsed = parse_chapter_input(chap)
        if parsed is None:
            return await interaction.followup.send(
                embed=create_error_embed(
                    "Số chương không hợp lệ! Nhập số (ví dụ: `10`) hoặc ngoại truyện (ví dụ: `NT1`)."
                ),
                ephemeral=True,
            )
        chapter_number, chapter_display = parsed
        user_id = str(user.id)
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"

        if truyen:
            deadline = await get_deadline_by_chap_and_user(
                chapter_number,
                user_id,
                series_name=truyen,
                guild_id=guild_id,
            )
        else:
            matches = await get_assigned_deadlines_by_chap(
                chapter_number,
                user_id,
                guild_id=guild_id,
            )
            if len(matches) > 1:
                series_list = ", ".join(f"**{item['series_name']}**" for item in matches)
                return await interaction.followup.send(
                    embed=create_error_embed(
                        f"Member đang nhận **{chapter_display}** ở nhiều bộ truyện ({series_list})!\n"
                        "Vui lòng nhập thêm ô `truyen` để chọn đúng batch."
                    ),
                    ephemeral=True,
                )
            deadline = matches[0] if matches else None

        if not deadline:
            search_info = f" bộ **{truyen}**" if truyen else ""
            return await interaction.followup.send(
                embed=create_error_embed(
                    f"Không tìm thấy {chapter_display}{search_info} đang được giao cho member **{user.display_name}**."
                ),
                ephemeral=True,
            )

        batch_id = deadline.get("batch_id")
        if not batch_id:
            return await interaction.followup.send(
                embed=create_error_embed(
                    "Lệnh này chỉ áp dụng cho **batch**. Chap được chọn không thuộc batch nào."
                ),
                ephemeral=True,
            )

        current_deadline_str = deadline.get("deadline_at")
        if not current_deadline_str:
            return await interaction.followup.send(
                embed=create_error_embed("Batch này chưa có mốc thời gian hạn nộp."),
                ephemeral=True,
            )

        try:
            current_dt = datetime.fromisoformat(current_deadline_str)
        except (TypeError, ValueError):
            try:
                current_dt = datetime.strptime(current_deadline_str, "%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                return await interaction.followup.send(
                    embed=create_error_embed("Không đọc được mốc deadline hiện tại của batch."),
                    ephemeral=True,
                )

        new_dt = current_dt + timedelta(hours=so_gio)
        extension_result = await extend_deadline_admin(
            deadline_id=deadline["id"],
            new_deadline_at=new_dt.strftime("%Y-%m-%d %H:%M:%S"),
            user_id=user_id,
            username=f"Admin: {interaction.user.display_name}",
            guild_id=guild_id,
            hours_extended=so_gio,
            batch_id=batch_id,
        )

        if not extension_result.get("success"):
            reason = extension_result.get("reason")
            if reason == "member_has_not_requested_extension":
                return await interaction.followup.send(
                    embed=create_error_embed(
                        "Member chưa xin trễ deadline cho batch này. "
                        "Lệnh `/doi-dl` chỉ dùng để dời thêm sau lần xin trễ trước đó."
                    ),
                    ephemeral=True,
                )
            if reason == "not_found":
                return await interaction.followup.send(
                    embed=create_error_embed(
                        "Batch không còn deadline đang làm để dời thêm. Có thể member đã nộp hoặc bị hủy."
                    ),
                    ephemeral=True,
                )
            return await interaction.followup.send(
                embed=create_error_embed("Không thể cập nhật deadline vì dữ liệu vừa thay đổi hoặc có lỗi cơ sở dữ liệu."),
                ephemeral=True,
            )

        if hasattr(self.bot, "scheduler") and self.bot.scheduler:
            progress = await get_batch_progress(batch_id, guild_id=guild_id)
            if progress and progress.get("all_deadlines"):
                self.bot.scheduler.clear_reminded(
                    [item["id"] for item in progress["all_deadlines"]]
                )

        role_type = deadline.get("role_type", "")
        role_name = ROLE_TYPES.get(role_type, {}).get("name", role_type)
        series_name = deadline.get("series_name", "Không xác định")
        target_name = getattr(user, "mention", user.display_name)
        embed = discord.Embed(
            title="👑 [Nhật Ký Quản Trị] Dời Thêm Deadline Thành Công",
            color=COLOR_SUCCESS,
        )
        embed.add_field(name="👤 Member", value=target_name, inline=True)
        embed.add_field(name="📚 Truyện", value=series_name, inline=True)
        embed.add_field(name="💼 Vị trí", value=role_name, inline=True)
        embed.add_field(name="📦 Batch", value=f"`{batch_id}`", inline=False)
        embed.add_field(name="⏱️ Admin dời thêm", value=f"**+{so_gio} giờ**", inline=True)
        embed.add_field(
            name="⏰ Gia hạn trước đó của member",
            value=f"**{extension_result.get('member_extension_hours', 0)} giờ**",
            inline=True,
        )
        embed.add_field(name="🛠️ Người thực hiện", value=interaction.user.mention, inline=True)
        embed.add_field(name="📅 Hạn cũ", value=format_deadline(current_dt), inline=True)
        embed.add_field(
            name="📅 Hạn mới",
            value=f"**{format_deadline(new_dt)}**\n(Còn **{format_remaining(new_dt)}**)",
            inline=True,
        )
        embed.set_footer(text="Lệnh admin này không thay đổi ngân sách 12 giờ của member.")

        await interaction.followup.send(embed=embed, ephemeral=True)
        if interaction.guild:
            asyncio.create_task(
                notify_all_admins(interaction.guild, embed, actor=interaction.user)
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(DoiDeadline(bot))
