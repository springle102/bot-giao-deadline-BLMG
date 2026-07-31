"""
Tất cả hàm query giao tiếp cơ sở dữ liệu SQLite async.
Đã cập nhật hỗ trợ phân tách dữ liệu độc lập theo từng Server (guild_id).
"""

import aiosqlite
from typing import List, Dict, Any, Optional
from database.db import get_db


async def get_available_deadlines(role_type: str, count: int, guild_id: str = "global") -> List[Dict[str, Any]]:
    """Lấy danh sách các deadline còn trống cho một vị trí trong Server cụ thể, xếp ngẫu nhiên."""
    db = await get_db()
    try:
        async with db.execute(
            """SELECT * FROM deadlines 
               WHERE (guild_id = ? OR guild_id IS NULL) 
                 AND role_type = ? 
                 AND status = 'available' 
               ORDER BY RANDOM() LIMIT ?""",
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
    query = f"""UPDATE deadlines 
               SET status = 'pending', assigned_to = ?, assigned_at = datetime('now','localtime') 
               WHERE id IN ({placeholders}) AND (guild_id = ? OR guild_id IS NULL)"""
    
    db = await get_db()
    try:
        params = [user_id] + ids + [guild_id]
        await db.execute(query, params)
        await db.commit()
    finally:
        await db.close()


async def confirm_deadlines(ids: List[int], user_id: str, username: str, deadline_at: str, batch_id: str = None, guild_id: str = "global") -> None:
    """Cập nhật trạng thái từ 'pending' sang 'assigned', ghi nhận thông tin user và thời hạn."""
    if not ids:
        return
    placeholders = ','.join('?' for _ in ids)
    update_query = f"""
        UPDATE deadlines 
        SET status = 'assigned', 
            assigned_username = ?, 
            assigned_at = datetime('now','localtime'), 
            deadline_at = ?,
            batch_id = ?
        WHERE id IN ({placeholders}) AND status = 'pending' AND assigned_to = ? AND (guild_id = ? OR guild_id IS NULL)
    """
    
    insert_log_query = """
        INSERT INTO assignment_log (guild_id, deadline_id, user_id, username, action)
        VALUES (?, ?, ?, ?, 'assigned')
    """
    
    db = await get_db()
    try:
        params = [username, deadline_at, batch_id] + ids + [user_id, guild_id]
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
               WHERE id IN ({placeholders}) AND status = 'pending' AND (guild_id = ? OR guild_id IS NULL)"""
    
    db = await get_db()
    try:
        await db.execute(query, ids + [guild_id])
        await db.commit()
    finally:
        await db.close()


async def return_deadline(deadline_id: int, user_id: str, guild_id: str = "global") -> bool:
    """Trả deadline, chuyển từ 'assigned' sang 'available' và xóa thông tin người nhận."""
    db = await get_db()
    try:
        async with db.execute(
            """SELECT assigned_username FROM deadlines 
               WHERE id = ? AND assigned_to = ? AND status = 'assigned' AND (guild_id = ? OR guild_id IS NULL)""",
            (deadline_id, user_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return False
            username = row['assigned_username']
            
        await db.execute("""
            UPDATE deadlines 
            SET status = 'available', assigned_to = NULL, assigned_username = NULL, 
                assigned_at = NULL, deadline_at = NULL, batch_id = NULL
            WHERE id = ? AND (guild_id = ? OR guild_id IS NULL)
        """, (deadline_id, guild_id))
        
        await db.execute("""
            INSERT INTO assignment_log (guild_id, deadline_id, user_id, username, action)
            VALUES (?, ?, ?, ?, 'returned')
        """, (guild_id, deadline_id, user_id, username))
        
        await db.commit()
        return True
    finally:
        await db.close()


async def mark_submitted(deadline_id: int, user_id: str, guild_id: str = "global") -> bool:
    """Đánh dấu một deadline đã được nộp."""
    db = await get_db()
    try:
        async with db.execute(
            """SELECT assigned_username FROM deadlines 
               WHERE id = ? AND assigned_to = ? AND status = 'assigned' AND (guild_id = ? OR guild_id IS NULL)""",
            (deadline_id, user_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return False
            username = row['assigned_username']
            
        await db.execute("UPDATE deadlines SET status = 'submitted' WHERE id = ? AND (guild_id = ? OR guild_id IS NULL)", (deadline_id, guild_id))
        
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
            """SELECT id, assigned_username FROM deadlines 
               WHERE assigned_to = ? AND status = 'assigned' AND (guild_id = ? OR guild_id IS NULL)""",
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
    count = 0
    db = await get_db()
    try:
        for chap_number, drive_link in items:
            chapter_name = f"Chap {chap_number}"
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
        async with db.execute("""
            SELECT * FROM deadlines 
            WHERE assigned_to = ? AND status = 'assigned' AND (guild_id = ? OR guild_id IS NULL)
            ORDER BY deadline_at ASC
        """, (user_id, guild_id)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_overdue_deadlines() -> List[Dict[str, Any]]:
    """Lấy danh sách các deadline đã quá hạn trên toàn hệ thống (phục vụ Scheduler)."""
    db = await get_db()
    try:
        async with db.execute("""
            SELECT * FROM deadlines 
            WHERE status = 'assigned' AND deadline_at < datetime('now','localtime')
        """) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_nearing_deadlines(hours_left: int) -> List[Dict[str, Any]]:
    """Lấy danh sách các deadline sắp đến hạn (phục vụ Scheduler)."""
    db = await get_db()
    try:
        async with db.execute("""
            SELECT * FROM deadlines 
            WHERE status = 'assigned' 
              AND deadline_at > datetime('now','localtime') 
              AND deadline_at <= datetime('now', '+' || ? || ' hours', 'localtime')
        """, (str(hours_left),)) as cursor:
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
    
    db = await get_db()
    try:
        async with db.execute(
            """SELECT status, count(*) as cnt FROM deadlines 
               WHERE (guild_id = ? OR guild_id IS NULL) 
               GROUP BY status""",
            (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                status = row['status']
                cnt = row['cnt']
                stats['total'] += cnt
                if status in stats:
                    stats[status] = cnt
                    
        async with db.execute(
            """SELECT count(*) as cnt FROM deadlines 
               WHERE status = 'assigned' 
                 AND deadline_at < datetime('now','localtime') 
                 AND (guild_id = ? OR guild_id IS NULL)""",
            (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            stats['overdue'] = row['cnt'] if row else 0
            
        async with db.execute(
            """SELECT role_type, status, count(*) as cnt FROM deadlines 
               WHERE (guild_id = ? OR guild_id IS NULL) 
               GROUP BY role_type, status""",
            (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                role = row['role_type']
                if role not in stats['per_role']:
                    stats['per_role'][role] = {'total': 0, 'available': 0, 'assigned': 0, 'submitted': 0, 'overdue': 0}
                stats['per_role'][role]['total'] += row['cnt']
                if row['status'] in stats['per_role'][role]:
                    stats['per_role'][role][row['status']] += row['cnt']
                    
        return stats
    finally:
        await db.close()


async def clean_expired_pending(minutes: int = 360) -> int:
    """Tự động dọn dẹp các deadline pending quá 6 giờ."""
    db = await get_db()
    try:
        async with db.execute("""
            UPDATE deadlines 
            SET status = 'available', assigned_to = NULL, assigned_at = NULL 
            WHERE status = 'pending' 
              AND assigned_at <= datetime('now', '-' || ? || ' minutes', 'localtime')
        """, (str(minutes),)) as cursor:
            await db.commit()
            return cursor.rowcount
    finally:
        await db.close()


async def cancel_deadline_admin(chapter_number: int, user_id: str, series_name: str = None, guild_id: str = "global") -> bool:
    """Admin hủy đăng ký deadline trong Server."""
    query = "SELECT id, assigned_username FROM deadlines WHERE chapter_number = ? AND assigned_to = ? AND status = 'assigned' AND (guild_id = ? OR guild_id IS NULL)"
    params = [chapter_number, user_id, guild_id]
    if series_name:
        query += " AND series_name = ?"
        params.append(series_name)
        
    db = await get_db()
    try:
        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            if not row:
                return False
            
            deadline_id = row['id']
            username = row['assigned_username']
            
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
        
        await db.commit()
        return True
    finally:
        await db.close()


async def get_deadline_by_chap_and_user(chapter_number: int, user_id: str, series_name: Optional[str] = None, guild_id: str = "global") -> Optional[Dict[str, Any]]:
    """Tìm một deadline cụ thể được giao cho user trong Server."""
    db = await get_db()
    try:
        if series_name:
            async with db.execute("""
                SELECT * FROM deadlines 
                WHERE chapter_number = ? AND assigned_to = ? AND status = 'assigned'
                  AND LOWER(series_name) LIKE LOWER(?)
                  AND (guild_id = ? OR guild_id IS NULL)
            """, (chapter_number, user_id, f"%{series_name}%", guild_id)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
        else:
            async with db.execute("""
                SELECT * FROM deadlines 
                WHERE chapter_number = ? AND assigned_to = ? AND status = 'assigned'
                  AND (guild_id = ? OR guild_id IS NULL)
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
        async with db.execute("""
            SELECT * FROM deadlines 
            WHERE chapter_number = ? AND assigned_to = ? AND status = 'assigned'
              AND (guild_id = ? OR guild_id IS NULL)
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
            "SELECT count(*) as total FROM deadlines WHERE batch_id = ? AND (guild_id = ? OR guild_id IS NULL)",
            (batch_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            total = row["total"] if row else 0

        if total == 0:
            return None

        async with db.execute(
            "SELECT count(*) as cnt FROM deadlines WHERE batch_id = ? AND status = 'submitted' AND (guild_id = ? OR guild_id IS NULL)",
            (batch_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            submitted = row["cnt"] if row else 0

        async with db.execute(
            "SELECT * FROM deadlines WHERE batch_id = ? AND status = 'assigned' AND (guild_id = ? OR guild_id IS NULL) ORDER BY chapter_number",
            (batch_id, guild_id)
        ) as cursor:
            remaining_rows = await cursor.fetchall()
            remaining = [dict(r) for r in remaining_rows]

        async with db.execute(
            "SELECT * FROM deadlines WHERE batch_id = ? AND (guild_id = ? OR guild_id IS NULL) ORDER BY chapter_number",
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
        async with db.execute("""
            SELECT * FROM deadlines 
            WHERE role_type = ? AND (guild_id = ? OR guild_id IS NULL)
            ORDER BY series_name ASC, chapter_number ASC
        """, (role_type, guild_id)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        await db.close()


async def save_user_email(user_id: str, username: str, email: str) -> None:
    """Lưu hoặc cập nhật địa chỉ email của thành viên."""
    db = await get_db()
    try:
        await db.execute("""
            INSERT INTO users (user_id, username, email, updated_at)
            VALUES (?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                email = excluded.email,
                updated_at = datetime('now','localtime')
        """, (user_id, username, email))
        await db.commit()
    finally:
        await db.close()


async def get_user_email(user_id: str) -> Optional[str]:
    """Lấy địa chỉ email của thành viên từ user_id."""
    db = await get_db()
    try:
        async with db.execute(
            "SELECT email FROM users WHERE user_id = ?",
            (user_id,)
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
