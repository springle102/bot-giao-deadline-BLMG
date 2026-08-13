"""
Cog xử lý lệnh /reset-dl
Cho phép admin reset hoặc xóa toàn bộ dữ liệu deadline để bắt đầu chu kỳ thống kê mới (tuần/tháng mới).
"""

import discord
from discord import app_commands
from discord.ext import commands

from config import is_admin
from database.queries import reset_all_deadlines, reset_deadlines_status
from utils.embed_builder import create_error_embed, create_success_embed
from utils.admin_notifier import notify_all_admins


RESET_CHOICES = [
    app_commands.Choice(
        name="🧹 Xóa toàn bộ (Clear All - Xóa toàn bộ chap & lịch sử)",
        value="xoa_toan_bo",
    ),
    app_commands.Choice(
        name="🔄 Reset trạng thái (Reset Status - Giữ lại chap, đưa về chưa giao)",
        value="reset_trang_thai",
    ),
]


class ConfirmResetView(discord.ui.View):
    """View xác nhận trước khi reset dữ liệu."""

    def __init__(self, mode: str, user_id: int, guild_id: str = "global"):
        super().__init__(timeout=60)
        self.mode = mode
        self.user_id = user_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ Bạn không có quyền tương tác với nút này!", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Xác Nhận Reset", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for btn in self.children:
            btn.disabled = True

        await interaction.response.defer()

        if self.mode == "xoa_toan_bo":
            count = await reset_all_deadlines(guild_id=self.guild_id)
            embed = create_success_embed(
                f"👑 **[Nhật Ký Quản Trị] Reset Dữ Liệu System (Xóa Toàn Bộ)**\n\n"
                f"• **Quản trị viên thực hiện:** {interaction.user.mention}\n"
                f"• **Số chap đã xóa:** **{count}** chap và toàn bộ lịch sử giao deadline.\n"
                f"💡 *Lưu ý: Danh sách Email đăng ký của thành viên được bảo lưu nguyên vẹn (không bị xóa).*\n"
                f"Hệ thống đã sẵn sàng cho đợt nhập deadline mới."
            )
        else:
            count = await reset_deadlines_status(guild_id=self.guild_id)
            embed = create_success_embed(
                f"👑 **[Nhật Ký Quản Trị] Reset Dữ Liệu System (Reset Trạng Thái)**\n\n"
                f"• **Quản trị viên thực hiện:** {interaction.user.mention}\n"
                f"• **Số chap đã reset:** **{count}** chap đưa về trạng thái **🟢 Chưa giao (Available)**.\n"
                f"💡 *Lưu ý: Danh sách Email đăng ký của thành viên được bảo lưu nguyên vẹn (không bị xóa).*\n"
                f"Toàn bộ phân công cũ đã được làm mới."
            )

        await interaction.edit_original_response(embed=embed, view=self)
        if interaction.guild:
            await notify_all_admins(interaction.guild, embed, actor=interaction.user)
        self.stop()

    @discord.ui.button(label="Hủy Bỏ", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for btn in self.children:
            btn.disabled = True

        embed = create_error_embed("Đã hủy thao tác reset dữ liệu.")
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


class ResetDataCog(commands.Cog):
    """Cog xử lý lệnh reset dữ liệu deadline (Dành cho Admin)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="reset-dl",
        description="Reset hoặc xóa dữ liệu deadline để bắt đầu đợt mới (Admin)",
    )
    @app_commands.describe(
        mode="Chọn chế độ reset dữ liệu (Xóa toàn bộ hoặc Reset trạng thái)"
    )
    @app_commands.choices(mode=RESET_CHOICES)
    async def reset_dl(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ):
        """Lệnh reset deadline."""
        if not await is_admin(interaction):
            return await interaction.response.send_message(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"
        mode_value = mode.value
        mode_label = mode.name

        embed = discord.Embed(
            title="⚠️ Cảnh Báo Reset Dữ Liệu Deadline",
            description=(
                f"Bạn đang chuẩn bị thực hiện: **{mode_label}** cho Server hiện tại.\n\n"
                "⚠️ Hành động này sẽ thay đổi/xóa toàn bộ danh sách chap & deadline.\n"
                "📧 **Danh sách Email đăng ký của thành viên sẽ ĐƯỢC GIỮ NGUYÊN (Không bị xóa).**\n\n"
                "Bạn có chắc chắn muốn tiếp tục không?"
            ),
            color=0xFF4444,
        )

        view = ConfirmResetView(mode=mode_value, user_id=interaction.user.id, guild_id=guild_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ResetDataCog(bot))
