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
    series_names = list(dict.fromkeys(c.get("series_name", "Không xác định") for c in chapters))
    series_display = ", ".join(series_names) if series_names else "Không xác định"

    embed = discord.Embed(
        title=f"📋 Xác nhận nhận Deadline - {role_name}",
        color=COLOR_PENDING,
    )

    embed.add_field(name="📚 Truyện", value=series_display, inline=True)
    embed.add_field(name="👤 Người nhận", value=user.mention, inline=True)
    embed.add_field(name="📖 Số chap", value=str(len(chapters)), inline=True)

    # Danh sách chap với tên bộ truyện kế bên
    chap_lines = [
        f"📖 **{c.get('series_name', 'Không rõ')}** - {c.get('chapter_name', '?')}"
        for c in chapters
    ]
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
    series_names = list(dict.fromkeys(c.get("series_name", "Không xác định") for c in chapters))
    series_display = ", ".join(series_names) if series_names else "Không xác định"

    embed = discord.Embed(
        title=f"✅ Đã giao Deadline - {role_name}",
        color=COLOR_SUCCESS,
    )

    embed.add_field(name="📚 Truyện", value=series_display, inline=True)
    embed.add_field(name="👤 Người nhận", value=user.mention, inline=True)
    embed.add_field(name="📖 Số chap", value=str(len(chapters)), inline=True)

    # Danh sách chap kèm tên bộ truyện và link drive
    chap_lines = []
    for c in chapters:
        s_name = c.get("series_name", "Không rõ")
        chap_name = c.get("chapter_name", "?")
        drive_link = c.get("drive_link")
        if drive_link:
            chap_lines.append(f"📖 **{s_name}** - {chap_name} — 🔗 [Link Drive]({drive_link})")
        else:
            chap_lines.append(f"📖 **{s_name}** - {chap_name} — *(chưa có link)*")

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
        series_names = list(dict.fromkeys(d.get("series_name", "?") for d in b_deadlines))
        series_display = ", ".join(series_names)
        deadline_at = b_deadlines[0].get("deadline_at", "")

        emoji = get_deadline_status_emoji(deadline_at)
        remaining = format_remaining(deadline_at)

        chap_items = " │ ".join(f"**{d.get('series_name', '?')}** - {d.get('chapter_name', '?')}" for d in b_deadlines)
        
        block = (
            f"📦 **Batch {role_name}** ({len(b_deadlines)} chap)\n"
            f"   📚 Truyện: {series_display}\n"
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


def format_chapter_numbers_to_ranges(numbers: list) -> str:
    """Format danh sách số chap thành chuỗi dải chap gọn gàng (ví dụ: Chap 1-5, 8, NT1-NT3)."""
    valid_nums = [n for n in numbers if isinstance(n, int)]
    if not valid_nums:
        if numbers:
            return "Chap " + ", ".join(str(n) for n in numbers)
        return "Chap (không xác định)"

    # Tách ngoại truyện (số âm) và chap thường (số dương)
    nt_nums = sorted([abs(n) for n in valid_nums if n < 0])
    regular_nums = sorted(set(n for n in valid_nums if n > 0))

    parts = []

    # Format chap thường thành dải
    if regular_nums:
        ranges = []
        start = regular_nums[0]
        end = regular_nums[0]

        for num in regular_nums[1:]:
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

        parts.append("Chap " + ", ".join(ranges))

    # Format ngoại truyện
    if nt_nums:
        nt_ranges = []
        start = nt_nums[0]
        end = nt_nums[0]

        for num in nt_nums[1:]:
            if num == end + 1:
                end = num
            else:
                if start == end:
                    nt_ranges.append(f"NT{start}")
                else:
                    nt_ranges.append(f"NT{start}-NT{end}")
                start = num
                end = num

        if start == end:
            nt_ranges.append(f"NT{start}")
        else:
            nt_ranges.append(f"NT{start}-NT{end}")

        parts.append(", ".join(nt_ranges))

    return ", ".join(parts) if parts else "Chap (không xác định)"



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


def create_single_thongke_panel(
    stats: dict,
    all_deadlines: list[dict],
    overdue_info: dict = None,
    drive_failures: list[dict] = None,
) -> discord.Embed:
    """Tạo 1 embed panel duy nhất chứa toàn bộ thông tin thống kê tổng quan, quá hạn và chi tiết chap."""
    month_str = get_current_month_str()
    embed = discord.Embed(
        title=f"📊 Dashboard Thống Kê Deadline — {month_str}",
        color=COLOR_INFO,
    )

    # 1. Header description: Tổng quan
    total = stats.get("total", 0)
    available = stats.get("available", 0)
    assigned = stats.get("assigned", 0)
    submitted = stats.get("submitted", 0)
    overdue = stats.get("overdue", 0)

    embed.description = (
        f"📁 **Tổng:** {total} chap  │  "
        f"🟢 **Còn tồn:** {available}  │  "
        f"🟡 **Đang làm:** {assigned}  │  "
        f"✅ **Đã nộp:** {submitted}  │  "
        f"🔴 **Quá hạn:** {overdue}"
    )

    # Footer: Chỉ còn Month string (đã xóa dòng nhắc /thongke [role]...)
    embed.set_footer(text=f"📅 {month_str}")

    embed.set_footer(text=f"📅 {month_str} • Làm mới lúc {datetime.now().strftime('%H:%M:%S')}")
    current_char_count = len(embed.title or "") + len(embed.description or "")

    # 2. Thống kê chi tiết Deadline Quá Hạn & Auto Returned
    overdue_lines = []
    if overdue_info:
        active_overdue = overdue_info.get("active_overdue", [])
        auto_returned = overdue_info.get("auto_returned", [])

        # Active overdue
        for d in active_overdue:
            role_name = ROLE_TYPES.get(d.get("role_type", ""), {}).get("name", d.get("role_type", ""))
            chap_name = d.get("chapter_name", f"Chap {d.get('chapter_number', '?')}")
            series = d.get("series_name", "Không rõ")
            user_id = d.get("assigned_to")
            username = d.get("assigned_username")
            user_mention = f"<@{user_id}>" if user_id else f"@{username or 'Chưa rõ'}"
            overdue_lines.append(f"• **{series}** - {chap_name} ({role_name}) — {user_mention} *(🔴 Đang quá hạn)*")

        # Auto returned overdue
        for log in auto_returned:
            role_name = ROLE_TYPES.get(log.get("role_type", ""), {}).get("name", log.get("role_type", ""))
            chap_name = log.get("chapter_name", f"Chap {log.get('chapter_number', '?')}")
            series = log.get("series_name", "Không rõ")
            user_id = log.get("user_id")
            username = log.get("username")
            user_mention = f"<@{user_id}>" if user_id else f"@{username or 'Chưa rõ'}"
            ret_at = log.get("returned_at", "")
            time_str = f" lúc {ret_at[:16]}" if ret_at else ""
            overdue_lines.append(f"• **{series}** - {chap_name} ({role_name}) — {user_mention} *(🔴 Quá hạn - Đã tự động thu hồi về kho{time_str})*")

    if overdue_lines:
        overdue_text = "\n".join(overdue_lines)
        if len(overdue_text) > 1024:
            overdue_text = overdue_text[:1000] + "\n... *(còn nữa)*"
        embed.add_field(
            name="🚨 Chi Tiết Deadline Quá Hạn & Thu Hồi Kho",
            value=overdue_text,
            inline=False,
        )
        current_char_count += len("🚨 Chi Tiết Deadline Quá Hạn & Thu Hồi Kho") + len(overdue_text)

    # 3. Các link Drive đã từng lỗi share
    drive_failures = drive_failures or []
    drive_failure_lines = []
    for failure in drive_failures[:10]:
        drive_link = str(failure.get("drive_link") or failure.get("drive_key") or "Không rõ")
        if len(drive_link) > 180:
            drive_link = drive_link[:177] + "..."
        drive_link = drive_link.replace("`", "'")

        is_active = bool(failure.get("is_active"))
        status_text = (
            "⛔ Đang tạm tránh"
            if is_active
            else "⚠️ Đã hết thời gian tránh, sẽ được thử lại"
        )
        last_failed_at = str(failure.get("last_failed_at") or "Không rõ")[:16]
        failure_count = int(failure.get("failure_count") or 0)
        last_error = str(failure.get("last_error") or "Không có chi tiết")
        last_error = last_error.replace("`", "'").replace("\n", " ")
        if len(last_error) > 220:
            last_error = last_error[:217] + "..."

        drive_failure_lines.append(
            f"• `{drive_link}`\n"
            f"  {status_text} · Lỗi **{failure_count} lần** · Lần cuối: `{last_failed_at}`\n"
            f"  Chi tiết: {last_error}"
        )

    drive_failure_text = "\n".join(drive_failure_lines) if drive_failure_lines else "✅ Không ghi nhận link Drive nào bị lỗi."
    if len(drive_failure_text) > 1024:
        drive_failure_text = drive_failure_text[:990] + "\n... *(còn link lỗi khác)*"
    embed.add_field(
        name="⚠️ Danh sách link Google Drive bị lỗi",
        value=drive_failure_text,
        inline=False,
    )
    current_char_count += len("⚠️ Danh sách link Google Drive bị lỗi") + len(drive_failure_text)

    # 4. Tóm tắt theo Role
    per_role = stats.get("per_role", {})
    if per_role:
        breakdown_lines = []
        for role_type, role_stats in per_role.items():
            role_name = ROLE_TYPES.get(role_type, {}).get("name", role_type)
            breakdown_lines.append(
                f"**{role_name}**: 🟢 {role_stats.get('available', 0)} tồn │ 🟡 {role_stats.get('assigned', 0)} đang làm │ ✅ {role_stats.get('submitted', 0)} đã nộp │ 🔴 {role_stats.get('overdue', 0)} quá hạn"
            )
        breakdown_text = "\n".join(breakdown_lines)
        if len(breakdown_text) > 1024:
            breakdown_text = breakdown_text[:1000] + "..."
        embed.add_field(
            name="━━━ Thống kê theo Vị Trí ━━━",
            value=breakdown_text,
            inline=False,
        )
        current_char_count += len("━━━ Thống kê theo Vị Trí ━━━") + len(breakdown_text)

    # 5. Chi tiết từng bộ truyện & chap
    if all_deadlines:
        now_dt = datetime.now()
        # Group theo (series_name, role_type)
        grouped = {}
        for d in all_deadlines:
            s_name = d.get("series_name", "Khác")
            r_type = d.get("role_type", "")
            grouped.setdefault((s_name, r_type), []).append(d)

        for (series_name, r_type), items in grouped.items():
            if len(embed.fields) >= 25 or current_char_count > 5500:
                break

            role_name = ROLE_TYPES.get(r_type, {}).get("name", r_type)
            assigned_items = [d for d in items if d.get("status") != "available"]
            available_items = [d for d in items if d.get("status") == "available"]

            assigned_lines = []
            user_groups = {}
            for d in assigned_items:
                st = d.get("status")
                dl_at = d.get("deadline_at")
                is_overdue = False
                if st == "assigned" and dl_at:
                    try:
                        dl_dt = datetime.fromisoformat(dl_at) if isinstance(dl_at, str) else dl_at
                        if dl_dt < now_dt:
                            is_overdue = True
                    except Exception:
                        pass
                user_key = (d.get("assigned_to"), d.get("assigned_username"), st, is_overdue)
                user_groups.setdefault(user_key, []).append(d.get("chapter_number"))

            for (user_id, username, status, is_overdue), chap_nums in user_groups.items():
                chap_str = format_chapter_numbers_to_ranges(chap_nums)
                user_mention = f"<@{user_id}>" if user_id else f"@{username or 'Chưa rõ'}"
                if status == "submitted":
                    st_str = " *(✅ Đã nộp)*"
                elif status == "pending":
                    st_str = " *(⏳ Chờ xác nhận)*"
                elif is_overdue:
                    st_str = " *(🔴 Quá hạn)*"
                else:
                    st_str = " *(🟡 Đang làm)*"
                assigned_lines.append(f"• {chap_str} — {user_mention}{st_str}")

            avail_nums = [d.get("chapter_number") for d in available_items]
            avail_text = f"• {format_chapter_numbers_to_ranges(avail_nums)} ({len(avail_nums)} chap)" if avail_nums else "• *(Hết chap tồn)*"

            field_val_parts = ["🟡 **Đã giao:**"]
            if assigned_lines:
                field_val_parts.extend(assigned_lines)
            else:
                field_val_parts.append("• *(Chưa giao chap nào)*")
            field_val_parts.append("\n🟢 **Còn tồn (chưa giao):**")
            field_val_parts.append(avail_text)

            field_value = "\n".join(field_val_parts)
            if len(field_value) > 1024:
                field_value = field_value[:1000] + "\n... *(còn nữa)*"

            field_name = f"📚 {series_name} ({role_name})"
            if len(field_name) > 256:
                field_name = field_name[:250] + "..."

            f_len = len(field_name) + len(field_value)
            if current_char_count + f_len > 5800:
                break

            embed.add_field(name=field_name, value=field_value, inline=False)
            current_char_count += f_len

    return embed
