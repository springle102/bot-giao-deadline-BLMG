"""
Cog xử lý lệnh /dangky
Cho phép thành viên đăng ký hoặc cập nhật địa chỉ Gmail để tự động nhận quyền truy cập Google Drive.
"""

import re
import discord
from discord import app_commands
from discord.ext import commands

from config import is_admin, COLOR_INFO
from database.queries import save_user_email, get_all_user_emails, delete_user_email
from utils.embed_builder import create_success_embed, create_error_embed


EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class DangKy(commands.Cog):
    """Cog xử lý đăng ký và quản lý email thành viên."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="dangky",
        description="Đăng ký hoặc cập nhật email của bạn để tự động nhận quyền Google Drive",
    )
    @app_commands.describe(
        email="Địa chỉ Gmail của bạn (ví dụ: user@gmail.com)"
    )
    async def dangky(self, interaction: discord.Interaction, email: str):
        """Đăng ký email nhận quyền Drive."""
        clean_email = email.strip().lower()

        if not EMAIL_REGEX.match(clean_email):
            return await interaction.response.send_message(
                embed=create_error_embed(
                    f"Địa chỉ email `{email}` không đúng định dạng!\n"
                    "Vui lòng nhập lại địa chỉ email hợp lệ (ví dụ: `yourname@gmail.com`)."
                ),
                ephemeral=True,
            )

        user_id = str(interaction.user.id)
        username = interaction.user.display_name

        await save_user_email(user_id, username, clean_email)

        embed = create_success_embed(
            f"Đã lưu thành công địa chỉ email: **{clean_email}**\n\n"
            "💡 Mỗi khi bạn bấm nhận deadline bằng lệnh `/xin-deadline`, "
            "bot sẽ tự động add email này vào Folder Google Drive tương ứng và gửi thông báo qua Gmail cho bạn."
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="xem-email",
        description="Xem danh sách tất cả email đã đăng ký của thành viên (Admin)",
    )
    async def xem_email(self, interaction: discord.Interaction):
        """Lệnh cho Admin xem toàn bộ danh sách email đã đăng ký."""
        if not await is_admin(interaction):
            return await interaction.response.send_message(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        user_emails = await get_all_user_emails()
        if not user_emails:
            return await interaction.followup.send(
                embed=create_error_embed("Chưa có thành viên nào đăng ký email trên hệ thống!"),
                ephemeral=True,
            )

        embed = discord.Embed(
            title=f"📧 Danh Sách Email Thành Viên Đã Đăng Ký ({len(user_emails)})",
            color=COLOR_INFO,
        )

        lines = []
        for item in user_emails:
            uid = item["user_id"]
            name = item.get("username", "Unknown")
            mail = item.get("email", "")
            lines.append(f"• <@{uid}> (**{name}**): `{mail}`")

        # Chia dòng để tránh quá giới hạn Discord Embed (max 4096 chars)
        content_block = "\n".join(lines)
        if len(content_block) > 4000:
            content_block = content_block[:3900] + "\n\n*(Danh sách còn nhiều, đã rút gọn...)*"

        embed.description = content_block
        embed.set_footer(text="Bảng danh sách này chỉ hiển thị riêng cho Admin.")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="xoa-email",
        description="Xóa email đăng ký của một thành viên khi mem out team (Admin)",
    )
    @app_commands.describe(
        user="Thành viên cần xóa email đăng ký khỏi hệ thống"
    )
    async def xoa_email(self, interaction: discord.Interaction, user: discord.User):
        """Lệnh cho Admin xóa 1 email thành viên khi out team."""
        if not await is_admin(interaction):
            return await interaction.response.send_message(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        success = await delete_user_email(str(user.id))

        if success:
            embed = create_success_embed(
                f"🗑️ **Đã xóa thành công email đăng ký của thành viên {user.mention}!**\n"
                f"Thông tin email của thành viên đã được rút khỏi CSDL hệ thống."
            )
        else:
            embed = create_error_embed(
                f"❌ Không tìm thấy dữ liệu email đăng ký của thành viên {user.mention} trong hệ thống!"
            )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DangKy(bot))

