"""
Cog xử lý lệnh /huy-dl
Cho phép admin hủy deadline đã giao cho user (Hỗ trợ nhập nhiều chap & truyện cùng lúc).
"""

import re
from utils.chapter_helper import parse_chapter_input, parse_chap_numbers, parse_series_and_chaps_input
from typing import Optional, List, Tuple
import discord
from discord import app_commands
from discord.ext import commands

from config import is_admin, COLOR_SUCCESS
from database.queries import cancel_bulk_deadlines_admin
from utils.embed_builder import create_error_embed
from utils.admin_notifier import notify_all_admins



class HuyDeadline(commands.Cog):
    """Cog xử lý lệnh hủy deadline của user."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="huy-dl",
        description="Admin hủy deadline đã giao của thành viên (Hỗ trợ nhiều chap/truyện cùng lúc)",
    )
    @app_commands.describe(
        user="Thành viên cần hủy deadline",
        chap="Danh sách chap hoặc cặp truyện/chap (Ví dụ: '11, 12' hoặc '11-15' hoặc 'ALPHEGA chap 11, chap 12')",
        truyen="Tên bộ truyện (Tùy chọn, ví dụ: 'ALPHEGA' hoặc 'ALPHEGA, SOLO')",
    )
    async def huy_deadline(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        chap: str,
        truyen: str = None,
    ):
        # 1. Defer ngay lập tức để tránh lỗi Discord 3s timeout / "BLMG đang suy nghĩ..."
        await interaction.response.defer(ephemeral=True)

        try:
            # 2. Kiểm tra quyền Admin
            if not await is_admin(interaction):
                return await interaction.followup.send(
                    embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                    ephemeral=True,
                )

            guild_id = str(interaction.guild_id) if interaction.guild_id else "global"

            # Phân tích danh sách các cặp (truyện, chap) cần hủy
            items_to_cancel = parse_series_and_chaps_input(chap, truyen)

            if not items_to_cancel:
                return await interaction.followup.send(
                    embed=create_error_embed(
                        "❌ Không tìm thấy thông tin số chap hợp lệ từ câu lệnh!\n"
                        "Ví dụ sử dụng:\n"
                        "• `/huy-dl user:@User truyen:ALPHEGA chap:11, 12`\n"
                        "• `/huy-dl user:@User chap:ALPHEGA chap 11, chap 12`"
                    ),
                    ephemeral=True,
                )

            res = await cancel_bulk_deadlines_admin(str(user.id), items_to_cancel, guild_id=guild_id)
            success = res.get("success", [])
            failed = res.get("failed", [])

            if not success and failed:
                failed_str = ", ".join(
                    f"Chap {c}" + (f" ({s})" if s else "") for s, c in failed
                )
                return await interaction.followup.send(
                    embed=create_error_embed(
                        f"❌ Không tìm thấy deadline nào phù hợp của **{user.display_name}**!\n"
                        f"Các chap không tìm thấy: {failed_str}"
                    ),
                    ephemeral=True,
                )

            # Tự động thu hồi quyền Google Drive nếu người dùng có email đăng ký
            import asyncio
            from database.queries import get_user_email, check_user_active_drive_link
            from utils.google_drive import friendly_drive_error, revoke_drive_permission

            user_email = await get_user_email(str(user.id), guild_id=guild_id)
            if user_email:
                user_email = user_email.strip().lower()
            drive_status_lines = []

            if user_email and success:
                # Lấy các drive_link độc nhất từ danh sách chap bị hủy
                cancelled_links = set(link for _, _, _, link in success if link and link.strip())
                for link in cancelled_links:
                    try:
                        # Kiểm tra xem user có còn chap nào khác đang làm/nhận dùng chung link này không
                        still_active = await check_user_active_drive_link(str(user.id), link, guild_id=guild_id)
                        if not still_active:
                            # Thu hồi quyền Drive nếu không còn chap nào khác dùng chung link này
                            ok, msg = await asyncio.to_thread(revoke_drive_permission, link, user_email)
                            drive_status_lines.append(f"• {msg}")
                        else:
                            drive_status_lines.append(f"• ℹ️ Giữ quyền Drive cho 1 Folder do {user.display_name} vẫn còn chap khác đang làm chung link.")
                    except Exception as drive_err:
                        print(f"[ERROR] Lỗi thu hồi Drive: {drive_err}")
                        drive_status_lines.append(
                            f"• ⚠️ Không thể thu hồi quyền Drive: "
                            f"{friendly_drive_error(drive_err, email=user_email, drive_url=link)}"
                        )

            embed = discord.Embed(
                title="👑 [Nhật Ký Quản Trị] Hủy Deadline Thành Công",
                color=COLOR_SUCCESS,
            )

            success_lines = [f"• 📖 **{chap_name}** ({series})" for series, chap_name, _, _ in success]
            embed.description = (
                f"• **Quản trị viên thực hiện:** {interaction.user.mention}\n"
                f"• **Thành viên bị hủy:** {user.mention}\n"
                f"Đã hủy và trả **{len(success)} chap** về kho deadline (`🟢 Available`):\n\n"
                + "\n".join(success_lines)
            )

            if drive_status_lines:
                embed.add_field(
                    name="📧 Thu Hồi Quyền Google Drive",
                    value="\n".join(drive_status_lines),
                    inline=False,
                )

            if failed:
                failed_lines = [f"• Chap {c}" + (f" ({s})" if s else "") for s, c in failed]
                embed.add_field(
                    name="⚠️ Các chap không tìm thấy (Chưa giao/Đã nộp)",
                    value="\n".join(failed_lines),
                    inline=False,
                )

            embed.set_footer(text="Hệ thống quản lý deadline Admin")
            await interaction.followup.send(embed=embed, ephemeral=True)

            if interaction.guild:
                asyncio.create_task(notify_all_admins(interaction.guild, embed, actor=interaction.user))

        except Exception as e:
            print(f"[ERROR] Lỗi thực thi /huy-dl: {e}")
            try:
                await interaction.followup.send(
                    embed=create_error_embed(f"Có lỗi xảy ra khi thực hiện lệnh: {e}"),
                    ephemeral=True,
                )
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(HuyDeadline(bot))
