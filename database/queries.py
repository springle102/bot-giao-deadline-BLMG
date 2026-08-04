"""
Tất cả hàm query giao tiếp cơ sở dữ liệu SQLite async.
Đã cập nhật hỗ trợ phân tách dữ liệu độc lập theo từng Server (guild_id).
"""

import aiosqlite
from typing import List, Dict, Any, Optional, Tuple
from datetime import timedelta
from database.db import get_db
from utils.time_helper import get_now, get_now_str
from utils.chapter_helper import normalize_chapter_number, series_names_match


def _deadline_guild_scope(column: str = "guild_id") -> str:
    """Shared scope for current guild data plus legacy global rows."""
    return f"({column} = ? OR {column} = 'global' OR {column} IS NULL)"


async def get_available_deadlines(role_type: str, count: int, guild_id: str = "global") -> List[Dict[str, Any]]:
    """Lấy danh sách các deadline còn trống cho một vị trí trong Server cụ thể, ưu tiên chapter_number nhỏ nhất."""
    db = await get_db()
    try:
        async with db.execute(
            f"""SELECT * FROM deadlines
               WHERE {_deadline_guild_scope()}
                 AND role_type = ? 
                 AND status = 'available' 
               ORDER BY chapter_number ASC, series_name ASC LIMIT ?""",
            (guild_id, role_type, count)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        await db.close()


async def set_pending_deadlines(ids: List[int], user_id: str, guild_id: str = "global") -> None:
    """Cập nhật trạng thái thành 'pending' và gán user_id cho các deadline."""
    if not ids:
        return
    placeholders = ','.join('?' for _ in ids)
    now_str = get_now_str()
    query = f"""UPDATE deadlines 
               SET status = 'pending', assigned_to = ?, assigned_at = ? 
               WHERE id IN ({placeholders}) AND {_deadline_guild_scope()}"""
    
    db = await get_db()
    try:
        params = [user_id, now_str] + ids + [guild_id]
        await db.execute(query, params)
        await db.commit()
    finally:
        await db.close()


async def confirm_deadlines(ids: List[int], user_id: str, username: str, deadline_at: str, batch_id: str = None, guild_id: str = "global") -> None:
    """Cập nhật trạng thái từ 'pending' sang 'assigned', ghi nhận thông tin user và thời hạn."""
    if not ids:
        return
    placeholders = ','.join('?' for _ in ids)
    now_str = get_now_str()
    update_query = f"""
        UPDATE deadlines 
        SET status = 'assigned', 
            assigned_username = ?, 
            assigned_at = ?, 
            deadline_at = ?,
            batch_id = ?
        WHERE id IN ({placeholders}) AND status = 'pending' AND assigned_to = ? AND {_deadline_guild_scope()}
    """
    
    insert_log_query = """
        INSERT INTO assignment_log (guild_id, deadline_id, user_id, username, action)
        VALUES (?, ?, ?, ?, 'assigned')
    """
    
    db = await get_db()
    try:
        params = [username, now_str, deadline_at, batch_id] + ids + [user_id, guild_id]
        await db.execute(update_query, params)
        
        for deadline_id in ids:
            await db.execute(insert_log_query, (guild_id, deadline_id, user_id, username))
            
        await db.commit()
    finally:
        await db.close()


async def cancel_pending_deadlines(ids: List[int], guild_id: str = "global") -> None:
    """Hủy trạng thái 'pending' và chuyển về 'available'."""
    if not ids:
        return
    placeholders = ','.join('?' for _ in ids)
    query = f"""UPDATE deadlines 
               SET status = 'available', assigned_to = NULL, assigned_at = NULL 
               WHERE id IN ({placeholders}) AND status = 'pending' AND {_deadline_guild_scope()}"""
    
    db = await get_db()
    try:
        await db.execute(query, ids + [guild_id])
        await db.commit()
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
            SET status = 'available', assigned_to = NULL, assigned_at = NULL 
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
                            assigned_at = NULL, deadline_at = NULL, batch_id = NULL
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
    db = await get_db()
    try:
        async with db.execute(
            """SELECT count(*) as cnt FROM deadlines 
               WHERE assigned_to = ? AND drive_link = ? AND status IN ('assigned', 'pending')
                 AND (guild_id = ? OR guild_id IS NULL)""",
            (user_id, drive_link.strip(), guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            return (row["cnt"] if row else 0) > 0
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
               WHERE user_id = ? AND (guild_id = ? OR guild_id IS NULL)
               LIMIT 1""",
            (user_id, guild_id)
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
                batch_id = NULL
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
) -> bool:
    """Gia hạn deadline thêm số giờ cho một chap hoặc toàn bộ batch (nếu có)."""
    db = await get_db()
    try:
        if batch_id:
            await db.execute("""
                UPDATE deadlines
                SET deadline_at = ?
                WHERE batch_id = ? AND assigned_to = ? AND (guild_id = ? OR guild_id IS NULL)
            """, (new_deadline_at, batch_id, user_id, guild_id))

            async with db.execute(
                "SELECT id FROM deadlines WHERE batch_id = ? AND assigned_to = ? AND (guild_id = ? OR guild_id IS NULL)",
                (batch_id, user_id, guild_id)
            ) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    await db.execute("""
                        INSERT INTO assignment_log (guild_id, deadline_id, user_id, username, action)
                        VALUES (?, ?, ?, ?, ?)
                    """, (guild_id, row["id"], user_id, username, f"extended_{hours_extended}h"))
        else:
            await db.execute("""
                UPDATE deadlines
                SET deadline_at = ?
                WHERE id = ? AND assigned_to = ? AND (guild_id = ? OR guild_id IS NULL)
            """, (new_deadline_at, deadline_id, user_id, guild_id))

            await db.execute("""
                INSERT INTO assignment_log (guild_id, deadline_id, user_id, username, action)
                VALUES (?, ?, ?, ?, ?)
            """, (guild_id, deadline_id, user_id, username, f"extended_{hours_extended}h"))

        await db.commit()
        return True
    except Exception as e:
        print(f"[DB Error] Lỗi extend_deadline: {e}")
        return False
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
                batch_id = NULL
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

