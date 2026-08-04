"""
Cog xử lý lệnh /xoa-dl
Cho phép Admin xóa bất kỳ deadline nào còn tồn (chưa được giao) trong kho deadline.
Hỗ trợ xóa nhiều chap cùng lúc.
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from config import is_admin, ROLE_CHOICES, ROLE_TYPES, COLOR_SUCCESS
from utils.chapter_helper import parse_series_and_chaps_input, chapter_number_to_display
from database.queries import delete_available_deadlines_admin
from utils.embed_builder import create_error_embed, create_success_embed
from utils.admin_notifier import notify_all_admins


class XoaDeadline(commands.Cog):
    """Cog xử lý lệnh xóa deadline chưa giao (dành cho Admin)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="xoa-dl",
        description="Admin xóa deadline chưa giao trong kho (Hỗ trợ xóa nhiều chap cùng lúc)",
    )
    @app_commands.describe(
        truyen="Tên bộ truyện (Ví dụ: 'ALPHEGA' hoặc 'ALPHEGA, SOLO')",
        chap="Danh sách chap hoặc dải chap (Ví dụ: '11, 12' hoặc '11-15' hoặc 'NT1')",
        role="Vị trí deadline cần xóa (Tùy chọn: nếu muốn xóa riêng 1 vị trí cụ thể)",
    )
    @app_commands.choices(role=ROLE_CHOICES)
    async def xoa_deadline(
        self,
        interaction: discord.Interaction,
        truyen: str,
        chap: str,
        role: Optional[app_commands.Choice[str]] = None,
    ):
        # 1. Defer ngay lập tức để tránh 3s timeout từ Discord
        await interaction.response.defer(ephemeral=True)

        try:
            # 2. Kiểm tra quyền Admin
            if not await is_admin(interaction):
                return await interaction.followup.send(
                    embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                    ephemeral=True,
                )

            guild_id = str(interaction.guild_id) if interaction.guild_id else "global"

            # Phân tích danh sách các cặp (truyện, chap) cần xóa
            items_to_delete = parse_series_and_chaps_input(chap, truyen)

            if not items_to_delete:
                return await interaction.followup.send(
                    embed=create_error_embed(
                        "❌ Không tìm thấy thông tin số chap hợp lệ từ câu lệnh!\n"
                        "Ví dụ sử dụng:\n"
                        "• `/xoa-dl truyen:ALPHEGA chap:11, 12`\n"
                        "• `/xoa-dl truyen:ALPHEGA chap:11-15`"
                    ),
                    ephemeral=True,
                )

            role_value = role.value if role else None
            res = await delete_available_deadlines_admin(
                items_to_delete,
                role_type=role_value,
                guild_id=guild_id
            )

            success = res.get("success", [])
            failed = res.get("failed", [])

            if not success and failed:
                failed_str = ", ".join(
                    f"{chapter_number_to_display(c)}" + (f" ({s})" if s else "") for s, c in failed
                )
                return await interaction.followup.send(
                    embed=create_error_embed(
                        f"❌ Không tìm thấy deadline chưa giao (`🟢 Available`) nào phù hợp để xóa!\n"
                        f"Các chap không tìm thấy: {failed_str}"
                    ),
                    ephemeral=True,
                )

            embed = discord.Embed(
                title="👑 [Nhật Ký Quản Trị] Xóa Kho Deadline Thành Công",
                color=COLOR_SUCCESS,
            )

            success_lines = []
            for series, chap_name, r_type, _ in success:
                r_name = ROLE_TYPES.get(r_type, {}).get("name", r_type)
                success_lines.append(f"• 📖 **{chap_name}** ({series}) — Vị trí: `{r_name}`")

            embed.description = (
                f"• **Quản trị viên thực hiện:** {interaction.user.mention}\n"
                f"Đã xóa vĩnh viễn **{len(success)} deadline chưa giao** khỏi hệ thống:\n\n"
                + "\n".join(success_lines)
            )

            if failed:
                failed_lines = [
                    f"• {chapter_number_to_display(c)}" + (f" ({s})" if s else "") for s, c in failed
                ]
                embed.add_field(
                    name="⚠️ Các chap không xóa được (Đã giao/Không tồn tại)",
                    value="\n".join(failed_lines),
                    inline=False,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

            if interaction.guild:
                await notify_all_admins(interaction.guild, embed, actor=interaction.user)

        except Exception as e:
            print(f"[ERROR] Lỗi thực thi /xoa-dl: {e}")
            await interaction.followup.send(
                embed=create_error_embed(f"Có lỗi xảy ra khi xóa deadline: {e}"),
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(XoaDeadline(bot))
