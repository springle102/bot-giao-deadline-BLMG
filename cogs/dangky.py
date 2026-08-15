"""
Cog xử lý lệnh /dangky, /xem-email (/xem-emaill) và /xoa-email (/xoa-mail)
Cho phép thành viên đăng ký Gmail và Quản trị viên quản lý danh sách email.
"""

import asyncio
import re
import discord
from discord import app_commands
from discord.ext import commands

from config import is_admin, COLOR_INFO
from database.queries import (
    get_all_user_emails,
    get_assigned_deadlines,
    get_user_email,
    delete_user_email,
    save_user_email,
)
from utils.embed_builder import create_success_embed, create_error_embed
from utils.admin_notifier import notify_all_admins
from utils.google_drive import (
    clean_drive_error_message,
    extract_drive_id,
    friendly_drive_error,
    grant_drive_permission,
    revoke_drive_permission,
)


EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
_DRIVE_EMAIL_UPDATE_LOCK = asyncio.Lock()


def _unique_drive_links(deadlines: list[dict]) -> list[str]:
    """Return one stored URL for each Drive item used by active deadlines."""
    links = []
    seen = set()
    for deadline in deadlines:
        raw_link = str(deadline.get("drive_link") or "").strip()
        if not raw_link:
            continue
        drive_key = extract_drive_id(raw_link) or raw_link
        if drive_key in seen:
            continue
        seen.add(drive_key)
        links.append(raw_link)
    return links


