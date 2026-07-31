"""
Cog xử lý lệnh /dangky
Cho phép thành viên đăng ký hoặc cập nhật địa chỉ Gmail để tự động nhận quyền truy cập Google Drive.
"""

import re
import discord
from discord import app_commands
from discord.ext import commands

from database.queries import save_user_email
from utils.embed_builder import create_success_embed, create_error_embed


EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class DangKy(commands.Cog):
    """Cog xử lý đăng ký email thành viên."""

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


async def setup(bot: commands.Bot):
    await bot.add_cog(DangKy(bot))
