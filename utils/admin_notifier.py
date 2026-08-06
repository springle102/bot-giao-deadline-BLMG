"""
Module xử lý thông báo nhật ký hoạt động cho Quản trị viên (Admin Action Notifier).
Gửi nhật ký hoạt động Admin vào Private Thread trong kênh giao deadline.
Chỉ thành viên có Role Admin mới thấy thread này.
"""

import discord
from typing import Optional
from config import DEADLINE_CHANNEL_ID, ROLE_TYPES
from database.queries import get_server_setting

# Tên Private Thread cố định
ADMIN_THREAD_NAME = "📋 Nhật Ký Admin"

ROLE_MENTION_NAMES = {
    "editfull": "EDIT",
    "clean": "CLEAN",
    "type_ko_sfx": "TYPE",
    "type_sfx": "TYPE",
}


async def _find_deadline_channel(
    guild: discord.Guild,
) -> Optional[discord.TextChannel]:
    """
    Tìm kênh giao deadline theo thứ tự ưu tiên:
    1. deadline_channel_id (cấu hình qua /cauhinh trong DB)
    2. DEADLINE_CHANNEL_ID (cấu hình trong .env)
    """
    setting = None
    try:
        setting = await get_server_setting(str(guild.id))
    except Exception as e:
        print(f"[ADMIN NOTIFIER] Lỗi đọc server_setting từ DB: {e}")

    # 1. Kênh deadline từ DB
    if setting and setting.get("deadline_channel_id"):
        try:
            channel = guild.get_channel(int(setting["deadline_channel_id"]))
            if channel and isinstance(channel, discord.TextChannel):
                return channel
        except (ValueError, TypeError):
            pass

    # 2. Fallback: .env
    if DEADLINE_CHANNEL_ID and str(DEADLINE_CHANNEL_ID).strip().isdigit():
        try:
            channel = guild.get_channel(int(DEADLINE_CHANNEL_ID))
            if channel and isinstance(channel, discord.TextChannel):
                return channel
        except (ValueError, TypeError):
            pass

    return None


async def _get_admin_role(guild: discord.Guild) -> Optional[discord.Role]:
    """Lấy Role Admin được cấu hình trong DB qua /cauhinh."""
    try:
        setting = await get_server_setting(str(guild.id))
        if setting and setting.get("admin_role_id"):
            role_id = int(setting["admin_role_id"])
            return guild.get_role(role_id)
    except Exception as e:
        print(f"[ADMIN NOTIFIER] Lỗi lấy admin role từ DB: {e}")
    return None


async def _get_or_create_admin_thread(
    channel: discord.TextChannel,
) -> Optional[discord.Thread]:
    """
    Tìm hoặc tạo Private Thread '📋 Nhật Ký Admin' trong kênh deadline.
    """
    # 1. Tìm trong danh sách thread đang active
    for thread in channel.threads:
        if thread.name == ADMIN_THREAD_NAME and thread.is_private():
            return thread

    # 2. Tìm trong archived threads
    try:
        async for thread in channel.archived_threads(private=True, limit=50):
            if thread.name == ADMIN_THREAD_NAME:
                # Unarchive thread bằng cách gửi tin nhắn (sẽ tự unarchive)
                return thread
    except discord.Forbidden:
        pass
    except Exception as e:
        print(f"[ADMIN NOTIFIER] Lỗi tìm archived threads: {e}")

    # 3. Tạo mới Private Thread
    try:
        thread = await channel.create_thread(
            name=ADMIN_THREAD_NAME,
            type=discord.ChannelType.private_thread,
            reason="Tự động tạo bởi Bot - Nhật ký Admin chỉ dành cho Role Quản lý",
        )
        return thread
    except discord.Forbidden:
        print(f"[ADMIN NOTIFIER] Bot không có quyền 'Create Private Threads' trong kênh #{channel.name}")
    except Exception as e:
        print(f"[ADMIN NOTIFIER] Lỗi tạo private thread: {e}")

    return None


async def _sync_admin_members_to_thread(
    thread: discord.Thread,
    guild: discord.Guild,
    admin_role: Optional[discord.Role],
) -> None:
    """Thêm tất cả thành viên có Role Admin vào Private Thread (nếu chưa có)."""
    if not admin_role:
        return

    # Lấy danh sách thành viên đã có trong thread
    try:
        existing_members = set()
        async for member in thread.fetch_members():
            existing_members.add(member.id)
    except Exception:
        existing_members = set()

    # Thêm các admin chưa có trong thread
    for member in admin_role.members:
        if member.bot:
            continue
        if member.id not in existing_members:
            try:
                await thread.add_user(member)
            except Exception:
                pass  # Bỏ qua nếu không thể thêm


