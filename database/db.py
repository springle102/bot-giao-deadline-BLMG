import os
import aiosqlite

from config import DRIVE_SHARE_FAILURE_COOLDOWN_HOURS

DB_PATH = os.getenv("DB_PATH", "deadline_bot.db")

async def get_db() -> aiosqlite.Connection:
    """Tạo và trả về kết nối đến cơ sở dữ liệu."""
    dirname = os.path.dirname(DB_PATH)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db() -> None:
    """Khởi tạo cơ sở dữ liệu và tạo bảng nếu chưa có."""
    db = await get_db()
    try:
        await db.execute('''
        CREATE TABLE IF NOT EXISTS deadlines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL DEFAULT 'global',
            chapter_name TEXT NOT NULL,
            chapter_number INTEGER NOT NULL,
            series_name TEXT NOT NULL,
            role_type TEXT NOT NULL,
            drive_link TEXT,
            batch_id TEXT DEFAULT NULL,
            extension_hours INTEGER NOT NULL DEFAULT 0,
            assigned_to TEXT DEFAULT NULL,
            assigned_username TEXT DEFAULT NULL,
            assigned_at TEXT DEFAULT NULL,
            deadline_at TEXT DEFAULT NULL,
            status TEXT DEFAULT 'available',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        ''')
        
        await db.execute('''
        CREATE TABLE IF NOT EXISTS assignment_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL DEFAULT 'global',
            deadline_id INTEGER,
            user_id TEXT,
            username TEXT,
            action TEXT,
            timestamp TEXT DEFAULT (datetime('now','localtime'))
        );
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT NOT NULL,
            guild_id TEXT NOT NULL DEFAULT 'global',
            username TEXT,
            email TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (user_id, guild_id)
        );
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS server_settings (
            guild_id TEXT PRIMARY KEY,
            deadline_channel_id TEXT,
            admin_role_id TEXT,
            admin_log_channel_id TEXT,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS self_check_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL DEFAULT 'global',
            fingerprint TEXT NOT NULL UNIQUE,
            issue_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'warning',
            entity_key TEXT,
            details TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            first_seen_at TEXT DEFAULT (datetime('now','localtime')),
            last_seen_at TEXT DEFAULT (datetime('now','localtime')),
            resolved_at TEXT,
            notified_at TEXT
        );
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS drive_share_failures (
            guild_id TEXT NOT NULL DEFAULT 'global',
            drive_key TEXT NOT NULL,
            drive_link TEXT NOT NULL,
            failure_count INTEGER NOT NULL DEFAULT 1,
            last_error TEXT,
            last_failed_at TEXT DEFAULT (datetime('now','localtime')),
            blocked_until TEXT,
            PRIMARY KEY (guild_id, drive_key)
        );
        ''')

        # Recalculate persisted cooldowns after a policy change (for example,
        # from 24 hours to 4 hours). This makes existing failure records obey
        # the current cooldown instead of keeping their old blocked_until.
        cooldown_modifier = f"+{max(1, int(DRIVE_SHARE_FAILURE_COOLDOWN_HOURS))} hours"
        await db.execute(
            """UPDATE drive_share_failures
               SET blocked_until = datetime(last_failed_at, ?)
               WHERE last_failed_at IS NOT NULL""",
            (cooldown_modifier,),
        )

        # Auto migration: Tự động thêm các cột mới nếu database cũ chưa có
        try:
            await db.execute("ALTER TABLE server_settings ADD COLUMN admin_log_channel_id TEXT")
        except Exception:
            pass  # Cột đã tồn tại

        try:
            await db.execute("ALTER TABLE deadlines ADD COLUMN guild_id TEXT NOT NULL DEFAULT 'global'")
        except Exception:
            pass  # Cột đã tồn tại

        try:
            await db.execute(
                "ALTER TABLE deadlines ADD COLUMN extension_hours INTEGER NOT NULL DEFAULT 0"
            )
        except Exception:
            pass  # Cột đã tồn tại

        # Backfill số giờ gia hạn của các assignment đang hoạt động trong database cũ.
        # Từ các assignment mới trở đi, extension_hours là nguồn dữ liệu chính;
        # assignment_log vẫn được giữ lại cho mục đích audit.
        try:
            await db.execute("""
                UPDATE deadlines
                SET extension_hours = COALESCE((
                    SELECT SUM(CAST(substr(al.action, 10, length(al.action) - 10) AS INTEGER))
                    FROM assignment_log al
                    WHERE al.deadline_id = deadlines.id
                      AND al.action LIKE 'extended_%h'
                      AND al.timestamp >= COALESCE((
                          SELECT MAX(al2.timestamp)
                          FROM assignment_log al2
                          WHERE al2.deadline_id = deadlines.id
                            AND al2.action = 'assigned'
                      ), '0000-00-00 00:00:00')
                ), 0)
                WHERE status = 'assigned' AND COALESCE(extension_hours, 0) = 0
            """)
        except Exception as e:
            print(f"[DB Migration Error] Backfill extension_hours: {e}")

        try:
            await db.execute("ALTER TABLE assignment_log ADD COLUMN guild_id TEXT NOT NULL DEFAULT 'global'")
        except Exception:
            pass  # Cột đã tồn tại

        # Migration cho bảng users: Đảm bảo PRIMARY KEY là (user_id, guild_id)
        try:
            async with db.execute("PRAGMA table_info(users)") as cursor:
                cols = await cursor.fetchall()
                col_names = [c[1] for c in cols]
                pk_count = sum(1 for c in cols if c[5] > 0)

            if "guild_id" not in col_names:
                await db.execute("ALTER TABLE users ADD COLUMN guild_id TEXT NOT NULL DEFAULT 'global'")

            if pk_count < 2:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS users_new (
                        user_id TEXT NOT NULL,
                        guild_id TEXT NOT NULL DEFAULT 'global',
                        username TEXT,
                        email TEXT NOT NULL,
                        updated_at TEXT DEFAULT (datetime('now','localtime')),
                        PRIMARY KEY (user_id, guild_id)
                    );
                """)
                await db.execute("""
                    INSERT OR IGNORE INTO users_new (user_id, guild_id, username, email, updated_at)
                    SELECT user_id, COALESCE(guild_id, 'global'), username, email, updated_at FROM users;
                """)
                await db.execute("DROP TABLE users;")
                await db.execute("ALTER TABLE users_new RENAME TO users;")
        except Exception as e:
            print(f"[DB Migration Error] users table: {e}")

        # Tạo Index để tối ưu hóa truy vấn theo Guild
        await db.execute("CREATE INDEX IF NOT EXISTS idx_deadlines_guild ON deadlines(guild_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_log_guild ON assignment_log(guild_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_guild ON users(guild_id)")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_self_check_status "
            "ON self_check_findings(guild_id, status, issue_type)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_drive_share_failures_active "
            "ON drive_share_failures(guild_id, blocked_until)"
        )

        await db.commit()
    finally:
        await db.close()
