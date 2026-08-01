"""
Cog xử lý lệnh /thongke
Hiển thị dashboard thống kê deadline và chi tiết theo từng role cho admin.
"""

from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands

from config import ROLE_CHOICES, ROLE_TYPES, is_admin
from database.queries import (
    get_stats,
    get_role_detailed_deadlines,
    get_all_detailed_deadlines,
)
from utils.embed_builder import (
    create_stats_embed,
    create_role_detail_embeds,
    create_error_embed,
)


class ThongKe(commands.Cog):
    """Cog xử lý lệnh thống kê deadline."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="thongke",
        description="Xem thống kê deadline tổng quan và chi tiết theo role (Admin)",
    )
    @app_commands.describe(
        role="Lọc chi tiết deadline theo vị trí cụ thể (tùy chọn)"
    )
    @app_commands.choices(role=ROLE_CHOICES)
    async def thongke(
        self,
        interaction: discord.Interaction,
        role: Optional[app_commands.Choice[str]] = None,
    ):
        """Lệnh xem thống kê tổng quan và chi tiết tất cả deadline (hoặc lọc theo vị trí)."""
        if not await is_admin(interaction):
            return await interaction.response.send_message(
                embed=create_error_embed("Bạn không có quyền sử dụng lệnh này!"),
                ephemeral=True,
            )

        await interaction.response.defer()
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"

        stats = await get_stats(guild_id=guild_id)
        stats_embed = create_stats_embed(stats)

        all_embeds = [stats_embed]

        if role is None:
            # Lấy tất cả chi tiết của các role
            all_deadlines = await get_all_detailed_deadlines(guild_id=guild_id)

            # Gom theo từng role_type
            deadlines_by_role = {}
            for d in all_deadlines:
                r_type = d.get("role_type")
                if r_type:
                    deadlines_by_role.setdefault(r_type, []).append(d)

            # Tạo embeds chi tiết cho các role có dữ liệu (theo thứ tự ROLE_TYPES)
            for r_type in ROLE_TYPES.keys():
                if r_type in deadlines_by_role:
                    role_name = ROLE_TYPES.get(r_type, {}).get("name", r_type)
                    r_embeds = create_role_detail_embeds(
                        r_type, role_name, deadlines_by_role[r_type]
                    )
                    all_embeds.extend(r_embeds)

            # Nếu có role nào khác chưa khai báo trong ROLE_TYPES
            for r_type, r_deadlines in deadlines_by_role.items():
                if r_type not in ROLE_TYPES:
                    r_embeds = create_role_detail_embeds(r_type, r_type, r_deadlines)
                    all_embeds.extend(r_embeds)
        else:
            # Xem chi tiết theo role được lọc
            role_type = role.value
            role_name = role.name
            role_deadlines = await get_role_detailed_deadlines(role_type, guild_id=guild_id)
            r_embeds = create_role_detail_embeds(role_type, role_name, role_deadlines)
            all_embeds.extend(r_embeds)

        # Gửi theo từng đợt tối đa 10 embeds (giới hạn Discord API)
        for i in range(0, len(all_embeds), 10):
            chunk = all_embeds[i : i + 10]
            await interaction.followup.send(embeds=chunk)


async def setup(bot: commands.Bot):
    await bot.add_cog(ThongKe(bot))


