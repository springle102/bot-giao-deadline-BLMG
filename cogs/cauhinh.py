"""
Cog xử lý lệnh /cauhinh và /xem-cauhinh
Cho phép Admin/Chủ Server thiết lập Kênh thông báo và Role Quản lý của Server hiện tại.
"""

from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands

from database.queries import save_server_setting, get_server_setting
from utils.embed_builder import create_success_embed, create_error_embed, COLOR_INFO
from utils.admin_notifier import notify_all_admins


class CauHinhCog(commands.Cog):
    """Cog quản lý cấu hình riêng cho từng Discord Server (Guild)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="cauhinh",
        description="Cấu hình Kênh thông báo deadline, Kênh Nhật Ký Quản Trị và Role Quản lý cho Server (Admin)",
    )
    @app_commands.describe(
        channel="Kênh văn bản nhận thông báo deadline cho thành viên (tùy chọn)",
        log_channel="Kênh văn bản nhận Nhật Ký Quản Trị riêng dành cho Admin (tùy chọn)",
        role="Role được phép sử dụng các lệnh Admin/Quản lý của bot (tùy chọn)",
    )
    async def cauhinh(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        log_channel: Optional[discord.TextChannel] = None,
        role: Optional[discord.Role] = None,
    ):
        """Lệnh thiết lập cấu hình Server."""
        if not interaction.guild_id:
            return await interaction.response.send_message(
                embed=create_error_embed("Lệnh này chỉ có thể sử dụng trong Server Discord!"),
                ephemeral=True,
            )

        # Kiểm tra quyền Admin
        from config import is_admin
        if not await is_admin(interaction):
            return await interaction.response.send_message(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        if channel is None and log_channel is None and role is None:
            return await interaction.response.send_message(
                embed=create_error_embed("Vui lòng chọn ít nhất **Kênh thông báo (channel)**, **Kênh log admin (log_channel)** hoặc **Role quản lý (role)** để thiết lập!"),
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        guild_id = str(interaction.guild_id)
        channel_id = str(channel.id) if channel else None
        log_channel_id = str(log_channel.id) if log_channel else None
        role_id = str(role.id) if role else None

        await save_server_setting(
            guild_id,
            channel_id=channel_id,
            role_id=role_id,
            admin_log_channel_id=log_channel_id,
        )

        # Lấy lại cấu hình sau khi cập nhật
        setting = await get_server_setting(guild_id)
        cfg_channel_id = setting.get("deadline_channel_id") if setting else None
        cfg_log_channel_id = setting.get("admin_log_channel_id") if setting else None
        cfg_role_id = setting.get("admin_role_id") if setting else None

        channel_str = f"<#{cfg_channel_id}>" if cfg_channel_id else "*(Chưa thiết lập - Nhắc qua DM)*"
        log_channel_str = f"<#{cfg_log_channel_id}>" if cfg_log_channel_id else "*(Chưa thiết lập - Chỉ gửi DM cho Admin)*"
        role_str = f"<@&{cfg_role_id}>" if cfg_role_id else "*(Mặc định theo Admin Discord)*"

        embed = discord.Embed(
            title="👑 [Nhật Ký Quản Trị] Cập Nhật Cấu Hình Server",
            color=0x00FF88,
        )
        embed.description = f"• **Quản trị viên thực hiện:** {interaction.user.mention}"
        embed.add_field(name="📢 Kênh thông báo deadline (Member)", value=channel_str, inline=False)
        embed.add_field(name="📜 Kênh Nhật Ký Quản Trị (Admin Only)", value=log_channel_str, inline=False)
        embed.add_field(name="👑 Role Quản lý/Admin", value=role_str, inline=False)
        embed.set_footer(text="Cấu hình này chỉ áp dụng riêng cho Server hiện tại.")

        await interaction.followup.send(embed=embed, ephemeral=True)
        if interaction.guild:
            await notify_all_admins(interaction.guild, embed, actor=interaction.user)

    @app_commands.command(
        name="xem-cauhinh",
        description="Xem cấu hình Kênh thông báo, Kênh nhật ký và Role Quản lý hiện tại của Server",
    )
    async def xem_cauhinh(self, interaction: discord.Interaction):
        """Lệnh xem cấu hình hiện tại của Server."""
        if not interaction.guild_id:
            return await interaction.response.send_message(
                embed=create_error_embed("Lệnh này chỉ có thể sử dụng trong Server Discord!"),
                ephemeral=True,
            )

        # Kiểm tra quyền Admin
        from config import is_admin
        if not await is_admin(interaction):
            return await interaction.response.send_message(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        guild_id = str(interaction.guild_id)
        setting = await get_server_setting(guild_id)

        cfg_channel_id = setting.get("deadline_channel_id") if setting else None
        cfg_log_channel_id = setting.get("admin_log_channel_id") if setting else None
        cfg_role_id = setting.get("admin_role_id") if setting else None

        channel_str = f"<#{cfg_channel_id}>" if cfg_channel_id else "*(Chưa thiết lập - Nhắc qua DM)*"
        log_channel_str = f"<#{cfg_log_channel_id}>" if cfg_log_channel_id else "*(Chưa thiết lập - Chỉ gửi DM cho Admin)*"
        role_str = f"<@&{cfg_role_id}>" if cfg_role_id else "*(Chưa thiết lập - Theo Admin Discord)*"

        embed = discord.Embed(
            title=f"⚙️ Cấu Hình Server — {interaction.guild.name if interaction.guild else ''}",
            color=COLOR_INFO,
        )
        embed.add_field(name="📢 Kênh thông báo deadline (Member)", value=channel_str, inline=False)
        embed.add_field(name="📜 Kênh Nhật Ký Quản Trị (Admin Only)", value=log_channel_str, inline=False)
        embed.add_field(name="👑 Role Quản lý/Admin", value=role_str, inline=False)
        embed.set_footer(text="Dùng lệnh /cauhinh channel:[kênh] log_channel:[kênh_log] role:[role] để thay đổi")

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(CauHinhCog(bot))