async def notify_all_admins(
    guild: discord.Guild,
    embed: discord.Embed,
    actor: discord.User | discord.Member = None,
) -> None:
    """
    Gửi thông báo nhật ký hoạt động Admin vào Private Thread trong kênh deadline.
    Chỉ thành viên có Role Admin mới thấy thread này.
    """
    if not guild:
        return

    # 1. Tìm kênh deadline
    channel = await _find_deadline_channel(guild)
    if not channel:
        print(f"[ADMIN NOTIFIER] Không tìm thấy kênh deadline cho Server {guild.name}. "
              "Hãy dùng /cauhinh channel:#kênh để thiết lập.")
        return

    # 2. Tìm hoặc tạo Private Thread
    thread = await _get_or_create_admin_thread(channel)
    if not thread:
        print(f"[ADMIN NOTIFIER] Không thể tạo Private Thread trong kênh #{channel.name}")
        return

    # 3. Lấy Admin Role và đồng bộ thành viên vào thread
    admin_role = await _get_admin_role(guild)
    await _sync_admin_members_to_thread(thread, guild, admin_role)

    # 4. Gửi thông báo vào thread
    try:
        await thread.send(embed=embed)
    except discord.Forbidden:
        print(f"[ADMIN NOTIFIER] Bot không có quyền gửi tin nhắn vào thread '{ADMIN_THREAD_NAME}'")
    except Exception as e:
        print(f"[ADMIN NOTIFIER] Lỗi gửi thông báo vào thread: {e}")


def _find_role_in_collection(
    roles: list[discord.Role],
    role_type: str,
) -> Optional[discord.Role]:
    """Tìm role deadline theo tên trong một danh sách role Discord."""
    mention_name = ROLE_MENTION_NAMES.get(role_type)
    if not mention_name:
        return None

    normalized_name = mention_name.casefold()
    for role in roles:
        role_name = str(getattr(role, "name", "")).strip().lstrip("@").casefold()
        if role_name == normalized_name:
            return role
    return None


async def _find_role_for_deadline(
    guild: discord.Guild,
    role_type: str,
) -> Optional[discord.Role]:
    """Tìm role deadline, làm mới cache nếu role chưa có trong guild hiện tại."""
    mention_role = _find_role_in_collection(getattr(guild, "roles", []), role_type)
    if mention_role:
        return mention_role

    try:
        fetched_roles = await guild.fetch_roles()
    except (discord.Forbidden, discord.HTTPException) as e:
        print(f"[DEADLINE NOTIFIER] Không thể tải danh sách role để tag: {e}")
        return None
    except Exception as e:
        print(f"[DEADLINE NOTIFIER] Lỗi tải danh sách role để tag: {e}")
        return None

    return _find_role_in_collection(fetched_roles, role_type)


async def notify_new_deadline_role(
    guild: discord.Guild,
    role_type: str,
) -> None:
    """Thông báo công khai khi kho của role vừa chuyển từ hết chap sang có chap."""
    if not guild:
        return

    channel = await _find_deadline_channel(guild)
    if not channel:
        print(f"[DEADLINE NOTIFIER] Không tìm thấy kênh deadline cho Server {guild.name}.")
        return

    role_config = ROLE_TYPES.get(role_type, {})
    role_name = role_config.get("name", role_type)
    mention_role = await _find_role_for_deadline(guild, role_type)
    if not mention_role:
        mention_name = ROLE_MENTION_NAMES.get(role_type, role_type)
        print(
            f"[DEADLINE NOTIFIER] Không tìm thấy role '{mention_name}' trong Server {guild.name}; "
            "bỏ qua thông báo để không gửi @ giả."
        )
        return

    # Role.mention tạo đúng cú pháp Discord <@&ROLE_ID>, không phải chuỗi @ROLE.
    content = f"Đã có deadline mới cho role {role_name} {mention_role.mention}"

    try:
        await channel.send(
            content=content,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
    except discord.Forbidden:
        print(f"[DEADLINE NOTIFIER] Bot không có quyền gửi tin nhắn vào kênh #{channel.name}")
    except Exception as e:
        print(f"[DEADLINE NOTIFIER] Lỗi gửi thông báo chap mới: {e}")
