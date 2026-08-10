"""
Embed Builder - Tạo các Discord Embed đẹp cho bot.
"""

import discord
from datetime import datetime
from config import ROLE_TYPES, COLOR_SUCCESS, COLOR_WARNING, COLOR_ERROR, COLOR_INFO, COLOR_PENDING
from utils.time_helper import format_deadline, format_remaining, get_now


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


DEADLINE_EMBED_CHAR_LIMIT = 5800
DEADLINE_FIELD_CHAR_LIMIT = 980


def _format_optional_datetime(value: datetime | str | None, fallback: str) -> str:
    """Format a stored timestamp without letting one malformed row break the panel."""
    if not value:
        return fallback
    try:
        return format_deadline(value)
    except (TypeError, ValueError):
        return str(value)


def _split_deadline_lines(lines: list[str], max_chars: int = DEADLINE_FIELD_CHAR_LIMIT) -> list[str]:
    """Split complete chapter entries while respecting Discord's field value limit."""
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    for line in lines:
        # A chapter name or series name can theoretically be unusually long.
        # Split that one entry as a last resort so no content is silently lost.
        if len(line) > max_chars:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_length = 0
            for start in range(0, len(line), max_chars):
                chunks.append(line[start : start + max_chars])
            continue

        extra_length = len(line) + (1 if current else 0)
        if current and current_length + extra_length > max_chars:
            chunks.append("\n".join(current))
            current = [line]
            current_length = len(line)
        else:
            current.append(line)
            current_length += extra_length

    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


def _deadline_embed_size(
    title: str,
    description: str,
    fields: list[tuple[str, str]],
    footer: str,
) -> int:
    """Approximate Discord's total embed character count for pagination."""
    return (
        len(title)
        + len(description)
        + len(footer)
        + sum(len(name) + len(value) for name, value in fields)
    )


def _build_deadline_page(
    title: str,
    description: str,
    fields: list[tuple[str, str]],
    page_number: int,
    page_count: int,
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=COLOR_INFO)
    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)
    if page_count > 1:
        embed.set_footer(text=f"Trang {page_number}/{page_count} · Dùng nút bên dưới để xem toàn bộ chap")
    return embed


