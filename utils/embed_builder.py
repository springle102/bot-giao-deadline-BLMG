"""
Embed Builder - Tạo các Discord Embed đẹp cho bot.
"""

import discord
from datetime import datetime
from config import ROLE_TYPES, COLOR_SUCCESS, COLOR_WARNING, COLOR_ERROR, COLOR_INFO, COLOR_PENDING
from utils.time_helper import format_deadline, format_remaining, get_deadline_status_emoji


def create_deadline_preview(
    chapters: list[dict],
    role_type: str,
    deadline_at: datetime,
    user: discord.Member,
    total_days: int,
) -> discord.Embed:
    """Tạo embed preview trước khi xác nhận nhận deadline."""
    role_name = ROLE_TYPES.get(role_type, {}).get("name", role_type)
    series_name = chapters[0].get("series_name", "Không xác định") if chapters else "Không xác định"

    embed = discord.Embed(
        title=f"📋 Xác nhận nhận Deadline - {role_name}",
        color=COLOR_PENDING,
    )

    embed.add_field(name="📚 Truyện", value=series_name, inline=True)
    embed.add_field(name="👤 Người nhận", value=user.mention, inline=True)
    embed.add_field(name="📖 Số chap", value=str(len(chapters)), inline=True)

    # Danh sách chap
    chap_list = " │ ".join(c.get("chapter_name", "?") for c in chapters)
    embed.add_field(
        name="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        value=chap_list,
        inline=False,
    )

    # Hạn nộp
    formatted = format_deadline(deadline_at)
    embed.add_field(
        name="⏰ Hạn nộp",
        value=f"{formatted} (**{total_days} ngày**)",
        inline=False,
    )

    embed.set_footer(text="Bấm ✅ để xác nhận │ Tự hủy sau 6 giờ")
    return embed


def create_deadline_confirm(
    chapters: list[dict],
    role_type: str,
    deadline_at: datetime,
    user: discord.Member,
    total_days: int,
) -> discord.Embed:
    """Tạo embed sau khi xác nhận nhận deadline."""
    role_name = ROLE_TYPES.get(role_type, {}).get("name", role_type)
    series_name = chapters[0].get("series_name", "Không xác định") if chapters else "Không xác định"

    embed = discord.Embed(
        title=f"✅ Đã giao Deadline - {role_name}",
        color=COLOR_SUCCESS,
    )

    embed.add_field(name="📚 Truyện", value=series_name, inline=True)
    embed.add_field(name="👤 Người nhận", value=user.mention, inline=True)
    embed.add_field(name="📖 Số chap", value=str(len(chapters)), inline=True)

    # Danh sách chap kèm link drive
    chap_lines = []
    for c in chapters:
        chap_name = c.get("chapter_name", "?")
        drive_link = c.get("drive_link")
        if drive_link:
            chap_lines.append(f"📖 {chap_name} — 🔗 [Link Drive]({drive_link})")
        else:
            chap_lines.append(f"📖 {chap_name} — *(chưa có link)*")

    embed.add_field(
        name="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        value="\n".join(chap_lines),
        inline=False,
    )

    # Hạn nộp
    formatted = format_deadline(deadline_at)
    embed.add_field(
        name="⏰ Hạn nộp",
        value=f"{formatted} (**{total_days} ngày**)",
        inline=False,
    )

    return embed


def get_current_month_str() -> str:
    """Trả về chuỗi hiển thị tháng hiện tại (ví dụ: 'Tháng 07/2026')."""
    return datetime.now().strftime("Tháng %m/%Y")


