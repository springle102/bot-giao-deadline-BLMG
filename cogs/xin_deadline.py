"""
Cog xử lý lệnh /xin-deadline
Cho phép member xin deadline với button xác nhận.
"""

import discord
from discord.ext import commands
from discord import app_commands

from config import ROLE_TYPES, ROLE_CHOICES, CONFIRM_TIMEOUT_SECONDS
from database.queries import (
    get_available_deadlines,
    set_pending_deadlines,
    confirm_deadlines,
    cancel_pending_deadlines,
    get_user_email,
)
from utils.time_helper import calculate_deadline, format_deadline, calculate_total_days
from utils.embed_builder import create_deadline_preview, create_deadline_confirm, create_error_embed
from utils.google_drive import grant_drive_permission


class ConfirmDeadlineView(discord.ui.View):
    """View với button ✅ Xác nhận và ❌ Hủy bỏ."""

    def __init__(
        self,
        deadline_ids: list[int],
        user_id: int,
        username: str,
        role_type: str,
        chap_count: int,
        chapters: list[dict],
        deadline_at,
        total_days: int,
        original_interaction: discord.Interaction,
        guild_id: str = "global",
    ):
        super().__init__(timeout=CONFIRM_TIMEOUT_SECONDS)
        self.deadline_ids = deadline_ids
        self.user_id = user_id
        self.username = username
        self.role_type = role_type
        self.chap_count = chap_count
        self.chapters = chapters
        self.deadline_at = deadline_at
        self.total_days = total_days
        self.original_interaction = original_interaction
        self.guild_id = guild_id
        self.is_responded = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Chỉ cho phép user gốc bấm button."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ Bạn không thể tương tác với nút này!", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Xác nhận", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Xác nhận nhận deadline."""
        self.is_responded = True

        # Disable tất cả buttons
        for btn in self.children:
            btn.disabled = True

        # Tạo batch_id nếu xin nhiều chap (hoặc tạo luôn để đồng bộ)
        import uuid
        batch_id = str(uuid.uuid4()) if len(self.deadline_ids) > 1 else None

        # Xác nhận trong database
        deadline_at_str = self.deadline_at.strftime("%Y-%m-%d %H:%M:%S")
        await confirm_deadlines(
            self.deadline_ids,
            str(self.user_id),
            self.username,
            deadline_at_str,
            batch_id=batch_id,
            guild_id=self.guild_id,
        )

        # Cấp quyền Google Drive nếu thành viên đã đăng ký email
        user_email = await get_user_email(str(self.user_id))
        drive_status_msgs = []

        if user_email:
            # Lấy các drive link duy nhất từ danh sách chap nhận
            unique_links = set(c.get("drive_link") for c in self.chapters if c.get("drive_link"))
            for link in unique_links:
                success, msg = grant_drive_permission(link, user_email, role="writer", send_notification=True)
                drive_status_msgs.append(f"• {msg}")
        else:
            drive_status_msgs.append("⚠️ Bạn chưa đăng ký email! Hãy dùng lệnh `/dangky [email]` để tự động nhận quyền Drive trong lần tới.")

        # Tạo embed xác nhận
        embed = create_deadline_confirm(
            self.chapters,
            self.role_type,
            self.deadline_at,
            interaction.user,
            self.total_days,
        )

        if drive_status_msgs:
            embed.add_field(
                name="📧 Cấp Quyền Google Drive",
                value="\n".join(drive_status_msgs),
                inline=False,
            )

        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


    @discord.ui.button(label="Hủy bỏ", style=discord.ButtonStyle.red, emoji="❌")
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Hủy yêu cầu nhận deadline."""
        self.is_responded = True

        for btn in self.children:
            btn.disabled = True

        # Trả deadline về pool
        await cancel_pending_deadlines(self.deadline_ids, guild_id=self.guild_id)

        embed = create_error_embed("Đã hủy yêu cầu nhận deadline.")
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def on_timeout(self):
        """Tự động hủy khi hết thời gian."""
        if not self.is_responded:
            for btn in self.children:
                btn.disabled = True

            await cancel_pending_deadlines(self.deadline_ids, guild_id=self.guild_id)

            try:
                message = await self.original_interaction.original_response()
                embed = create_error_embed(
                    "⏰ Đã hết 6 giờ xác nhận. Deadline đã được tự động hủy và trả về pool."
                )
                await message.edit(embed=embed, view=self)
            except Exception:
                pass


class XinDeadline(commands.Cog):
    """Cog xử lý lệnh xin deadline."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="xin-dl",
        description="Xin nhận deadline cho một vị trí cụ thể",
    )
    @app_commands.describe(
        role="Vị trí bạn muốn xin deadline",
        so_luong="Số lượng chap muốn nhận (mặc định 1, tối đa 20)",
    )
    @app_commands.choices(role=ROLE_CHOICES)
    async def xin_deadline(
        self,
        interaction: discord.Interaction,
        role: app_commands.Choice[str],
        so_luong: app_commands.Range[int, 1, 20] = 1,
    ):
        """Xử lý lệnh /xin-deadline."""
        await interaction.response.defer()

        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"
        role_type = role.value
        role_name = role.name

        # Lấy deadline available trong Server này
        available = await get_available_deadlines(role_type, so_luong, guild_id=guild_id)

        if not available:
            return await interaction.followup.send(
                embed=create_error_embed(f"Đã hết deadline {role_name}!")
            )

        if len(available) < so_luong:
            return await interaction.followup.send(
                embed=create_error_embed(
                    f"Chỉ còn **{len(available)}** chap {role_name}! "
                    f"Bạn yêu cầu {so_luong} chap."
                )
            )

        deadline_ids = [d["id"] for d in available]

        # Đặt trạng thái pending
        await set_pending_deadlines(deadline_ids, str(interaction.user.id), guild_id=guild_id)

        # Tính deadline
        deadline_at = calculate_deadline(role_type, so_luong)
        total_days = calculate_total_days(role_type, so_luong)

        # Tạo embed preview
        embed = create_deadline_preview(
            available, role_type, deadline_at, interaction.user, total_days
        )

        # Tạo view với buttons
        view = ConfirmDeadlineView(
            deadline_ids=deadline_ids,
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            role_type=role_type,
            chap_count=so_luong,
            chapters=available,
            deadline_at=deadline_at,
            total_days=total_days,
            original_interaction=interaction,
            guild_id=guild_id,
        )

        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(XinDeadline(bot))
