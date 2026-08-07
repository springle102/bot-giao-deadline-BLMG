"""
Tất cả hàm query giao tiếp cơ sở dữ liệu SQLite async.
Đã cập nhật hỗ trợ phân tách dữ liệu độc lập theo từng Server (guild_id).
"""

import json
import random
import aiosqlite
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from config import DRIVE_SHARE_FAILURE_COOLDOWN_HOURS, MAX_EXTENSION_HOURS
from database.db import get_db
from utils.time_helper import get_now, get_now_str
from utils.chapter_helper import (
    chapter_sort_key,
    normalize_chapter_number,
    normalize_series_name,
    series_names_match,
)
from utils.google_drive import extract_drive_id


def _deadline_guild_scope(column: str = "guild_id") -> str:
    """Shared scope for current guild data plus legacy global rows."""
    return f"({column} = ? OR {column} = 'global' OR {column} IS NULL)"


async def get_available_deadlines(role_type: str, count: int, guild_id: str = "global") -> List[Dict[str, Any]]:
    """Random bộ truyện trước, rồi lấy chapter nhỏ nhất trong từng bộ."""
    db = await get_db()
    try:
        async with db.execute(
            f"""SELECT * FROM deadlines
               WHERE {_deadline_guild_scope()}
                 AND role_type = ? 
                 AND status = 'available' 
               ORDER BY series_name ASC, chapter_number ASC, id ASC""",
            (guild_id, role_type)
        ) as cursor:
            rows = await cursor.fetchall()
            available_rows = [dict(row) for row in rows]
            blocked_keys = await _get_blocked_drive_link_keys(db, guild_id)
            available_rows = [
                row for row in available_rows
                if _drive_link_key(row.get("drive_link")) not in blocked_keys
            ]
            return select_available_deadlines(available_rows, count)
    finally:
        await db.close()


async def count_available_deadlines(role_type: str, guild_id: str = "global") -> int:
    """Đếm số chap chưa giao còn tồn của một role trong Server."""
    db = await get_db()
    try:
        async with db.execute(
            f"""SELECT drive_link
                FROM deadlines
                WHERE {_deadline_guild_scope()}
                  AND role_type = ?
                  AND status = 'available'""",
            (guild_id, role_type),
        ) as cursor:
            rows = await cursor.fetchall()
            blocked_keys = await _get_blocked_drive_link_keys(db, guild_id)
            return sum(
                1
                for row in rows
                if _drive_link_key(row["drive_link"]) not in blocked_keys
            )
    finally:
        await db.close()


def _drive_link_key(drive_link: Any) -> str:
    """Return a stable key so URL variants of the same Drive item match."""
    clean_link = str(drive_link or "").strip()
    if not clean_link:
        return ""

    drive_id = extract_drive_id(clean_link)
    if drive_id:
        return f"id:{drive_id}"
    return f"url:{clean_link.rstrip('/').lower()}"


async def _get_blocked_drive_link_keys(db, guild_id: str) -> set[str]:
    """Load currently blocked links without breaking legacy test/DB schemas."""
    try:
        async with db.execute(
            """SELECT drive_key
               FROM drive_share_failures
               WHERE (guild_id = ? OR guild_id = 'global' OR guild_id IS NULL)
                 AND (blocked_until IS NULL OR blocked_until > ?)""",
            (guild_id, get_now_str()),
        ) as cursor:
            rows = await cursor.fetchall()
    except Exception as error:
        # init_db creates this table. The fallback keeps older databases usable
        # during an upgrade and keeps read-only maintenance commands safe.
        if "no such table" not in str(error).lower():
            print(f"[DB Error] Không thể đọc danh sách link Drive bị chặn: {error}")
        return set()
    return {str(row["drive_key"]) for row in rows if row["drive_key"]}


