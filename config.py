"""
Cấu hình cho Discord Bot Giao Deadline.
Định nghĩa các role types, thời hạn, và choices cho slash commands.
"""

import os
import discord
from dotenv import load_dotenv

load_dotenv()

# ── Discord Config ──────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
GUILD_ID = os.getenv("GUILD_ID")
ADMIN_ROLE_ID = os.getenv("ADMIN_ROLE_ID")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
DEADLINE_CHANNEL_ID = os.getenv("DEADLINE_CHANNEL_ID")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")


def get_admin_identifiers() -> set[str]:
    """
    Phân tích ADMIN_ROLE_ID và ADMIN_USER_ID từ file .env.
    Hỗ trợ:
    - Nhiều ID hoặc Tên Role phân cách bằng dấu phẩy (,), dấu chấm phẩy (;), hoặc khoảng trắng.
    - Loại bỏ comment inline (`# ...`).
    - Cắt bỏ dấu ngoặc (`"`, `'`) và khoảng trắng thừa.
    """
    identifiers = set()
    for env_var in ["ADMIN_ROLE_ID", "ADMIN_USER_ID"]:
        raw_val = os.getenv(env_var, "")
        if not raw_val:
            continue
        # Bỏ inline comment
        clean_raw = raw_val.split("#")[0]
        # Thay thế dấu phẩy và chấm phẩy bằng khoảng trắng
        clean_raw = clean_raw.replace(",", " ").replace(";", " ")
        for token in clean_raw.split():
            item = token.strip().strip("\"'").strip()
            if item:
                identifiers.add(item)
                identifiers.add(item.lower())
    return identifiers


def is_admin(interaction: discord.Interaction) -> bool:
    """
    Kiểm tra quyền Admin của người dùng:
    1. Trả về True nếu user là Guild Owner (Chủ server Discord).
    2. Trả về True nếu user có quyền Administrator hệ thống Discord.
    3. Trả về True nếu User ID hoặc Role ID/Name nằm trong cấu hình ADMIN_ROLE_ID / ADMIN_USER_ID của .env.
    """
    user = interaction.user
    if not user:
        return False

    # 1. Kiểm tra nếu là Chủ Server Discord
    if interaction.guild and interaction.guild.owner_id == user.id:
        return True

    # Lấy đối tượng Member nếu user đang là discord.User
    member = user
    if interaction.guild and not isinstance(member, discord.Member):
        member = interaction.guild.get_member(user.id) or member

    # 2. Kiểm tra quyền Administrator hệ thống Discord
    guild_perms = getattr(member, "guild_permissions", None)
    if guild_perms and getattr(guild_perms, "administrator", False):
        return True

    # 3. Phân tích các ID / Tên được cấu hình trong .env
    admin_identifiers = get_admin_identifiers()
    if not admin_identifiers:
        print(f"[PERMISSION CHECK] ⚠️ Không có ADMIN_ROLE_ID/ADMIN_USER_ID trong .env và User {user} không phải Discord Admin.")
        return False

    user_id_str = str(user.id)

    # 3a. Kiểm tra theo User ID của người gọi lệnh
    if user_id_str in admin_identifiers:
        return True

    # 3b. Kiểm tra danh sách Role của member (theo Role ID và Role Name)
    roles = getattr(member, "roles", [])
    for role in roles:
        role_id_str = str(role.id)
        role_name_str = role.name.strip()
        if (
            role_id_str in admin_identifiers
            or role_name_str in admin_identifiers
            or role_name_str.lower() in admin_identifiers
        ):
            return True

    # In log debug ra terminal để dễ dàng kiểm tra khi permission check bị từ chối
    role_info = [f"{r.name}({r.id})" for r in roles] if isinstance(roles, list) and roles else "Không có roles"
    print(f"[PERMISSION DENIED] User: {user} (ID: {user_id_str}) | User Roles: {role_info} | Configured Identifiers: {list(admin_identifiers)}")

    return False


# ── Role Types ──────────────────────────────────────────────────
# Định nghĩa các loại role và quy tắc deadline tương ứng
ROLE_TYPES = {
    "editfull": {
        "name": "Edit Full Manhwa",
        "days_per_chap": 2,       # 2 ngày / chap
        "group_size": 1,          # Nhắc từng chap
        "reminder_unit": "chap",  # Đơn vị nhắc nhở
    },
    "clean": {
        "name": "Clean Full SFX",
        "days_per_chap": 1,       # 1 ngày / chap
        "group_size": 1,
        "reminder_unit": "chap",
    },
    "type_ko_sfx": {
        "name": "Type không SFX",
        "days_per_chap": 0.5,     # 2 chap / ngày
        "group_size": 2,          # Gộp 2 chap = 1 ngày khi nhắc
        "reminder_unit": "day",   # Nhắc theo ngày (không nhắc theo 0.5 ngày)
    },
    "type_sfx": {
        "name": "Type mỗi SFX",
        "days_per_chap": 0.5,     # 2 chap / ngày
        "group_size": 2,
        "reminder_unit": "day",
    },
}

# ── Slash Command Choices ───────────────────────────────────────
# Các lựa chọn role trong slash commands
ROLE_CHOICES = [
    discord.app_commands.Choice(name="Edit Full Manhwa", value="editfull"),
    discord.app_commands.Choice(name="Clean Full SFX", value="clean"),
    discord.app_commands.Choice(name="Type không SFX", value="type_ko_sfx"),
    discord.app_commands.Choice(name="Type mỗi SFX", value="type_sfx"),
]

# ── Embed Colors ────────────────────────────────────────────────
COLOR_SUCCESS = 0x00FF88     # Xanh lá - thành công
COLOR_WARNING = 0xFFAA00     # Vàng - cảnh báo
COLOR_ERROR = 0xFF4444       # Đỏ - lỗi / quá hạn
COLOR_INFO = 0x5865F2        # Xanh Discord - thông tin
COLOR_PENDING = 0xFFA500     # Cam - đang chờ xác nhận

# ── Timing Config ───────────────────────────────────────────────
CONFIRM_TIMEOUT_SECONDS = 21600    # Thời gian chờ xác nhận button (6 giờ = 21600 giây)
PENDING_EXPIRE_MINUTES = 360       # Tự hủy pending trong DB sau 6 giờ (360 phút)
REMINDER_CHECK_HOURS = 1           # Check nhắc nhở mỗi 1 giờ
REMINDER_THRESHOLD_HOURS = 24      # Nhắc khi còn ≤ 24 giờ
