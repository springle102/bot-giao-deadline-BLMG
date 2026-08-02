"""
Module xử lý thông báo nhật ký hoạt động cho Quản trị viên (Admin Action Notifier).
Đảm bảo tất cả người có Role Admin/Quản lý đều xem được các thao tác của Quản trị viên khác,
đồng thời bảo mật thông tin với thành viên thường (mem).
"""

import discord
from typing import List, Set
from config import get_admin_identifiers
from database.queries import get_server_setting


async def get_admin_members(guild: discord.Guild) -> List[discord.Member]:
    """
    Lấy danh sách tất cả các thành viên có quyền Admin / Role Quản lý trong Server.
    """
    if not guild:
        return []

    admin_members: Set[discord.Member] = set()

    # 1. Chủ Server
    if guild.owner:
        admin_members.add(guild.owner)

    # 2. Lấy role_id từ DB cấu hình server
    cfg_role_id = None
    try:
        setting = await get_server_setting(str(guild.id))
        if setting and setting.get("admin_role_id"):
            cfg_role_id = str(setting["admin_role_id"]).strip()
    except Exception as e:
        print(f"[ADMIN NOTIFIER] Lỗi đọc DB server_setting: {e}")

    admin_identifiers = get_admin_identifiers()

    for member in guild.members:
        if member.bot:
            continue

        # Check Administrator permission
        if getattr(member.guild_permissions, "administrator", False):
            admin_members.add(member)
            continue

        user_id_str = str(member.id)
        if user_id_str in admin_identifiers:
            admin_members.add(member)
            continue

        roles = getattr(member, "roles", [])
        for role in roles:
            role_id_str = str(role.id)
            role_name_str = role.name.strip()

            if cfg_role_id and role_id_str == cfg_role_id:
                admin_members.add(member)
                break

            if (
                role_id_str in admin_identifiers
                or role_name_str in admin_identifiers
                or role_name_str.lower() in admin_identifiers
            ):
                admin_members.add(member)
                break

    return list(admin_members)


async def notify_all_admins(
    guild: discord.Guild,
    embed: discord.Embed,
    actor: discord.User | discord.Member = None,
) -> None:
    """
    Gửi thông báo nhật ký hoạt động Admin đến tất cả Quản trị viên trong Server:
    1. Gửi vào Kênh Nhật Ký Quản Trị riêng của Server (nếu có cấu hình `admin_log_channel_id` trong DB).
    2. Gửi tin nhắn DM riêng cho từng Quản trị viên có role/quyền Admin trong Server.
    Member thường sẽ không bao giờ xem được các tin nhắn/DM này.
    """
    if not guild:
        return

    guild_id = str(guild.id)
    setting = None
    try:
        setting = await get_server_setting(guild_id)
    except Exception as e:
        print(f"[ADMIN NOTIFIER] Lỗi đọc server_setting: {e}")

    # 1. Gửi vào Kênh Nhật Ký Quản Trị riêng (admin_log_channel_id) nếu được thiết lập
    if setting and setting.get("admin_log_channel_id"):
        try:
            log_channel_id = int(setting["admin_log_channel_id"])
            log_channel = guild.get_channel(log_channel_id)
            if log_channel and isinstance(log_channel, discord.TextChannel):
                await log_channel.send(embed=embed)
        except Exception as e:
            print(f"[ADMIN NOTIFIER] Không thể gửi log vào kênh admin_log {setting.get('admin_log_channel_id')}: {e}")

    # 2. Gửi DM tới tất cả các Quản trị viên trong Server (role được set sử dụng lệnh admin)
    admins = await get_admin_members(guild)
    for admin in admins:
        try:
            await admin.send(embed=embed)
        except Exception:
            # Bỏ qua nếu Admin đóng DM cá nhân
            pass