async def record_drive_share_failure(
    guild_id: str,
    drive_link: str,
    error_message: str,
    cooldown_hours: int = DRIVE_SHARE_FAILURE_COOLDOWN_HOURS,
) -> bool:
    """Remember a failed link so the next request avoids its chapters."""
    drive_key = _drive_link_key(drive_link)
    if not drive_key:
        return False

    now = get_now()
    blocked_until = now + timedelta(hours=max(1, int(cooldown_hours)))
    db = await get_db()
    try:
        async with db.execute(
            """SELECT failure_count
               FROM drive_share_failures
               WHERE guild_id = ? AND drive_key = ?""",
            (guild_id, drive_key),
        ) as cursor:
            existing = await cursor.fetchone()

        if existing:
            await db.execute(
                """UPDATE drive_share_failures
                   SET drive_link = ?, failure_count = failure_count + 1,
                       last_error = ?, last_failed_at = ?, blocked_until = ?
                   WHERE guild_id = ? AND drive_key = ?""",
                (
                    str(drive_link).strip(),
                    str(error_message)[:2000],
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    blocked_until.strftime("%Y-%m-%d %H:%M:%S"),
                    guild_id,
                    drive_key,
                ),
            )
        else:
            await db.execute(
                """INSERT INTO drive_share_failures
                   (guild_id, drive_key, drive_link, failure_count,
                    last_error, last_failed_at, blocked_until)
                   VALUES (?, ?, ?, 1, ?, ?, ?)""",
                (
                    guild_id,
                    drive_key,
                    str(drive_link).strip(),
                    str(error_message)[:2000],
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    blocked_until.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
        await db.commit()
        return True
    except Exception as error:
        await db.rollback()
        print(f"[DB Error] Không thể ghi nhận link Drive lỗi: {error}")
        return False
    finally:
        await db.close()


async def get_drive_share_failures(guild_id: str = "global") -> List[Dict[str, Any]]:
    """Return Drive links that have failed sharing for this guild.

    Expired cooldown records are kept in the result so admins can still see
    links that need fixing before they are tried again.
    """
    db = await get_db()
    try:
        async with db.execute(
            """SELECT guild_id, drive_key, drive_link, failure_count,
                      last_error, last_failed_at, blocked_until,
                      CASE WHEN blocked_until IS NOT NULL
                                      AND blocked_until > ?
                           THEN 1 ELSE 0 END AS is_active
               FROM drive_share_failures
               WHERE guild_id = ? OR guild_id = 'global' OR guild_id IS NULL
               ORDER BY is_active DESC, last_failed_at DESC, drive_link ASC""",
            (get_now_str(), guild_id),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]
    except Exception as error:
        if "no such table" not in str(error).lower():
            print(f"[DB Error] Không thể đọc danh sách link Drive lỗi: {error}")
        return []
    finally:
        await db.close()


def select_available_deadlines(
    rows: List[Dict[str, Any]],
    count: int,
) -> List[Dict[str, Any]]:
    """Chọn deadline theo bộ truyện rồi mới theo thứ tự chapter.

    Với yêu cầu 2 chap và có ít nhất 2 bộ, chọn 2 bộ khác nhau ngẫu nhiên và
    lấy chap nhỏ nhất của mỗi bộ. Nếu chỉ có 1 bộ, lấy 2 chap nhỏ nhất của bộ
    đó. Các chapter trùng trong cùng một bộ không được chọn lặp lại.
    """
    if count <= 0 or not rows:
        return []

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        series_key = normalize_series_name(row.get("series_name")) or "__unknown__"
        grouped.setdefault(series_key, []).append(row)

    ordered_groups: Dict[str, List[Dict[str, Any]]] = {}
    for series_key, series_rows in grouped.items():
        ordered_rows = sorted(
            series_rows,
            key=lambda row: (
                chapter_sort_key(row.get("chapter_number")),
                row.get("id", 0),
            ),
        )
        unique_rows = []
        seen_chapters = set()
        for row in ordered_rows:
            chapter_number = normalize_chapter_number(row.get("chapter_number"))
            if chapter_number in seen_chapters:
                continue
            seen_chapters.add(chapter_number)
            unique_rows.append(row)
        if unique_rows:
            ordered_groups[series_key] = unique_rows

    series_keys = list(ordered_groups)
    if not series_keys:
        return []

    selected_series = random.sample(series_keys, min(count, len(series_keys)))
    selected = [ordered_groups[key][0] for key in selected_series]

    # Nếu số bộ ít hơn số chap cần nhận, bổ sung các chap kế tiếp từ các bộ
    # đã chọn; với giới hạn hiện tại (tối đa 2) đây là nhánh một bộ/2 chap.
    remaining = count - len(selected)
    if remaining > 0:
        for series_key in selected_series:
            for row in ordered_groups[series_key][1:]:
                selected.append(row)
                remaining -= 1
                if remaining == 0:
                    break
            if remaining == 0:
                break

    return selected


async def set_pending_deadlines(ids: List[int], user_id: str, guild_id: str = "global") -> bool:
    """Atomically reserve exactly these available deadlines for a user."""
    if not ids:
        return False
    placeholders = ','.join('?' for _ in ids)
    now_str = get_now_str()
    query = f"""UPDATE deadlines 
               SET status = 'pending', assigned_to = ?, assigned_at = ? 
               WHERE id IN ({placeholders}) AND status = 'available'
                 AND {_deadline_guild_scope()}"""
    
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        params = [user_id, now_str] + ids + [guild_id]
        cursor = await db.execute(query, params)
        if cursor.rowcount != len(ids):
            await db.rollback()
            return False
        await db.commit()
        return True
    except Exception as error:
        await db.rollback()
        print(f"[DB Error] Không thể giữ deadline pending: {error}")
        return False
    finally:
        await db.close()


async def confirm_deadlines(ids: List[int], user_id: str, username: str, deadline_at: str, batch_id: str = None, guild_id: str = "global") -> bool:
    """Cập nhật trạng thái từ 'pending' sang 'assigned', ghi nhận thông tin user và thời hạn."""
    if not ids:
        return False
    placeholders = ','.join('?' for _ in ids)
    now_str = get_now_str()
    update_query = f"""
        UPDATE deadlines 
        SET status = 'assigned', 
            assigned_username = ?, 
            assigned_at = ?, 
            deadline_at = ?,
            batch_id = ?,
            extension_hours = 0
        WHERE id IN ({placeholders}) AND status = 'pending' AND assigned_to = ? AND {_deadline_guild_scope()}
    """
    
    insert_log_query = """
        INSERT INTO assignment_log (guild_id, deadline_id, user_id, username, action)
        VALUES (?, ?, ?, ?, 'assigned')
    """
    
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        params = [username, now_str, deadline_at, batch_id] + ids + [user_id, guild_id]
        cursor = await db.execute(update_query, params)
        if cursor.rowcount != len(ids):
            await db.rollback()
            return False
        
        for deadline_id in ids:
            await db.execute(insert_log_query, (guild_id, deadline_id, user_id, username))
            
        await db.commit()
        return True
    except Exception as e:
        await db.rollback()
        print(f"[DB Error] Lỗi confirm_deadlines: {e}")
        return False
    finally:
        await db.close()


async def cancel_pending_deadlines(ids: List[int], guild_id: str = "global") -> None:
    """Hủy trạng thái 'pending' và chuyển về 'available'."""
    if not ids:
        return
    placeholders = ','.join('?' for _ in ids)
    query = f"""UPDATE deadlines 
               SET status = 'available', assigned_to = NULL, assigned_at = NULL,
                   assigned_username = NULL, deadline_at = NULL, batch_id = NULL,
                   extension_hours = 0
               WHERE id IN ({placeholders}) AND status = 'pending' AND {_deadline_guild_scope()}"""
    
    db = await get_db()
    try:
        await db.execute(query, ids + [guild_id])
        await db.commit()
    finally:
        await db.close()


async def rollback_deadline_assignment(
    ids: List[int],
    user_id: str,
    guild_id: str = "global",
    reason: str = "assignment_rollback",
) -> List[Dict[str, Any]]:
    """Rollback các deadline pending/assigned của một lần nhận bị lỗi."""
    if not ids:
        return []

    placeholders = ",".join("?" for _ in ids)
    scope = _deadline_guild_scope()
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            f"""SELECT id, guild_id, assigned_to, assigned_username
                FROM deadlines
                WHERE id IN ({placeholders})
                  AND assigned_to = ?
                  AND status IN ('pending', 'assigned')
                  AND {scope}""",
            ids + [user_id, guild_id],
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]

        if not rows:
            return []

        row_ids = [row["id"] for row in rows]
        row_placeholders = ",".join("?" for _ in row_ids)
        await db.execute(
            f"""UPDATE deadlines
                SET status = 'available',
                    assigned_to = NULL,
                    assigned_username = NULL,
                    assigned_at = NULL,
                    deadline_at = NULL,
                    batch_id = NULL,
                    extension_hours = 0
                WHERE id IN ({row_placeholders})
                  AND assigned_to = ?
                  AND status IN ('pending', 'assigned')
                  AND {scope}""",
            row_ids + [user_id, guild_id],
        )

        for row in rows:
            await db.execute(
                """INSERT INTO assignment_log
                   (guild_id, deadline_id, user_id, username, action)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    row.get("guild_id") or guild_id,
                    row["id"],
                    user_id,
                    row.get("assigned_username") or "",
                    reason,
                ),
            )

        await db.commit()
        return rows
    except Exception as e:
        await db.rollback()
        print(f"[DB Error] Lỗi rollback_deadline_assignment: {e}")
        return []
    finally:
        await db.close()


async def mark_submitted(deadline_id: int, user_id: str, guild_id: str = "global") -> bool:
    """Đánh dấu một deadline đã được nộp."""
    db = await get_db()
    try:
        async with db.execute(
            f"""SELECT assigned_username FROM deadlines
               WHERE id = ? AND assigned_to = ? AND status = 'assigned' AND {_deadline_guild_scope()}""",
            (deadline_id, user_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return False
            username = row['assigned_username']
            
        await db.execute(f"UPDATE deadlines SET status = 'submitted' WHERE id = ? AND {_deadline_guild_scope()}", (deadline_id, guild_id))
        
        await db.execute("""
            INSERT INTO assignment_log (guild_id, deadline_id, user_id, username, action)
            VALUES (?, ?, ?, ?, 'submitted')
        """, (guild_id, deadline_id, user_id, username))
        
        await db.commit()
        return True
    finally:
        await db.close()


async def mark_all_submitted(user_id: str, guild_id: str = "global") -> int:
    """Đánh dấu tất cả deadline của user trong Server đã nộp."""
    db = await get_db()
    try:
        async with db.execute(
            f"""SELECT id, assigned_username FROM deadlines
               WHERE assigned_to = ? AND status = 'assigned' AND {_deadline_guild_scope()}""",
            (user_id, guild_id)
        ) as cursor:
            rows = await cursor.fetchall()
            
        if not rows:
            return 0
            
        count = 0
        for row in rows:
            deadline_id = row['id']
            username = row['assigned_username']
            await db.execute("UPDATE deadlines SET status = 'submitted' WHERE id = ?", (deadline_id,))
            await db.execute("""
                INSERT INTO assignment_log (guild_id, deadline_id, user_id, username, action)
                VALUES (?, ?, ?, ?, 'submitted')
            """, (guild_id, deadline_id, user_id, username))
            count += 1
            
        await db.commit()
        return count
    finally:
        await db.close()


async def add_deadline(chapter_name: str, chapter_number: int, series_name: str, role_type: str, drive_link: str = None, guild_id: str = "global") -> int:
    """Thêm một deadline mới cho Server."""
    db = await get_db()
    try:
        async with db.execute("""
            INSERT INTO deadlines (guild_id, chapter_name, chapter_number, series_name, role_type, drive_link)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (guild_id, chapter_name, chapter_number, series_name, role_type, drive_link)) as cursor:
            await db.commit()
            return cursor.lastrowid
    finally:
        await db.close()


async def add_bulk_deadlines(series_name: str, role_type: str, chap_start: int, chap_end: int, drive_link: str = None, guild_id: str = "global") -> int:
    """Thêm nhiều deadline cùng lúc cho Server với chung 1 link."""
    count = 0
    db = await get_db()
    try:
        for n in range(chap_start, chap_end + 1):
            chapter_name = f"Chap {n}"
            await db.execute("""
                INSERT INTO deadlines (guild_id, chapter_name, chapter_number, series_name, role_type, drive_link)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (guild_id, chapter_name, n, series_name, role_type, drive_link))
            count += 1
        await db.commit()
        return count
    finally:
        await db.close()


async def add_list_deadlines(series_name: str, role_type: str, items: List[tuple], guild_id: str = "global") -> int:
    """Thêm danh sách các (chap_number, drive_link) riêng biệt cho từng chap trong Server."""
    from utils.chapter_helper import chapter_number_to_display
    count = 0
    db = await get_db()
    try:
        for chap_number, drive_link in items:
            chapter_name = chapter_number_to_display(chap_number)
            await db.execute("""
                INSERT INTO deadlines (guild_id, chapter_name, chapter_number, series_name, role_type, drive_link)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (guild_id, chapter_name, chap_number, series_name, role_type, drive_link))
            count += 1
        await db.commit()
        return count
    finally:
        await db.close()


async def get_assigned_deadlines(user_id: str, guild_id: str = "global") -> List[Dict[str, Any]]:
    """Lấy danh sách các deadline đã nhận của user trong Server."""
    db = await get_db()
    try:
        async with db.execute(f"""
            SELECT * FROM deadlines 
            WHERE assigned_to = ? AND status = 'assigned' AND {_deadline_guild_scope()}
            ORDER BY deadline_at ASC
        """, (user_id, guild_id)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_user_active_count(user_id: str, guild_id: str = "global") -> int:
    """Đếm số lượng chap user đang nhận và chưa trả/chưa nộp (status 'assigned' hoặc 'pending')."""
    db = await get_db()
    try:
        async with db.execute(
            f"""SELECT COUNT(*) as cnt FROM deadlines
               WHERE assigned_to = ? 
                 AND status IN ('assigned', 'pending') 
                 AND {_deadline_guild_scope()}""",
            (user_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row['cnt'] if row else 0
    finally:
        await db.close()


async def get_overdue_deadlines() -> List[Dict[str, Any]]:
    """Lấy danh sách các deadline đã quá hạn trên toàn hệ thống (phục vụ Scheduler)."""
    now_str = get_now_str()
    db = await get_db()
    try:
        async with db.execute("""
            SELECT * FROM deadlines 
            WHERE status = 'assigned' AND deadline_at < ?
        """, (now_str,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_nearing_deadlines(hours_left: int) -> List[Dict[str, Any]]:
    """Lấy danh sách các deadline sắp đến hạn (phục vụ Scheduler)."""
    now = get_now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    future_str = (now + timedelta(hours=hours_left)).strftime('%Y-%m-%d %H:%M:%S')
    db = await get_db()
    try:
        async with db.execute("""
            SELECT * FROM deadlines 
            WHERE status = 'assigned' 
              AND deadline_at > ? 
              AND deadline_at <= ?
        """, (now_str, future_str)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_stats(guild_id: str = "global") -> Dict[str, Any]:
    """Lấy thống kê về tổng số, trạng thái và chi tiết theo vai trò của Server."""
    stats = {
        'total': 0, 'available': 0, 'assigned': 0, 'submitted': 0, 'overdue': 0,
        'per_role': {}
    }
    now_str = get_now_str()

    # Reuse the exact population used by get_all_detailed_deadlines so that
    # the summary cannot disagree with the chapter detail panel.
    rows = await get_all_detailed_deadlines(guild_id=guild_id)
    for row in rows:
        status = row.get('status')
        role = row.get('role_type')
        stats['total'] += 1

        if status in stats:
            stats[status] += 1

        is_overdue = (
            status == 'assigned'
            and row.get('deadline_at')
            and row['deadline_at'] < now_str
        )
        if is_overdue:
            stats['overdue'] += 1

        role_stats = stats['per_role'].setdefault(
            role,
            {'total': 0, 'available': 0, 'assigned': 0, 'submitted': 0, 'overdue': 0},
        )
        role_stats['total'] += 1
        if status in role_stats:
            role_stats[status] += 1
        if is_overdue:
            role_stats['overdue'] += 1

    return stats

async def clean_expired_pending(minutes: int = 360) -> int:
    """Tự động dọn dẹp các deadline pending quá 6 giờ."""
    expire_str = (get_now() - timedelta(minutes=minutes)).strftime('%Y-%m-%d %H:%M:%S')
    db = await get_db()
    try:
        async with db.execute("""
            UPDATE deadlines 
            SET status = 'available', assigned_to = NULL, assigned_at = NULL,
                assigned_username = NULL, deadline_at = NULL, batch_id = NULL,
                extension_hours = 0
            WHERE status = 'pending' 
              AND assigned_at <= ?
        """, (expire_str,)) as cursor:
            await db.commit()
            return cursor.rowcount
    finally:
        await db.close()


async def cancel_deadline_admin(chapter_number: int, user_id: str, series_name: str = None, guild_id: str = "global") -> bool:
    """Admin hủy đăng ký deadline trong Server (1 chap)."""
    res = await cancel_bulk_deadlines_admin(user_id, [(series_name, chapter_number)], guild_id=guild_id)
    return len(res.get("success", [])) > 0


async def cancel_bulk_deadlines_admin(
    user_id: str,
    items: List[tuple[Optional[str], int]],
    guild_id: str = "global",
) -> Dict[str, Any]:
    """
    Admin hủy hàng loạt deadline của user theo danh sách các cặp (series_name, chapter_number).
    Trả về dict chứa danh sách thành công và danh sách thất bại.
    """
    success_list = []
    failed_list = []

    db = await get_db()
    try:
        for series_name, chap_num in items:
            query = """
                SELECT id, series_name, chapter_name, assigned_username, drive_link 
                FROM deadlines 
                WHERE chapter_number = ? AND assigned_to = ? AND status IN ('assigned', 'pending') 
                  AND (guild_id = ? OR guild_id = 'global' OR guild_id IS NULL)
            """
            params = [chap_num, user_id, guild_id]

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                matched_rows = [dict(r) for r in rows]

            if series_name and series_name.strip():
                clean_target = series_name.strip().lower()
                matched_rows = [
                    r for r in matched_rows
                    if clean_target in r["series_name"].strip().lower()
                ]

            if not matched_rows:
                failed_list.append((series_name, chap_num))
                continue

            for row in matched_rows:
                    deadline_id = row['id']
                    matched_series = row['series_name']
                    chap_name = row['chapter_name']
                    username = row['assigned_username']
                    drive_link = row['drive_link']

                    await db.execute("""
                        UPDATE deadlines 
                        SET status = 'available', assigned_to = NULL, assigned_username = NULL, 
                            assigned_at = NULL, deadline_at = NULL, batch_id = NULL,
                            extension_hours = 0
                        WHERE id = ?
                    """, (deadline_id,))

                    await db.execute("""
                        INSERT INTO assignment_log (guild_id, deadline_id, user_id, username, action)
                        VALUES (?, ?, ?, ?, 'cancelled_by_admin')
                    """, (guild_id, deadline_id, user_id, username))

                    success_list.append((matched_series, chap_name, chap_num, drive_link))

        await db.commit()
        return {
            "success": success_list,
            "failed": failed_list,
        }
    except Exception as e:
        print(f"[DB Error] Lỗi cancel_bulk_deadlines_admin: {e}")
        return {"success": success_list, "failed": failed_list}
    finally:
        await db.close()


async def check_user_active_drive_link(user_id: str, drive_link: str, guild_id: str = "global") -> bool:
    """Kiểm tra xem thành viên có còn chap nào khác đang nhận/làm chung drive_link này không."""
    if not drive_link or not drive_link.strip():
        return False
    target_link = drive_link.strip()
    target_key = extract_drive_id(target_link) or target_link
    db = await get_db()
    try:
        async with db.execute(
            f"""SELECT drive_link FROM deadlines
               WHERE assigned_to = ? AND status IN ('assigned', 'pending')
                 AND {_deadline_guild_scope()}""",
            (user_id, guild_id)
        ) as cursor:
            rows = await cursor.fetchall()

        # The same Drive item can be stored with different URL variants
        # (query parameters, /file/d versus /folders, etc.). Compare IDs so
        # an overdue chapter cannot revoke access needed by another chapter.
        for row in rows:
            candidate_link = str(row["drive_link"] or "").strip()
            if not candidate_link:
                continue
            candidate_key = extract_drive_id(candidate_link) or candidate_link
            if candidate_key == target_key:
                return True
        return False
    finally:
        await db.close()





async def delete_available_deadlines_admin(
    items: List[Tuple[Optional[str], int]],
    role_type: Optional[str] = None,
    guild_id: str = "global",
) -> Dict[str, Any]:
    """Delete requested available deadlines using the shared data scope.

    Matching is done in Python after loading the scoped rows so legacy chapter
    values and Unicode series names are normalized exactly like user input.
    Each row is deleted with a second ``status = 'available'`` guard to avoid
    deleting a deadline that was claimed between lookup and delete.
    """
    db = await get_db()
    success = []
    failed = []
    diagnostics = []

    try:
        async with db.execute(
            f"""SELECT * FROM deadlines
                WHERE {_deadline_guild_scope()}
                ORDER BY role_type ASC, series_name ASC, chapter_number ASC""",
            (guild_id,),
        ) as cursor:
            scoped_rows = [dict(row) for row in await cursor.fetchall()]

        for series_name, chap_num in items:
            requested_chapter = normalize_chapter_number(chap_num)
            same_chapter = [
                row for row in scoped_rows
                if normalize_chapter_number(row.get("chapter_number")) == requested_chapter
            ] if requested_chapter is not None else []

            same_series = [
                row for row in same_chapter
                if not series_name or series_names_match(series_name, row.get("series_name"))
            ]
            role_matches = [
                row for row in same_series
                if not role_type or row.get("role_type") == role_type
            ]
            matched_rows = [
                row for row in role_matches
                if row.get("status") == "available"
            ]

            if not matched_rows:
                if not same_chapter:
                    reason = "chapter_not_found"
                elif series_name and not same_series:
                    reason = "series_not_match"
                elif role_type and not role_matches:
                    reason = "role_not_match"
                else:
                    reason = "not_available"
                diagnostics.append({
                    "series_name": series_name,
                    "chapter_number": chap_num,
                    "role_type": role_type,
                    "reason": reason,
                })
                failed.append((series_name, chap_num))
                continue

            deleted_for_item = 0
            for row in matched_rows:
                async with db.execute(
                    f"""DELETE FROM deadlines
                        WHERE id = ?
                          AND status = 'available'
                          AND {_deadline_guild_scope()}""",
                    (row["id"], guild_id),
                ) as delete_cursor:
                    deleted = delete_cursor.rowcount == 1

                if not deleted:
                    continue

                await db.execute(
                    """INSERT INTO assignment_log (guild_id, deadline_id, user_id, username, action)
                       VALUES (?, ?, ?, ?, 'deleted_by_admin')""",
                    (guild_id, row["id"], None, "Admin"),
                )
                success.append((
                    row.get("series_name", ""),
                    row.get("chapter_name", ""),
                    row.get("role_type", ""),
                    row.get("drive_link", ""),
                ))
                deleted_for_item += 1
                scoped_rows = [item for item in scoped_rows if item.get("id") != row["id"]]

            if deleted_for_item == 0:
                diagnostics.append({
                    "series_name": series_name,
                    "chapter_number": chap_num,
                    "role_type": role_type,
                    "reason": "changed_before_delete",
                })
                failed.append((series_name, chap_num))

        await db.commit()
        return {
            "success": success,
            "failed": failed,
            "diagnostics": diagnostics,
        }
    except Exception as e:
        print(f"[DB Error] delete_available_deadlines_admin: {e}")
        import traceback
        traceback.print_exc()
        await db.rollback()
        return {"success": [], "failed": items, "diagnostics": []}
    finally:
        await db.close()


async def get_deadline_by_chap_and_user(chapter_number: int, user_id: str, series_name: Optional[str] = None, guild_id: str = "global") -> Optional[Dict[str, Any]]:
    """Tìm một deadline cụ thể được giao cho user trong Server."""
    db = await get_db()
    try:
        if series_name:
            async with db.execute(f"""
                SELECT * FROM deadlines 
                WHERE chapter_number = ? AND assigned_to = ? AND status = 'assigned'
                  AND LOWER(series_name) LIKE LOWER(?)
                  AND {_deadline_guild_scope()}
            """, (chapter_number, user_id, f"%{series_name}%", guild_id)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
        else:
            async with db.execute(f"""
                SELECT * FROM deadlines 
                WHERE chapter_number = ? AND assigned_to = ? AND status = 'assigned'
                  AND {_deadline_guild_scope()}
            """, (chapter_number, user_id, guild_id)) as cursor:
                rows = await cursor.fetchall()
                if not rows:
                    return None
                return dict(rows[0])
    finally:
        await db.close()


async def get_assigned_deadlines_by_chap(chapter_number: int, user_id: str, guild_id: str = "global") -> List[Dict[str, Any]]:
    """Lấy danh sách tất cả deadline chap X được giao cho user trong Server."""
    db = await get_db()
    try:
        async with db.execute(f"""
            SELECT * FROM deadlines 
            WHERE chapter_number = ? AND assigned_to = ? AND status = 'assigned'
              AND {_deadline_guild_scope()}
        """, (chapter_number, user_id, guild_id)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_batch_progress(batch_id: str, guild_id: str = "global") -> Optional[Dict[str, Any]]:
    """Lấy tiến độ nộp của một batch trong Server."""
    if not batch_id:
        return None
    db = await get_db()
    try:
        async with db.execute(
            f"SELECT count(*) as total FROM deadlines WHERE batch_id = ? AND {_deadline_guild_scope()}",
            (batch_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            total = row["total"] if row else 0

        if total == 0:
            return None

        async with db.execute(
            f"SELECT count(*) as cnt FROM deadlines WHERE batch_id = ? AND status = 'submitted' AND {_deadline_guild_scope()}",
            (batch_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            submitted = row["cnt"] if row else 0

        async with db.execute(
            f"SELECT * FROM deadlines WHERE batch_id = ? AND status = 'assigned' AND {_deadline_guild_scope()} ORDER BY chapter_number",
            (batch_id, guild_id)
        ) as cursor:
            remaining_rows = await cursor.fetchall()
            remaining = [dict(r) for r in remaining_rows]

        async with db.execute(
            f"SELECT * FROM deadlines WHERE batch_id = ? AND {_deadline_guild_scope()} ORDER BY chapter_number",
            (batch_id, guild_id)
        ) as cursor:
            all_rows = await cursor.fetchall()
            all_deadlines = [dict(r) for r in all_rows]

        deadline_at = all_deadlines[0].get("deadline_at") if all_deadlines else None
        role_type = all_deadlines[0].get("role_type") if all_deadlines else None
        series_name = all_deadlines[0].get("series_name") if all_deadlines else None

        return {
            "batch_id": batch_id,
            "total": total,
            "submitted": submitted,
            "remaining_count": len(remaining),
            "remaining": remaining,
            "all_deadlines": all_deadlines,
            "deadline_at": deadline_at,
            "role_type": role_type,
            "series_name": series_name,
        }
    finally:
        await db.close()


async def get_role_detailed_deadlines(role_type: str, guild_id: str = "global") -> List[Dict[str, Any]]:
    """Lấy tất cả các deadline thuộc một role_type trong Server."""
    db = await get_db()
    try:
        async with db.execute(f"""
            SELECT * FROM deadlines 
            WHERE role_type = ? AND {_deadline_guild_scope()}
            ORDER BY series_name ASC, chapter_number ASC
        """, (role_type, guild_id)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_all_detailed_deadlines(guild_id: str = "global") -> List[Dict[str, Any]]:
    """Lấy tất cả các deadline trong Server, sắp xếp theo role_type, series_name, chapter_number."""
    db = await get_db()
    try:
        async with db.execute(f"""
            SELECT * FROM deadlines 
            WHERE {_deadline_guild_scope()}
            ORDER BY role_type ASC, series_name ASC, chapter_number ASC
        """, (guild_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        await db.close()



async def save_user_email(user_id: str, username: str, email: str, guild_id: str = "global") -> None:
    """Lưu hoặc cập nhật địa chỉ email của thành viên trong Server cụ thể."""
    db = await get_db()
    try:
        await db.execute("""
            INSERT INTO users (user_id, guild_id, username, email, updated_at)
            VALUES (?, ?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(user_id, guild_id) DO UPDATE SET
                username = excluded.username,
                email = excluded.email,
                updated_at = datetime('now','localtime')
        """, (user_id, guild_id, username, email))
        await db.commit()
    finally:
        await db.close()


async def get_user_email(user_id: str, guild_id: str = "global") -> Optional[str]:
    """Lấy địa chỉ email của thành viên từ user_id trong Server cụ thể."""
    db = await get_db()
    try:
        async with db.execute(
            """SELECT email FROM users 
               WHERE user_id = ?
                 AND (guild_id = ? OR guild_id = 'global' OR guild_id IS NULL)
               ORDER BY CASE WHEN guild_id = ? THEN 0 ELSE 1 END
               LIMIT 1""",
            (user_id, guild_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row["email"] if row else None
    finally:
        await db.close()


async def reset_all_deadlines(guild_id: str = "global") -> int:
    """Xóa sạch toàn bộ dữ liệu deadline và log của Server hiện tại."""
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM deadlines WHERE guild_id = ? OR guild_id IS NULL", (guild_id,))
        deleted_count = cursor.rowcount
        await db.execute("DELETE FROM assignment_log WHERE guild_id = ? OR guild_id IS NULL", (guild_id,))
        await db.commit()
        return deleted_count
    finally:
        await db.close()


async def reset_deadlines_status(guild_id: str = "global") -> int:
    """Reset trạng thái tất cả deadline về 'available' của Server hiện tại."""
    db = await get_db()
    try:
        cursor = await db.execute("""
            UPDATE deadlines
            SET status = 'available',
                assigned_to = NULL,
                assigned_username = NULL,
                assigned_at = NULL,
                deadline_at = NULL,
                batch_id = NULL,
                extension_hours = 0
            WHERE guild_id = ? OR guild_id IS NULL
        """, (guild_id,))
        updated_count = cursor.rowcount
        await db.execute("DELETE FROM assignment_log WHERE guild_id = ? OR guild_id IS NULL", (guild_id,))
        await db.commit()
        return updated_count
    finally:
        await db.close()


async def save_server_setting(
    guild_id: str,
    channel_id: Optional[str] = None,
    role_id: Optional[str] = None,
) -> None:
    """Lưu hoặc cập nhật cấu hình kênh giao deadline và role admin cho Server."""
    now_str = get_now_str()
    db = await get_db()
    try:
        # Kiểm tra xem Server đã có cấu hình chưa
        async with db.execute("SELECT * FROM server_settings WHERE guild_id = ?", (guild_id,)) as cursor:
            row = await cursor.fetchone()

        if row:
            row_dict = dict(row)
            new_channel = channel_id if channel_id is not None else row_dict.get("deadline_channel_id")
            new_role = role_id if role_id is not None else row_dict.get("admin_role_id")
            await db.execute("""
                UPDATE server_settings
                SET deadline_channel_id = ?,
                    admin_role_id = ?,
                    updated_at = ?
                WHERE guild_id = ?
            """, (new_channel, new_role, now_str, guild_id))
        else:
            await db.execute("""
                INSERT INTO server_settings (guild_id, deadline_channel_id, admin_role_id, updated_at)
                VALUES (?, ?, ?, ?)
            """, (guild_id, channel_id, role_id, now_str))
        await db.commit()
    finally:
        await db.close()


async def get_server_setting(guild_id: str) -> Optional[Dict[str, Any]]:
    """Lấy cấu hình riêng (kênh thông báo & role admin) của Server."""
    db = await get_db()
    try:
        async with db.execute("SELECT * FROM server_settings WHERE guild_id = ?", (guild_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
    finally:
        await db.close()


async def extend_deadline(
    deadline_id: int,
    new_deadline_at: str,
    user_id: str,
    username: str = "",
    guild_id: str = "global",
    hours_extended: int = 0,
    batch_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Gia hạn deadline thêm số giờ cho một chap hoặc toàn bộ batch (nếu có)."""
    db = await get_db()
    try:
        if hours_extended < 1 or hours_extended > MAX_EXTENSION_HOURS:
            return {
                "success": False,
                "reason": "invalid_hours",
                "current_hours": 0,
                "remaining_hours": MAX_EXTENSION_HOURS,
            }

        await db.execute("BEGIN IMMEDIATE")

        if batch_id:
            async with db.execute(
                """SELECT id, COALESCE(extension_hours, 0) AS extension_hours
                   FROM deadlines
                   WHERE batch_id = ? AND assigned_to = ? AND status = 'assigned'
                     AND (guild_id = ? OR guild_id IS NULL)""",
                (batch_id, user_id, guild_id)
            ) as cursor:
                rows = await cursor.fetchall()
            current_hours = max((int(row["extension_hours"]) for row in rows), default=0)
            where_params = [batch_id, user_id, guild_id]
            update_query = """UPDATE deadlines
                SET deadline_at = ?,
                    extension_hours = ?
                WHERE batch_id = ? AND assigned_to = ? AND status = 'assigned'
                  AND (guild_id = ? OR guild_id IS NULL)"""
        else:
            async with db.execute(
                """SELECT id, COALESCE(extension_hours, 0) AS extension_hours
                   FROM deadlines
                   WHERE id = ? AND assigned_to = ? AND status = 'assigned'
                     AND (guild_id = ? OR guild_id IS NULL)""",
                (deadline_id, user_id, guild_id)
            ) as cursor:
                rows = await cursor.fetchall()
            current_hours = int(rows[0]["extension_hours"]) if rows else 0
            where_params = [deadline_id, user_id, guild_id]
            update_query = """UPDATE deadlines
                SET deadline_at = ?,
                    extension_hours = ?
                WHERE id = ? AND assigned_to = ? AND status = 'assigned'
                  AND (guild_id = ? OR guild_id IS NULL)"""

        if not rows:
            await db.rollback()
            return {
                "success": False,
                "reason": "not_found",
                "current_hours": 0,
                "remaining_hours": MAX_EXTENSION_HOURS,
            }

        remaining_hours = MAX_EXTENSION_HOURS - current_hours
        if current_hours + hours_extended > MAX_EXTENSION_HOURS:
            await db.rollback()
            return {
                "success": False,
                "reason": "extension_limit",
                "current_hours": current_hours,
                "remaining_hours": max(remaining_hours, 0),
            }

        cursor = await db.execute(
            update_query,
            [new_deadline_at, current_hours + hours_extended] + where_params,
        )
        if cursor.rowcount != len(rows):
            await db.rollback()
            return {
                "success": False,
                "reason": "conflict",
                "current_hours": current_hours,
                "remaining_hours": max(remaining_hours, 0),
            }

        for row in rows:
            await db.execute("""
                INSERT INTO assignment_log (guild_id, deadline_id, user_id, username, action)
                VALUES (?, ?, ?, ?, ?)
            """, (guild_id, row["id"], user_id, username, f"extended_{hours_extended}h"))

        await db.commit()
        return {
            "success": True,
            "reason": "extended",
            "current_hours": current_hours + hours_extended,
            "remaining_hours": MAX_EXTENSION_HOURS - current_hours - hours_extended,
        }
    except Exception as e:
        await db.rollback()
        print(f"[DB Error] Lỗi extend_deadline: {e}")
        return {
            "success": False,
            "reason": "database_error",
            "current_hours": 0,
            "remaining_hours": MAX_EXTENSION_HOURS,
        }
    finally:
        await db.close()


def _parse_deadline_value(value: Any) -> Optional[datetime]:
    """Parse the two timestamp formats used by the legacy database."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        try:
            return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


async def repair_overextended_deadlines() -> List[Dict[str, Any]]:
    """Cap legacy over-extensions and move the deadline back by the excess.

    The current command path rejects an extension that would exceed the limit.
    This repair is for rows written by older deployments. A batch shares one
    extension budget, so every active row in that batch is repaired together.
    """
    db = await get_db()
    repairs: List[Dict[str, Any]] = []
    try:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """SELECT * FROM deadlines
               WHERE status = 'assigned'
               ORDER BY guild_id, batch_id, id""",
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]

        grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
        for row in rows:
            guild_id = row.get("guild_id") or "global"
            batch_id = row.get("batch_id")
            group_key = (
                guild_id,
                f"batch:{batch_id}" if batch_id else f"deadline:{row['id']}",
                str(row.get("assigned_to") or ""),
            )
            grouped.setdefault(group_key, []).append(row)

        for (guild_id, _group_key, user_id), group_rows in grouped.items():
            current_hours = max(
                int(row.get("extension_hours") or 0) for row in group_rows
            )
            if current_hours <= MAX_EXTENSION_HOURS:
                continue
            excess_hours = current_hours - MAX_EXTENSION_HOURS
            prepared_rows = []
            invalid_rows = []

            for row in group_rows:
                current_dt = _parse_deadline_value(row.get("deadline_at"))
                if current_dt is None:
                    invalid_rows.append(row["id"])
                    continue
                prepared_rows.append(
                    (row["id"], current_dt, current_dt - timedelta(hours=excess_hours))
                )

            if invalid_rows:
                repairs.append(
                    {
                        "repaired": False,
                        "guild_id": guild_id,
                        "user_id": user_id,
                        "deadline_ids": [row["id"] for row in group_rows],
                        "current_hours": current_hours,
                        "excess_hours": excess_hours,
                        "reason": "invalid_deadline_at",
                        "invalid_ids": invalid_rows,
                    }
                )
                continue

            for deadline_id, _old_dt, new_dt in prepared_rows:
                cursor = await db.execute(
                    """UPDATE deadlines
                       SET deadline_at = ?, extension_hours = ?
                       WHERE id = ? AND status = 'assigned'""",
                    (
                        new_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        MAX_EXTENSION_HOURS,
                        deadline_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"self-check repair conflict for deadline {deadline_id}"
                    )

                row = next(row for row in group_rows if row["id"] == deadline_id)
                await db.execute(
                    """INSERT INTO assignment_log
                       (guild_id, deadline_id, user_id, username, action)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        guild_id,
                        deadline_id,
                        row.get("assigned_to"),
                        "SelfCheck",
                        f"extension_repair_removed_{excess_hours}h_capped_{MAX_EXTENSION_HOURS}h",
                    ),
                )

            repairs.append(
                {
                    "repaired": True,
                    "guild_id": guild_id,
                    "user_id": user_id,
                    "deadline_ids": [row["id"] for row in group_rows],
                    "batch_id": group_rows[0].get("batch_id"),
                    "previous_hours": current_hours,
                    "current_hours": MAX_EXTENSION_HOURS,
                    "excess_hours": excess_hours,
                    "new_deadline_at": prepared_rows[0][2].strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
            )

        await db.commit()
        return repairs
    except Exception as e:
        await db.rollback()
        print(f"[DB Error] repair_overextended_deadlines failed: {e!s}")
        return []
    finally:
        await db.close()


async def get_assigned_deadlines_for_drive_check() -> List[Dict[str, Any]]:
    """Load active assignments whose Drive access can be verified."""
    db = await get_db()
    try:
        async with db.execute(
            """SELECT * FROM deadlines
               WHERE status = 'assigned'
                 AND assigned_to IS NOT NULL
                 AND drive_link IS NOT NULL
                 AND TRIM(drive_link) <> ''
               ORDER BY guild_id, assigned_to, id"""
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def record_self_check_finding(
    guild_id: str,
    fingerprint: str,
    issue_type: str,
    severity: str,
    entity_key: str,
    details: Dict[str, Any],
) -> bool:
    """Persist a finding and return whether an admin should be notified now."""
    details_json = json.dumps(details, ensure_ascii=False, default=str)
    db = await get_db()
    try:
        async with db.execute(
            """SELECT status, severity FROM self_check_findings
               WHERE fingerprint = ?""",
            (fingerprint,),
        ) as cursor:
            existing = await cursor.fetchone()

        should_notify = (
            not existing
            or existing["status"] != "open"
            or existing["severity"] != severity
        )
        if existing:
            await db.execute(
                """UPDATE self_check_findings
                   SET guild_id = ?, issue_type = ?, severity = ?, entity_key = ?,
                       details = ?, status = 'open', last_seen_at = datetime('now','localtime'),
                       resolved_at = NULL,
                       notified_at = CASE WHEN ? THEN datetime('now','localtime') ELSE notified_at END
                   WHERE fingerprint = ?""",
                (
                    guild_id,
                    issue_type,
                    severity,
                    entity_key,
                    details_json,
                    1 if should_notify else 0,
                    fingerprint,
                ),
            )
        else:
            await db.execute(
                """INSERT INTO self_check_findings
                   (guild_id, fingerprint, issue_type, severity, entity_key,
                    details, notified_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'))""",
                (
                    guild_id,
                    fingerprint,
                    issue_type,
                    severity,
                    entity_key,
                    details_json,
                ),
            )
        await db.commit()
        return should_notify
    finally:
        await db.close()


async def resolve_self_check_finding(fingerprint: str) -> None:
    """Mark a previously observed finding as resolved."""
    db = await get_db()
    try:
        await db.execute(
            """UPDATE self_check_findings
               SET status = 'resolved', resolved_at = datetime('now','localtime'),
                   last_seen_at = datetime('now','localtime')
               WHERE fingerprint = ? AND status = 'open'""",
            (fingerprint,),
        )
        await db.commit()
    finally:
        await db.close()


async def get_all_user_emails(guild_id: str = "global") -> List[Dict[str, Any]]:
    """Lấy danh sách tất cả các email đã đăng ký của thành viên trong Server hiện tại."""
    db = await get_db()
    try:
        async with db.execute(
            """SELECT user_id, username, email, updated_at FROM users 
               WHERE guild_id = ? OR guild_id IS NULL
               ORDER BY updated_at DESC""",
            (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        await db.close()


async def delete_user_email(identifier: str, guild_id: str = "global") -> tuple[bool, Optional[Dict[str, Any]]]:
    """
    Xóa thông tin email đăng ký của thành viên trong Server hiện tại bằng User ID, Mention, Username hoặc Email.
    Trả về (thành_công, thông_tin_bản_ghi_đã_xóa).
    """
    if not identifier:
        return False, None

    clean_id = identifier.strip()
    if clean_id.startswith("<@") and clean_id.endswith(">"):
        clean_id = clean_id.strip("<@!>")

    db = await get_db()
    try:
        async with db.execute(
            """SELECT * FROM users 
               WHERE (user_id = ? 
                   OR LOWER(email) = LOWER(?) 
                   OR LOWER(username) = LOWER(?))
                 AND (guild_id = ? OR guild_id IS NULL)""",
            (clean_id, clean_id, clean_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return False, None
            deleted_user = dict(row)

        await db.execute(
            "DELETE FROM users WHERE user_id = ? AND (guild_id = ? OR guild_id IS NULL)",
            (deleted_user["user_id"], guild_id)
        )
        await db.commit()
        return True, deleted_user
    except Exception as e:
        print(f"[DB Error] Lỗi delete_user_email: {e}")
        return False, None
    finally:
        await db.close()


async def auto_return_overdue_deadlines() -> List[Dict[str, Any]]:
    """
    Tự động thu hồi các deadline đã quá hạn:
    - Tìm tất cả deadline status='assigned' và deadline_at < now_str.
    - Chuyển trạng thái về 'available', reset thông tin người nhận.
    - Ghi nhận nhật ký vào assignment_log (action = 'auto_returned_overdue').
    - Trả về danh sách các deadline vừa được thu hồi.
    """
    now_str = get_now_str()
    db = await get_db()
    try:
        async with db.execute("""
            SELECT * FROM deadlines 
            WHERE status = 'assigned' 
              AND deadline_at IS NOT NULL 
              AND deadline_at < ?
        """, (now_str,)) as cursor:
            rows = await cursor.fetchall()
            overdue_list = [dict(r) for r in rows]

        if not overdue_list:
            return []

        overdue_ids = [d["id"] for d in overdue_list]
        placeholders = ",".join("?" for _ in overdue_ids)

        await db.execute(f"""
            UPDATE deadlines
            SET status = 'available',
                assigned_to = NULL,
                assigned_username = NULL,
                assigned_at = NULL,
                deadline_at = NULL,
                batch_id = NULL,
                extension_hours = 0
            WHERE id IN ({placeholders})
        """, overdue_ids)

        for d in overdue_list:
            await db.execute("""
                INSERT INTO assignment_log (guild_id, deadline_id, user_id, username, action)
                VALUES (?, ?, ?, ?, 'auto_returned_overdue')
            """, (d.get("guild_id", "global"), d["id"], d.get("assigned_to"), d.get("assigned_username")))

        await db.commit()
        return overdue_list
    except Exception as e:
        print(f"[DB Error] Lỗi auto_return_overdue_deadlines: {e}")
        return []
    finally:
        await db.close()


async def get_overdue_details(guild_id: str = "global") -> Dict[str, Any]:
    """
    Lấy thông tin chi tiết các deadline quá hạn:
    - active_overdue: Các deadline đang bị quá hạn (status='assigned', deadline_at < now).
    - auto_returned: Các deadline quá hạn đã bị tự động trả về kho (từ assignment_log).
    """
    now_str = get_now_str()
    db = await get_db()
    try:
        # 1. Active overdue
        async with db.execute(f"""
            SELECT * FROM deadlines 
            WHERE status = 'assigned' 
              AND deadline_at IS NOT NULL 
              AND deadline_at < ?
              AND {_deadline_guild_scope()}
            ORDER BY series_name ASC, chapter_number ASC
        """, (now_str, guild_id)) as cursor:
            active_rows = await cursor.fetchall()
            active_overdue = [dict(r) for r in active_rows]

        # 2. Auto returned overdue from assignment_log
        async with db.execute(f"""
            SELECT al.deadline_id, al.user_id, al.username, al.timestamp as returned_at,
                   d.chapter_number, d.chapter_name, d.series_name, d.role_type
            FROM assignment_log al
            JOIN deadlines d ON al.deadline_id = d.id
            WHERE al.action = 'auto_returned_overdue' 
              AND {_deadline_guild_scope('al.guild_id')}
            ORDER BY al.timestamp DESC
        """, (guild_id,)) as cursor:
            log_rows = await cursor.fetchall()
            auto_returned = [dict(r) for r in log_rows]

        return {
            "active_overdue": active_overdue,
            "auto_returned": auto_returned,
        }
    finally:
        await db.close()