def create_deadline_list(deadlines: list[dict], user: discord.Member) -> discord.Embed:
    """Tạo embed danh sách deadline của user (nhóm theo batch nếu có)."""
    month_str = get_current_month_str()
    embed = discord.Embed(
        title=f"📋 Deadline của {user.display_name} — {month_str}",
        color=COLOR_INFO,
    )

    if not deadlines:
        embed.description = "Bạn chưa có deadline nào."
        return embed

    # Phân loại: gom theo batch_id và các chap lẻ
    batches = {}
    singles = []

    for d in deadlines:
        b_id = d.get("batch_id")
        if b_id:
            if b_id not in batches:
                batches[b_id] = []
            batches[b_id].append(d)
        else:
            singles.append(d)

    blocks = []

    # Xử lý các batch
    for b_id, b_deadlines in batches.items():
        role_type = b_deadlines[0].get("role_type", "")
        role_name = ROLE_TYPES.get(role_type, {}).get("name", "?")
        series = b_deadlines[0].get("series_name", "?")
        deadline_at = b_deadlines[0].get("deadline_at", "")

        emoji = get_deadline_status_emoji(deadline_at)
        remaining = format_remaining(deadline_at)

        chap_items = " │ ".join(f"**{d.get('chapter_name', '?')}**" for d in b_deadlines)
        
        block = (
            f"📦 **Batch {role_name}** ({len(b_deadlines)} chap)\n"
            f"   📚 Truyện: {series}\n"
            f"   📖 {chap_items}\n"
            f"   {emoji} Hạn chung: ⏰ {remaining}"
        )
        blocks.append(block)

    # Xử lý các chap lẻ
    for d in singles:
        deadline_at = d.get("deadline_at", "")
        emoji = get_deadline_status_emoji(deadline_at)
        remaining = format_remaining(deadline_at)
        role_name = ROLE_TYPES.get(d.get("role_type", ""), {}).get("name", "?")
        chap_name = d.get("chapter_name", "?")
        series = d.get("series_name", "?")

        block = f"{emoji} **{chap_name}** — {role_name}\n   📚 {series} │ ⏰ {remaining}"
        blocks.append(block)

    embed.description = "\n\n".join(blocks)
    return embed


def create_stats_embed(stats: dict) -> discord.Embed:
    """Tạo embed thống kê tổng quan."""
    month_str = get_current_month_str()
    embed = discord.Embed(
        title=f"📊 Thống kê Deadline — {month_str}",
        color=COLOR_INFO,
    )

    # Tổng quan
    embed.add_field(name="📁 Tổng", value=str(stats.get("total", 0)), inline=True)
    embed.add_field(name="🟢 Chưa giao", value=str(stats.get("available", 0)), inline=True)
    embed.add_field(name="🟡 Đã giao", value=str(stats.get("assigned", 0)), inline=True)
    embed.add_field(name="✅ Đã nộp", value=str(stats.get("submitted", 0)), inline=True)
    embed.add_field(name="🔴 Quá hạn", value=str(stats.get("overdue", 0)), inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)  # Spacer

    # Chi tiết theo role
    per_role = stats.get("per_role", {})
    if per_role:
        breakdown_lines = []
        for role_type, role_stats in per_role.items():
            role_name = ROLE_TYPES.get(role_type, {}).get("name", role_type)
            breakdown_lines.append(
                f"**{role_name}**\n"
                f"🟢 {role_stats.get('available', 0)} │ "
                f"🟡 {role_stats.get('assigned', 0)} │ "
                f"✅ {role_stats.get('submitted', 0)} │ "
                f"🔴 {role_stats.get('overdue', 0)}"
            )
        embed.add_field(
            name="━━━ Chi tiết theo role ━━━",
            value="\n\n".join(breakdown_lines),
            inline=False,
        )

    embed.set_footer(text=f"📅 {month_str} │ Dùng /thongke role:[vị trí] để xem chi tiết")
    return embed


def format_chapter_numbers_to_ranges(numbers: list) -> str:
    """Format danh sách số chap thành chuỗi dải chap gọn gàng (ví dụ: Chap 1-5, 8, 10-12)."""
    valid_nums = [n for n in numbers if isinstance(n, int)]
    if not valid_nums:
        if numbers:
            return "Chap " + ", ".join(str(n) for n in numbers)
        return "Chap (không xác định)"

    sorted_nums = sorted(set(valid_nums))
    ranges = []
    start = sorted_nums[0]
    end = sorted_nums[0]

    for num in sorted_nums[1:]:
        if num == end + 1:
            end = num
        else:
            if start == end:
                ranges.append(f"{start}")
            else:
                ranges.append(f"{start}-{end}")
            start = num
            end = num

    if start == end:
        ranges.append(f"{start}")
    else:
        ranges.append(f"{start}-{end}")

    return "Chap " + ", ".join(ranges)


