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
CLIENT_ID = os.getenv("CLIENT_ID")  # TODO: Chưa sử dụng, dùng cho invite link trong tương lai
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


async def is_admin(interaction: discord.Interaction) -> bool:
    """
    Kiểm tra quyền Admin của người dùng:
    1. Trả về True nếu user là Guild Owner (Chủ server Discord).
    2. Trả về True nếu user có quyền Administrator hệ thống Discord.
    3. Trả về True nếu user sở hữu Role được cấu hình riêng trong Database (/cauhinh) của Server đó.
    4. Trả về True nếu User ID hoặc Role ID/Name nằm trong cấu hình ADMIN_ROLE_ID / ADMIN_USER_ID của .env.
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

    roles = getattr(member, "roles", [])

    # 3. Kiểm tra cấu hình riêng của Server từ Database (/cauhinh)
    if interaction.guild_id:
        try:
            from database.queries import get_server_setting
            setting = await get_server_setting(str(interaction.guild_id))
            if setting and setting.get("admin_role_id"):
                cfg_role_id = str(setting["admin_role_id"]).strip()
                if any(str(r.id) == cfg_role_id for r in roles):
                    return True
        except Exception as e:
            print(f"[PERMISSION CHECK] Lỗi đọc server_setting từ DB: {e}")

    # 4. Phân tích các ID / Tên được cấu hình trong .env (Fallback)
    admin_identifiers = get_admin_identifiers()
    if admin_identifiers:
        user_id_str = str(user.id)
        if user_id_str in admin_identifiers:
            return True

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
    print(f"[PERMISSION DENIED] User: {user} (ID: {user.id}) | User Roles: {role_info} | Configured Identifiers: {list(admin_identifiers)}")

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
REMINDER_CHECK_MINUTES = 10        # Check nhắc nhở mỗi 10 phút
REMINDER_THRESHOLD_HOURS = 6       # Nhắc khi còn ≤ 6 giờ (các mốc 6h và 3h)
MAX_EXTENSION_HOURS = 12           # Thời gian xin trễ deadline tối đa (12 giờ)
DRIVE_SHARE_FAILURE_COOLDOWN_HOURS = 4  # Tạm tránh link Drive lỗi trong 4 giờ
DRIVE_SHARE_TIMEOUT_SECONDS = 30  # Thời gian tối đa chờ cấp quyền Drive khi xác nhận
DRIVE_FAILURE_RECHECK_MINUTES = 30  # Tự kiểm tra link Drive bị block mỗi 30 phút