def create_deadline_pages(deadlines: list[dict], user: discord.Member) -> list[discord.Embed]:
    """Create all pages for the private per-user deadline dashboard.

    Discord limits one embed to 6,000 characters and one field value to 1,024
    characters. Entries are therefore split only between chapters and exposed
    as multiple pages in the same Discord message by the command cog.
    """
    month_str = get_current_month_str()
    title = f"📋 Deadline của {user.display_name} — {month_str}"

    if not deadlines:
        return [
            _build_deadline_page(
                title,
                "Bạn chưa có deadline nào đang làm hoặc đã nộp.",
                [],
                1,
                1,
            )
        ]

    now = get_now()
    groups = {"doing": [], "submitted": [], "overdue": []}

    for deadline in deadlines:
        if deadline.get("status") == "submitted":
            groups["submitted"].append(deadline)
            continue

        deadline_at = deadline.get("deadline_at")
        is_overdue = False
        if deadline_at:
            try:
                parsed_deadline = (
                    datetime.fromisoformat(deadline_at)
                    if isinstance(deadline_at, str)
                    else deadline_at
                )
                is_overdue = parsed_deadline < now
            except (TypeError, ValueError):
                pass
        groups["overdue" if is_overdue else "doing"].append(deadline)

    description = (
        f"📚 **Tổng:** {len(deadlines)} chap │ "
        f"🟡 **Đang làm:** {len(groups['doing'])} │ "
        f"✅ **Đã nộp:** {len(groups['submitted'])} │ "
        f"🔴 **Quá hạn:** {len(groups['overdue'])}"
    )

    def render_deadline(deadline: dict, status: str, sequence: int) -> str:
        role_name = ROLE_TYPES.get(deadline.get("role_type", ""), {}).get(
            "name", deadline.get("role_type", "?")
        )
        series = deadline.get("series_name", "Không rõ")
        chapter = deadline.get("chapter_name", "?")
        deadline_at = deadline.get("deadline_at")
        due_text = _format_optional_datetime(deadline_at, "Chưa có hạn")

        if status == "submitted":
            # The section heading already communicates the status. Keep only
            # the completion timestamp under each submitted chapter.
            completed_text = _format_optional_datetime(
                deadline.get("submitted_at"),
                "Chưa ghi nhận",
            )
            status_text = f"🕒 Hoàn thành lúc: `{completed_text}`"
        elif status == "overdue":
            status_text = f"🔴 Quá hạn · Hạn: `{due_text}`"
        else:
            remaining = format_remaining(deadline_at) if deadline_at else "chưa có hạn"
            status_text = f"🟡 Đang làm · còn {remaining} · Hạn: `{due_text}`"

        return f"{sequence}. **{series}** — **{chapter}** · {role_name}\n  {status_text}"

    field_specs = (
        ("🟡 Đang làm", "doing", "Hiện không có chap nào đang làm."),
        ("✅ Đã nộp", "submitted", "Chưa có chap nào đã nộp."),
        ("🔴 Quá hạn", "overdue", "Hiện không có chap nào quá hạn."),
    )

    fields: list[tuple[str, str]] = []
    sequence = 1
    for field_name, group_name, empty_text in field_specs:
        items = groups[group_name]
        lines = [
            render_deadline(item, group_name, sequence + index)
            for index, item in enumerate(items)
        ]
        sequence += len(items)
        chunks = _split_deadline_lines(lines) if lines else [empty_text]
        for chunk_index, chunk in enumerate(chunks):
            chunk_name = field_name if chunk_index == 0 else f"{field_name} (tiếp)"
            fields.append((chunk_name, chunk))

    page_fields: list[list[tuple[str, str]]] = []
    current_fields: list[tuple[str, str]] = []
    for field in fields:
        candidate_fields = current_fields + [field]
        candidate_footer = "Trang 1/1"
        candidate_size = _deadline_embed_size(
            title,
            description,
            candidate_fields,
            candidate_footer,
        )
        if current_fields and (
            candidate_size > DEADLINE_EMBED_CHAR_LIMIT
            or len(current_fields) >= 25
        ):
            page_fields.append(current_fields)
            current_fields = [field]
        else:
            current_fields = candidate_fields

    if current_fields:
        page_fields.append(current_fields)

    page_count = len(page_fields)
    return [
        _build_deadline_page(title, description, page, index, page_count)
        for index, page in enumerate(page_fields, start=1)
    ]


def create_deadline_list(deadlines: list[dict], user: discord.Member) -> discord.Embed:
    """Backward-compatible helper returning the first dashboard page."""
    return create_deadline_pages(deadlines, user)[0]


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


def _build_thongke_series_fields(all_deadlines: list[dict]) -> list[tuple[str, str]]:
    """Build one Discord field per series/role without applying embed limits."""
    now_dt = datetime.now()
    grouped = {}
    for deadline in all_deadlines:
        series_name = deadline.get("series_name") or "Khác"
        role_type = deadline.get("role_type") or ""
        grouped.setdefault((series_name, role_type), []).append(deadline)

    fields = []
    for (series_name, role_type), items in grouped.items():
        role_name = ROLE_TYPES.get(role_type, {}).get("name", role_type)
        assigned_items = [item for item in items if item.get("status") != "available"]
        available_items = [item for item in items if item.get("status") == "available"]

        assigned_lines = []
        user_groups = {}
        for item in assigned_items:
            status = item.get("status")
            deadline_at = item.get("deadline_at")
            is_overdue = False
            if status == "assigned" and deadline_at:
                try:
                    deadline_dt = (
                        datetime.fromisoformat(deadline_at)
                        if isinstance(deadline_at, str)
                        else deadline_at
                    )
                    is_overdue = deadline_dt < now_dt
                except Exception:
                    pass

            user_key = (
                item.get("assigned_to"),
                item.get("assigned_username"),
                status,
                is_overdue,
            )
            user_groups.setdefault(user_key, []).append(item.get("chapter_number"))

        for (user_id, username, status, is_overdue), chapter_numbers in user_groups.items():
            chapter_text = format_chapter_numbers_to_ranges(chapter_numbers)
            user_mention = (
                f"<@{user_id}>" if user_id else f"@{username or 'Chưa rõ'}"
            )
            if status == "submitted":
                status_text = " *(\u2705 Đã nộp)*"
            elif status == "pending":
                status_text = " *(\u23f3 Chờ xác nhận)*"
            elif is_overdue:
                status_text = " *(\U0001f534 Quá hạn)*"
            else:
                status_text = " *(\U0001f7e1 Đang làm)*"
            assigned_lines.append(
                f"\u2022 {chapter_text} \u2014 {user_mention}{status_text}"
            )

        available_numbers = [item.get("chapter_number") for item in available_items]
        available_text = (
            f"\u2022 {format_chapter_numbers_to_ranges(available_numbers)} "
            f"({len(available_numbers)} chap)"
            if available_numbers
            else "\u2022 *(Hết chap tồn)*"
        )

        field_parts = ["\U0001f7e1 **Đã giao:**"]
        field_parts.extend(assigned_lines or ["\u2022 *(Chưa giao chap nào)*"])
        field_parts.extend(["\n\U0001f7e2 **Còn tồn (chưa giao):**", available_text])
        content_lines = field_parts

        field_name = f"\U0001f4da {series_name} ({role_name})"
        if len(field_name) > 256:
            field_name = field_name[:250] + "..."
        chunks = []
        current_lines = []
        for line in content_lines:
            candidate = "\n".join(current_lines + [line])
            if current_lines and len(candidate) > 1000:
                chunks.append("\n".join(current_lines))
                current_lines = [line]
            else:
                current_lines.append(line)
        if current_lines:
            chunks.append("\n".join(current_lines))

        for chunk_index, chunk in enumerate(chunks, start=1):
            chunk_name = field_name
            if len(chunks) > 1:
                suffix = f" — {chunk_index}/{len(chunks)}"
                chunk_name = field_name[: 256 - len(suffix)] + suffix
            fields.append((chunk_name, chunk))

    return fields