async def _resync_drive_permissions_for_email_change(
    old_email: str,
    new_email: str,
    deadlines: list[dict],
) -> tuple[bool, str]:
    """Move Drive access from the old email to the new email safely.

    The new permission is created first so a temporary Google API failure does
    not remove a member's existing access. The old permission is removed only
    after every new permission is ready. If cleanup fails, best-effort rollback
    restores the old permission and removes permissions created by this update.
    """
    links = _unique_drive_links(deadlines)
    if not links:
        return True, ""

    created_new_links: list[str] = []
    revoked_old_links: list[str] = []

    async with _DRIVE_EMAIL_UPDATE_LOCK:
        try:
            for link in links:
                try:
                    result = await asyncio.to_thread(
                        grant_drive_permission,
                        link,
                        new_email,
                        "writer",
                        True,
                    )
                except Exception as error:
                    raise RuntimeError(
                        friendly_drive_error(error, email=new_email, drive_url=link)
                    ) from error

                if not isinstance(result, tuple) or len(result) < 2:
                    raise RuntimeError(
                        "Google Drive trả về kết quả cấp quyền không hợp lệ."
                    )

                success, message = result[0], str(result[1])
                if success is not True:
                    raise RuntimeError(
                        clean_drive_error_message(
                            message,
                            email=new_email,
                            drive_url=link,
                        )
                    )

                # "Email ..." means the new email already had access, so it
                # must not be deleted if this operation later needs rollback.
                if not message.strip().lower().startswith("email "):
                    created_new_links.append(link)

            for link in links:
                try:
                    result = await asyncio.to_thread(
                        revoke_drive_permission,
                        link,
                        old_email,
                    )
                except Exception as error:
                    raise RuntimeError(
                        friendly_drive_error(error, email=old_email, drive_url=link)
                    ) from error

                if not isinstance(result, tuple) or len(result) < 2 or result[0] is not True:
                    message = result[1] if isinstance(result, tuple) and len(result) > 1 else str(result)
                    raise RuntimeError(
                        clean_drive_error_message(
                            str(message),
                            email=old_email,
                            drive_url=link,
                        )
                    )
                revoked_old_links.append(link)

            return True, f"Đã share lại {len(links)} link Drive"
        except Exception as error:
            # Re-establish old access before removing the new permissions. The
            # database is deliberately not updated when this operation fails.
            for link in reversed(revoked_old_links):
                try:
                    await asyncio.to_thread(
                        grant_drive_permission,
                        link,
                        old_email,
                        "writer",
                        True,
                    )
                except Exception as rollback_error:
                    print(
                        f"[DriveEmailUpdate] Không thể khôi phục email cũ cho link "
                        f"{extract_drive_id(link) or 'invalid-link'}: {rollback_error}",
                        flush=True,
                    )

            for link in reversed(created_new_links):
                try:
                    await asyncio.to_thread(revoke_drive_permission, link, new_email)
                except Exception as rollback_error:
                    print(
                        f"[DriveEmailUpdate] Không thể dọn email mới cho link "
                        f"{extract_drive_id(link) or 'invalid-link'}: {rollback_error}",
                        flush=True,
                    )

            return False, str(error)


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

        old_email = await get_user_email(user_id, guild_id=guild_id)
        old_email = old_email.strip().lower() if old_email else None

        if old_email and old_email != clean_email:
            active_deadlines = await get_assigned_deadlines(user_id, guild_id=guild_id)
            drive_links = _unique_drive_links(active_deadlines)

            if drive_links:
                await interaction.response.defer(ephemeral=True)
                sync_ok, sync_message = await _resync_drive_permissions_for_email_change(
                    old_email,
                    clean_email,
                    active_deadlines,
                )
                if not sync_ok:
                    return await interaction.followup.send(
                        embed=create_error_embed(
                            "❌ Không thể đổi email Drive lúc này. Email cũ vẫn được giữ "
                            f"(`{old_email}`), quyền Drive và deadline đang nhận không bị thay đổi.\n\n"
                            f"Chi tiết: {sync_message}"
                        ),
                        ephemeral=True,
                    )

                try:
                    await save_user_email(user_id, username, clean_email, guild_id=guild_id)
                except Exception as save_error:
                    print(
                        f"[DriveEmailUpdate] Không thể lưu email mới cho user {user_id}: "
                        f"{save_error}",
                        flush=True,
                    )
                    rollback_ok, rollback_message = (
                        await _resync_drive_permissions_for_email_change(
                            clean_email,
                            old_email,
                            active_deadlines,
                        )
                    )
                    rollback_note = (
                        "Quyền Drive cũ đã được khôi phục."
                        if rollback_ok
                        else "Không thể khôi phục hoàn toàn quyền Drive cũ; admin cần kiểm tra log."
                    )
                    return await interaction.followup.send(
                        embed=create_error_embed(
                            "❌ Không thể lưu email mới vào hệ thống. Email đăng ký cũ vẫn được giữ. "
                            f"{rollback_note}\n\n"
                            f"Chi tiết: {save_error}"
                            + (f" ({rollback_message})" if not rollback_ok else "")
                        ),
                        ephemeral=True,
                    )
                return await interaction.followup.send(
                    embed=create_success_embed(
                        f"✅ Đã share lại quyền Drive thành công với email mới: **{clean_email}**\n\n"
                        f"Đã thu hồi quyền của email cũ `{old_email}` trên **{len(drive_links)}** link Drive.\n"
                        "Các deadline bạn đang nhận vẫn được giữ nguyên."
                    ),
                    ephemeral=True,
                )

        await save_user_email(user_id, username, clean_email, guild_id=guild_id)

        if old_email and old_email != clean_email:
            message = (
                f"Đã cập nhật thành công địa chỉ email: **{clean_email}**\n\n"
                "Bạn hiện không có deadline đang nhận cần share lại quyền Drive."
            )
        else:
            message = (
                f"Đã lưu thành công địa chỉ email: **{clean_email}**\n\n"
                "💡 Mỗi khi bạn bấm nhận deadline bằng lệnh `/xin-dl`, "
                "bot sẽ tự động add email này vào Folder Google Drive tương ứng và gửi thông báo qua Gmail cho bạn."
            )

        embed = create_success_embed(
            message
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="xem-email",
        description="Xem danh sách tất cả email đã đăng ký của thành viên (Admin)",
    )
    async def xem_email(self, interaction: discord.Interaction):
        """Lệnh /xem-email dành cho Admin."""
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
                f"👑 **[Nhật Ký Quản Trị] Xóa Email Thành Viên**\n\n"
                f"• **Quản trị viên thực hiện:** {interaction.user.mention}\n"
                f"• **Thành viên bị xóa:** <@{del_uid}> (**{del_name}**)\n"
                f"• **Email đã xóa:** `{del_email}`\n\n"
                f"Thông tin email đã được xóa khỏi hệ thống."
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            if interaction.guild:
                await notify_all_admins(interaction.guild, embed, actor=interaction.user)
        else:
            embed = create_error_embed(
                f"❌ Không tìm thấy dữ liệu email đăng ký tương ứng với `{target_identifier}` trong hệ thống!"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DangKy(bot))
