"""
Cog xử lý lệnh /nop-deadline và /nop-deadline-all
Cho phép user nộp deadline đã hoàn thành.
"""

import discord
from discord.ext import commands
from discord import app_commands

from database.queries import (
    mark_submitted,
    mark_all_submitted,
    get_assigned_deadlines,
    get_deadline_by_chap_and_user,
)
from utils.embed_builder import create_success_embed, create_error_embed
from utils.chapter_helper import parse_chapter_input, chapter_number_to_display


async def _revoke_drive_access_for_completed_deadlines(
    user_id: str,
    deadlines: list[dict],
    guild_id: str,
) -> list[str]:
    """Thu hồi quyền Drive của các chap done, nhưng giữ quyền nếu còn chap dùng chung link."""
    if not deadlines:
        return []

    import asyncio
    from database.queries import check_user_active_drive_link, get_user_email
    from utils.google_drive import friendly_drive_error, revoke_drive_permission

    user_email = await get_user_email(user_id, guild_id=guild_id)
    if not user_email:
        return []
    user_email = user_email.strip().lower()

    drive_links = {
        str(deadline.get("drive_link")).strip()
        for deadline in deadlines
        if deadline.get("drive_link") and str(deadline.get("drive_link")).strip()
    }
    status_lines = []

    for link in drive_links:
        try:
            still_active = await check_user_active_drive_link(
                user_id,
                link,
                guild_id=guild_id,
            )
            if still_active:
                status_lines.append(
                    "• ℹ️ Giữ quyền Drive vì bạn vẫn còn chap khác đang dùng chung folder/link này."
                )
                continue

            _, message = await asyncio.to_thread(
                revoke_drive_permission,
                link,
                user_email,
            )
            status_lines.append(f"• {message}")
        except Exception as drive_error:
            print(f"[ERROR] Lỗi thu hồi Drive sau khi báo done: {drive_error}")
            status_lines.append(
                f"• ⚠️ Không thể thu hồi quyền Drive: "
                f"{friendly_drive_error(drive_error, email=user_email, drive_url=link)}"
            )

    return status_lines


class NopDeadline(commands.Cog):
    """Cog xử lý lệnh nộp deadline."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="nop-dl",
        description="Nộp deadline một chương cụ thể",
    )
    @app_commands.describe(
        chap="Số chương cần nộp (ví dụ: 10 hoặc NT1 cho ngoại truyện)",
        truyen="Tên bộ truyện (tùy chọn, cần thiết nếu bạn có trùng số chap ở nhiều bộ truyện)"
    )
    async def nop_deadline(self, interaction: discord.Interaction, chap: str, truyen: str = None):
        """Nộp 1 chương cụ thể."""
        await interaction.response.defer()

        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"
        
        parsed = parse_chapter_input(chap)
        if parsed is None:
            return await interaction.followup.send(
                embed=create_error_embed(
                    "Số chương không hợp lệ! Nhập số (ví dụ: `10`) hoặc ngoại truyện (ví dụ: `NT1`)."
                )
            )
        chapter_number, chapter_display = parsed

        # Nếu không cung cấp tên truyện, kiểm tra xem người dùng có bị trùng chap X ở nhiều bộ không
        if not truyen:
            from database.queries import get_assigned_deadlines_by_chap
            matches = await get_assigned_deadlines_by_chap(chapter_number, user_id, guild_id=guild_id)
            if len(matches) > 1:
                series_list = ", ".join(f"**{m['series_name']}**" for m in matches)
                return await interaction.followup.send(
                    embed=create_error_embed(
                        f"Bạn đang nhận **{chapter_display}** ở nhiều bộ truyện khác nhau ({series_list})!\n"
                        f"Vui lòng điền thêm ô `truyen` trong lệnh để nộp đúng bộ. Ví dụ:\n"
                        f"`/nop-dl chap:{chap} truyen:{matches[0]['series_name']}`"
                    )
                )
            deadline = matches[0] if matches else None
        else:
            deadline = await get_deadline_by_chap_and_user(chapter_number, user_id, series_name=truyen, guild_id=guild_id)

        if not deadline:
            search_info = f" bộ **{truyen}**" if truyen else ""
            return await interaction.followup.send(
                embed=create_error_embed(
                    f"Không tìm thấy deadline {chapter_display}{search_info} được giao cho bạn "
                    f"hoặc đã nộp rồi!"
                )
            )

        batch_id = deadline.get("batch_id")
        success = await mark_submitted(deadline["id"], user_id, guild_id=guild_id)
        if success:
            drive_status_lines = await _revoke_drive_access_for_completed_deadlines(
                user_id,
                [deadline],
                guild_id,
            )
            if batch_id:
                from database.queries import get_batch_progress
                from utils.time_helper import format_remaining, format_deadline
                from datetime import datetime

                progress = await get_batch_progress(batch_id, guild_id=guild_id)
                if progress:
                    total = progress["total"]
                    submitted = progress["submitted"]
                    remaining_items = progress["remaining"]
                    dl_at = progress["deadline_at"]

                    embed = discord.Embed(
                        title=f"📝 Đã nộp thành công {chapter_display}!",
                        color=0x00FF88,
                    )
                    embed.add_field(
                        name="📊 Tiến độ batch",
                        value=f"**{submitted}/{total}** chap đã nộp",
                        inline=False,
                    )

                    if remaining_items:
                        chap_names = ", ".join(c["chapter_name"] for c in remaining_items)
                        embed.add_field(
                            name="📖 Các chap còn lại",
                            value=chap_names,
                            inline=False,
                        )
                        if dl_at:
                            dt = datetime.fromisoformat(dl_at)
                            embed.add_field(
                                name="⏰ Hạn nộp chung",
                                value=f"{format_deadline(dt)} (Còn **{format_remaining(dt)}**)",
                                inline=False,
                            )
                    else:
                        embed.description = "🎉 **Chúc mừng! Bạn đã hoàn thành tất cả các chap trong batch này!**"

                    if drive_status_lines:
                        embed.add_field(
                            name="📧 Thu hồi quyền Google Drive",
                            value="\n".join(drive_status_lines),
                            inline=False,
                        )

                    return await interaction.followup.send(embed=embed)

            embed = create_success_embed(f"📝 Đã nộp thành công {chapter_display}!")
            if drive_status_lines:
                embed.add_field(
                    name="📧 Thu hồi quyền Google Drive",
                    value="\n".join(drive_status_lines),
                    inline=False,
                )
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(
                embed=create_error_embed("Có lỗi xảy ra khi nộp deadline!")
            )

    @app_commands.command(
        name="nop-dl-all",
        description="Nộp tất cả deadline hiện có của bạn",
    )
    async def nop_deadline_all(self, interaction: discord.Interaction):
        """Nộp tất cả deadline."""
        await interaction.response.defer()

        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"
        assigned_deadlines = await get_assigned_deadlines(user_id, guild_id=guild_id)
        count = await mark_all_submitted(user_id, guild_id=guild_id)

        if count > 0:
            drive_status_lines = await _revoke_drive_access_for_completed_deadlines(
                user_id,
                assigned_deadlines,
                guild_id,
            )
            embed = create_success_embed(
                f"📝 Đã nộp thành công **{count}** deadline!"
            )
            if drive_status_lines:
                embed.add_field(
                    name="📧 Thu hồi quyền Google Drive",
                    value="\n".join(drive_status_lines),
                    inline=False,
                )
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(
                embed=create_error_embed("Bạn không có deadline nào cần nộp!")
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(NopDeadline(bot))