def _embed_character_count(embed: discord.Embed) -> int:
    """Count the relevant characters before adding another field."""
    count = len(embed.title or "") + len(embed.description or "")
    count += len(embed.footer.text or "") if embed.footer else 0
    for field in embed.fields:
        count += len(field.name) + len(field.value)
    return count


def create_thongke_panels(
    stats: dict,
    all_deadlines: list[dict],
    overdue_info: dict = None,
    drive_failures: list[dict] = None,
    filter_label: str = None,
) -> list[discord.Embed]:
    """Create as many embeds as needed so every series can be displayed.

    Discord limits an embed to 25 fields and 6,000 characters. The old
    single-panel renderer stopped at those limits and silently dropped later
    series. The first page keeps the dashboard summary; subsequent pages carry
    the remaining series fields.
    """
    first_page = create_single_thongke_panel(
        stats=stats,
        all_deadlines=[],
        overdue_info=overdue_info,
        drive_failures=drive_failures,
    )
    if filter_label:
        first_page.description = (
            f"{first_page.description or ''}\n\U0001f3af **Bộ lọc:** {filter_label}"
        )[:4096]
    pages = [first_page]
    month_str = get_current_month_str()
    timestamp = datetime.now().strftime("%H:%M:%S")

    for field_name, field_value in _build_thongke_series_fields(all_deadlines):
        page = pages[-1]
        field_size = len(field_name) + len(field_value)
        if (
            len(page.fields) >= 25
            or _embed_character_count(page) + field_size > 5800
        ):
            page = discord.Embed(
                title=f"\U0001f4ca Dashboard Thống Kê Deadline — {month_str}",
                description=(
                    "Chi tiết các bộ truyện còn lại:"
                    + (f"\n\U0001f3af **Bộ lọc:** {filter_label}" if filter_label else "")
                ),
                color=COLOR_INFO,
            )
            pages.append(page)
        page.add_field(name=field_name, value=field_value, inline=False)

    total_pages = len(pages)
    for index, page in enumerate(pages, start=1):
        page.set_footer(
            text=f"\U0001f4c5 {month_str} • Trang {index}/{total_pages} "
            f"• Làm mới lúc {timestamp}"
        )
    return pages


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
            else "✅ Đã hết thời gian tránh, member có thể nhận lại"
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

    drive_failure_text = (
        "\n".join(drive_failure_lines)
        if drive_failure_lines
        else "✅ Không có link Drive nào đang bị tạm tránh."
    )
    if len(drive_failure_text) > 1024:
        drive_failure_text = drive_failure_text[:990] + "\n... *(còn link lỗi khác)*"
    embed.add_field(
        name="⚠️ Danh sách link Google Drive đang bị tạm tránh",
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