def create_role_detail_embeds(
    role_type: str,
    role_name: str,
    deadlines: list[dict],
) -> list[discord.Embed]:
    """Tạo danh sách embed thống kê chi tiết theo role (gồm bộ truyện, chap đã giao & ai nhận, chap còn tồn)."""
    if not deadlines:
        embed = discord.Embed(
            title=f"📊 Thống Kê Chi Tiết — {role_name}",
            description=f"Hiện tại chưa có dữ liệu deadline nào cho vị trí **{role_name}**.",
            color=COLOR_INFO,
        )
        return [embed]

    total = len(deadlines)
    available = sum(1 for d in deadlines if d.get("status") == "available")
    assigned = sum(1 for d in deadlines if d.get("status") in ("assigned", "pending"))
    submitted = sum(1 for d in deadlines if d.get("status") == "submitted")

    # Group theo bộ truyện (series_name)
    series_dict = {}
    for d in deadlines:
        s_name = d.get("series_name", "Khác")
        series_dict.setdefault(s_name, []).append(d)

    embeds = []
    month_str = get_current_month_str()

    current_embed = discord.Embed(
        title=f"📊 Thống Kê Chi Tiết — {role_name} ({month_str})",
        description=(
            f"📁 **Tổng:** {total} chap  │  "
            f"🟢 **Còn tồn:** {available}  │  "
            f"🟡 **Đang làm:** {assigned}  │  "
            f"✅ **Đã nộp:** {submitted}"
        ),
        color=COLOR_INFO,
    )

    current_char_count = len(current_embed.title or "") + len(current_embed.description or "")

    for series_name, items in series_dict.items():
        assigned_items = [d for d in items if d.get("status") != "available"]
        available_items = [d for d in items if d.get("status") == "available"]

        assigned_lines = []
        user_groups = {}
        for d in assigned_items:
            user_key = (d.get("assigned_to"), d.get("assigned_username"), d.get("status"))
            user_groups.setdefault(user_key, []).append(d.get("chapter_number"))

        for (user_id, username, status), chap_nums in user_groups.items():
            chap_str = format_chapter_numbers_to_ranges(chap_nums)
            user_mention = f"<@{user_id}>" if user_id else f"@{username or 'Chưa rõ'}"

            status_str = ""
            if status == "submitted":
                status_str = " *(✅ Đã nộp)*"
            elif status == "pending":
                status_str = " *(⏳ Chờ xác nhận)*"

            assigned_lines.append(f"• {chap_str} — {user_mention}{status_str}")

        avail_nums = [d.get("chapter_number") for d in available_items]
        if avail_nums:
            avail_str = format_chapter_numbers_to_ranges(avail_nums)
            avail_text = f"• {avail_str} ({len(avail_nums)} chap)"
        else:
            avail_text = "• *(Hết chap tồn)*"

        field_value_parts = []
        field_value_parts.append("🟡 **Đã giao:**")
        if assigned_lines:
            field_value_parts.extend(assigned_lines)
        else:
            field_value_parts.append("• *(Chưa giao chap nào)*")

        field_value_parts.append("\n🟢 **Còn tồn (chưa giao):**")
        field_value_parts.append(avail_text)

        field_value = "\n".join(field_value_parts)
        if len(field_value) > 1024:
            field_value = field_value[:1000] + "\n... *(còn nữa)*"

        field_name = f"📚 Bộ truyện: {series_name}"
        if len(field_name) > 256:
            field_name = field_name[:250] + "..."

        field_length = len(field_name) + len(field_value)

        if len(current_embed.fields) >= 25 or (current_char_count + field_length > 5500):
            embeds.append(current_embed)
            current_embed = discord.Embed(
                title=f"📊 Thống Kê Chi Tiết — {role_name} (Tiếp)",
                color=COLOR_INFO,
            )
            current_char_count = len(current_embed.title or "")

        current_embed.add_field(name=field_name, value=field_value, inline=False)
        current_char_count += field_length

    embeds.append(current_embed)
    return embeds


def create_error_embed(message: str) -> discord.Embed:
    """Tạo embed thông báo lỗi."""
    return discord.Embed(
        title="❌ Lỗi",
        description=message,
        color=COLOR_ERROR,
    )


def create_success_embed(message: str) -> discord.Embed:
    """Tạo embed thành công."""
    return discord.Embed(
        title="✅ Thành công",
        description=message,
        color=COLOR_SUCCESS,
    )

