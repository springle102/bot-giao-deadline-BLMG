"""
Cog xử lý lệnh /help hiển thị danh sách tất cả các lệnh của bot.
"""

import discord
from discord import app_commands
from discord.ext import commands
from config import COLOR_INFO


class HelpCog(commands.Cog):
    """Cog hiển thị danh sách hướng dẫn sử dụng bot."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="help",
        description="Xem danh sách tất cả các lệnh hướng dẫn sử dụng bot",
    )
    async def help_command(self, interaction: discord.Interaction):
        """Hiển thị bảng trợ giúp."""
        await interaction.response.defer()

        from utils.embed_builder import get_current_month_str
        month_str = get_current_month_str()

        embed = discord.Embed(
            title=f"📖 Hướng Dẫn Sử Dụng Bot Giao Deadline — {month_str}",
            description="Dưới đây là danh sách toàn bộ lệnh slash commands (`/`) của bot:",
            color=COLOR_INFO,
        )

        # Lệnh dành cho Thành Viên
        user_commands = (
            "• `/dangky [email]` : Đăng ký/cập nhật Gmail để tự động nhận quyền Drive\n"
            "• `/xin-dl [role] [so-luong]` : Xin nhận deadline (Nút ✅ Xác nhận)\n"
            "• `/xin-tre-dl [chap] [so-gio] [truyen]` : Xin gia hạn/trễ deadline (tối đa 12 tiếng)\n"
            "• `/xem-dl` : Xem deadline bạn đang làm và đã nộp\n"
            "• `/nop-dl [chap] [truyen]` : Nộp 1 chap (ô `truyen` tùy chọn khi bị trùng số chap)\n"
            "• `/nop-dl-all` : Nộp tất cả deadline hiện có"
        )
        embed.add_field(
            name="👥 Lệnh Cho Thành Viên",
            value=user_commands,
            inline=False,
        )

        # Lệnh dành cho Admin
        admin_commands = (
            "• `/cauhinh [channel] [role]` : Thiết lập Kênh thông báo và Role Quản lý cho Server\n"
            "• `/xem-cauhinh` : Xem Kênh thông báo và Role Quản lý đang cấu hình của Server\n"
            "• `/add-dl [truyen] [role] [chap-bat-dau] [chap-ket-thuc] [drive-link]` : Thêm nhiều chap chung 1 link\n"
            "• `/add-dl-list [truyen] [role]` : Thêm hàng loạt chap với **link riêng từng chap**\n"
            "• `/add-dl-single [truyen] [role] [chap] [drive-link]` : Thêm 1 chap lẻ\n"
            "• `/huy-dl [user] [chap] [truyen]` : Admin hủy hàng loạt chap/truyện đã giao của thành viên\n"
            "• `/xoa-dl [truyen] [chap] [role]` : Admin xóa hàng loạt deadline chưa giao trong kho\n"
            "• `/xem-email` : Admin xem danh sách tất cả email thành viên đã đăng ký\n"
            "• `/xoa-email [user]` : Admin xóa email của 1 thành viên khi out team\n"
            "• `/deploy` : Kích hoạt deploy phiên bản mới lên Render (chỉ ADMIN_USER_ID)\n"
            "• `/thongke` : Xem dashboard thống kê; sau khi chạy lệnh, dùng 2 dropdown để lọc theo role và trạng thái\n"
            "• `/reset-dl [mode]` : Reset trạng thái hoặc xóa toàn bộ deadline để bắt đầu đợt mới"
        )
        embed.add_field(
            name="👑 Lệnh Cho Quản Lí / Admin",
            value=admin_commands,
            inline=False,
        )

        # Thông tin thêm về quy tắc hạn nộp
        rules_info = (
            "• **Edit Full Manhwa**: 2 ngày/chap\n"
            "• **Clear Full SFX**: 1 ngày/chap\n"
            "• **Type không SFX**: 2 chap/ngày (làm tròn lên ngày)\n"
            "• **Type mỗi SFX**: 2 chap/ngày (làm tròn lên ngày)\n"
            "⏰ *Nút bấm xin deadline tự hủy sau 6 giờ nếu không bấm xác nhận.*"
        )
        embed.add_field(
            name="⏱️ Quy Tắc Hạn Nộp (Deadline)",
            value=rules_info,
            inline=False,
        )

        embed.set_footer(text=f"📅 {month_str} │ Gõ / để chọn các lệnh trực tiếp trên Discord")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
