"""
Cog xử lý lệnh /dangky, /xem-email (/xem-emaill) và /xoa-email (/xoa-mail)
Cho phép thành viên đăng ký Gmail và Quản trị viên quản lý danh sách email.
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
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"

        await save_user_email(user_id, username, clean_email, guild_id=guild_id)

        embed = create_success_embed(
            f"Đã lưu thành công địa chỉ email: **{clean_email}**\n\n"
            "💡 Mỗi khi bạn bấm nhận deadline bằng lệnh `/xin-deadline`, "
            "bot sẽ tự động add email này vào Folder Google Drive tương ứng và gửi thông báo qua Gmail cho bạn."
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _handle_xem_email(self, interaction: discord.Interaction):
        """Xử lý hiển thị danh sách email cho Admin."""
        await interaction.response.defer(ephemeral=True)

        if not await is_admin(interaction):
            return await interaction.followup.send(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này! (Cần quyền Admin / Quản Lý)"),
                ephemeral=True,
            )

        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"
        user_emails = await get_all_user_emails(guild_id=guild_id)
        if not user_emails:
            return await interaction.followup.send(
                embed=create_error_embed("Chưa có thành viên nào đăng ký email trên hệ thống của Server này!"),
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

        content_block = "\n".join(lines)
        if len(content_block) > 4000:
            content_block = content_block[:3900] + "\n\n*(Danh sách còn nhiều, đã rút gọn...)*"

        embed.description = content_block
        embed.set_footer(text="Bảng danh sách này chỉ hiển thị riêng cho Admin của Server.")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="xem-email",
        description="Xem danh sách tất cả email đã đăng ký của thành viên (Admin)",
    )
    async def xem_email(self, interaction: discord.Interaction):
        """Lệnh /xem-email dành cho Admin."""
        await self._handle_xem_email(interaction)

    @app_commands.command(
        name="xem-emaill",
        description="Xem danh sách tất cả email đã đăng ký của thành viên (Admin)",
    )
    async def xem_emaill(self, interaction: discord.Interaction):
        """Lệnh /xem-emaill (alias) dành cho Admin."""
        await self._handle_xem_email(interaction)

    async def _handle_xoa_email(
        self,
        interaction: discord.Interaction,
        user: discord.User = None,
        email_hoac_id: str = None,
    ):
        """Xử lý xóa email cho Admin."""
        await interaction.response.defer(ephemeral=True)

        if not await is_admin(interaction):
            return await interaction.followup.send(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này! (Cần quyền Admin / Quản Lý)"),
                ephemeral=True,
            )

        target_identifier = None
        if user:
            target_identifier = str(user.id)
        elif email_hoac_id:
            target_identifier = email_hoac_id.strip()

        if not target_identifier:
            return await interaction.followup.send(
                embed=create_error_embed("Vui lòng chọn 1 `user` hoặc điền ô `email_hoac_id` để xóa!"),
                ephemeral=True,
            )

        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"
        success, deleted_data = await delete_user_email(target_identifier, guild_id=guild_id)

        if success and deleted_data:
            del_uid = deleted_data.get("user_id", "")
            del_name = deleted_data.get("username", "Unknown")
            del_email = deleted_data.get("email", "")

            embed = create_success_embed(
                f"🗑️ **Đã xóa thành công email đăng ký!**\n\n"
                f"• **Thành viên:** <@{del_uid}> (**{del_name}**)\n"
                f"• **Email đã xóa:** `{del_email}`\n\n"
                f"Thông tin email đã được rút khỏi CSDL hệ thống."
            )
        else:
            embed = create_error_embed(
                f"❌ Không tìm thấy dữ liệu email đăng ký tương ứng với `{target_identifier}` trong hệ thống!"
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="xoa-email",
        description="Xóa email đăng ký của một thành viên khi mem out team (Admin)",
    )
    @app_commands.describe(
        user="Chọn thành viên cần xóa email",
        email_hoac_id="Nhập Email hoặc User ID (dùng khi member đã out Discord server)",
    )
    async def xoa_email(
        self,
        interaction: discord.Interaction,
        user: discord.User = None,
        email_hoac_id: str = None,
    ):
        """Lệnh /xoa-email dành cho Admin."""
        await self._handle_xoa_email(interaction, user, email_hoac_id)

    @app_commands.command(
        name="xoa-mail",
        description="Xóa email đăng ký của một thành viên khi mem out team (Admin)",
    )
    @app_commands.describe(
        user="Chọn thành viên cần xóa email",
        email_hoac_id="Nhập Email hoặc User ID (dùng khi member đã out Discord server)",
    )
    async def xoa_mail(
        self,
        interaction: discord.Interaction,
        user: discord.User = None,
        email_hoac_id: str = None,
    ):
        """Lệnh /xoa-mail dành cho Admin."""
        await self._handle_xoa_email(interaction, user, email_hoac_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(DangKy(bot))
