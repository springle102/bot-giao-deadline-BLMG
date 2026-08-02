"""
Cog xử lý lệnh /add-deadline, /add-deadline-single và /add-deadline-list
Dành cho admin - thêm deadline mới vào hệ thống.
"""

import discord
from discord.ext import commands
from discord import app_commands

from config import ROLE_CHOICES, is_admin
from database.queries import add_deadline, add_bulk_deadlines
from utils.embed_builder import create_success_embed, create_error_embed
from utils.admin_notifier import notify_all_admins



class AddListModal(discord.ui.Modal, title="Thêm danh sách chap & link riêng"):
    danh_sach = discord.ui.TextInput(
        label="Nhập danh sách chap và link (mỗi chap 1 dòng)",
        style=discord.TextStyle.paragraph,
        placeholder="10: https://drive.google.com/link_chap_10\n11: https://drive.google.com/link_chap_11",
        required=True,
        max_length=4000,
    )

    def __init__(self, truyen: str, role: app_commands.Choice[str], guild_id: str = "global"):
        super().__init__()
        self.truyen = truyen
        self.role = role
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        items = []
        lines = self.danh_sach.value.strip().split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue

            parts = None
            for sep in [":", "-", " "]:
                if sep in line:
                    p = line.split(sep, 1)
                    if p[0].strip().isdigit():
                        parts = (int(p[0].strip()), p[1].strip())
                        break

            if parts:
                items.append(parts)

        if not items:
            return await interaction.followup.send(
                embed=create_error_embed(
                    "Danh sách không đúng định dạng!\n"
                    "Vui lòng nhập dạng (mỗi chap 1 dòng):\n"
                    "```\n"
                    "10: https://drive.google.com/link1\n"
                    "11: https://drive.google.com/link2\n"
                    "```"
                ),
                ephemeral=True,
            )

        from database.queries import add_list_deadlines

        count = await add_list_deadlines(self.truyen, self.role.value, items, guild_id=self.guild_id)
        role_name = self.role.name

        embed = create_success_embed(
            f"👑 **[Nhật Ký Quản Trị] Thêm Deadline Mới (Danh Sách)**\n\n"
            f"• **Quản trị viên:** {interaction.user.mention}\n"
            f"• **Bộ truyện:** **{self.truyen}**\n"
            f"• **Vị trí:** **{role_name}**\n"
            f"• **Số chap đã thêm:** **{count} chap**"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        if interaction.guild:
            await notify_all_admins(interaction.guild, embed, actor=interaction.user)


class AddDeadline(commands.Cog):
    """Cog xử lý lệnh thêm deadline (Dành cho admin)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="add-dl",
        description="Thêm nhiều deadline cùng lúc (Admin)",
    )
    @app_commands.describe(
        truyen="Tên truyện",
        role="Vị trí deadline",
        chap_bat_dau="Số chương bắt đầu",
        chap_ket_thuc="Số chương kết thúc",
        drive_link="Link Google Drive (tùy chọn)",
    )
    @app_commands.choices(role=ROLE_CHOICES)
    async def add_deadline_bulk(
        self,
        interaction: discord.Interaction,
        truyen: str,
        role: app_commands.Choice[str],
        chap_bat_dau: int,
        chap_ket_thuc: int,
        drive_link: str = None,
    ):
        """Xử lý lệnh thêm nhiều deadline."""
        if not await is_admin(interaction):
            return await interaction.response.send_message(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        if chap_bat_dau > chap_ket_thuc:
            return await interaction.response.send_message(
                embed=create_error_embed(
                    "Chương bắt đầu không được lớn hơn chương kết thúc!"
                ),
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"

        count = await add_bulk_deadlines(
            truyen, role.value, chap_bat_dau, chap_ket_thuc, drive_link, guild_id=guild_id
        )
        role_name = role.name

        embed = create_success_embed(
            f"👑 **[Nhật Ký Quản Trị] Thêm Deadline Mới**\n\n"
            f"• **Quản trị viên:** {interaction.user.mention}\n"
            f"• **Bộ truyện:** **{truyen}**\n"
            f"• **Vị trí:** **{role_name}**\n"
            f"• **Số chap:** **{count} chap** ({chap_bat_dau}-{chap_ket_thuc})\n"
            f"• **Link Drive:** {drive_link if drive_link else '*(Chưa có)*'}"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        if interaction.guild:
            await notify_all_admins(interaction.guild, embed, actor=interaction.user)

    @app_commands.command(
        name="add-dl-single",
        description="Thêm một deadline (Admin)",
    )
    @app_commands.describe(
        truyen="Tên truyện",
        role="Vị trí deadline",
        chap="Số chương",
        drive_link="Link Google Drive (tùy chọn)",
    )
    @app_commands.choices(role=ROLE_CHOICES)
    async def add_deadline_single(
        self,
        interaction: discord.Interaction,
        truyen: str,
        role: app_commands.Choice[str],
        chap: int,
        drive_link: str = None,
    ):
        """Xử lý lệnh thêm một deadline."""
        if not await is_admin(interaction):
            return await interaction.response.send_message(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"

        chapter_name = f"Chap {chap}"
        await add_deadline(chapter_name, chap, truyen, role.value, drive_link, guild_id=guild_id)
        role_name = role.name

        embed = create_success_embed(
            f"👑 **[Nhật Ký Quản Trị] Thêm Deadline Mới**\n\n"
            f"• **Quản trị viên:** {interaction.user.mention}\n"
            f"• **Bộ truyện:** **{truyen}**\n"
            f"• **Vị trí:** **{role_name}**\n"
            f"• **Chap:** **Chap {chap}**\n"
            f"• **Link Drive:** {drive_link if drive_link else '*(Chưa có)*'}"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        if interaction.guild:
            await notify_all_admins(interaction.guild, embed, actor=interaction.user)

    @app_commands.command(
        name="add-dl-list",
        description="Thêm nhiều chap kèm link riêng cho từng chap (Admin)",
    )
    @app_commands.describe(
        truyen="Tên truyện",
        role="Vị trí deadline",
    )
    @app_commands.choices(role=ROLE_CHOICES)
    async def add_deadline_list(
        self,
        interaction: discord.Interaction,
        truyen: str,
        role: app_commands.Choice[str],
    ):
        """Mở khung Modal nhập văn bản nhiều dòng."""
        if not await is_admin(interaction):
            return await interaction.response.send_message(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"
        modal = AddListModal(truyen=truyen, role=role, guild_id=guild_id)
        await interaction.response.send_modal(modal)


async def setup(bot: commands.Bot):
    await bot.add_cog(AddDeadline(bot))
