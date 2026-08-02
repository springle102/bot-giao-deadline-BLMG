import os
import aiosqlite

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
            user_id TEXT PRIMARY KEY,
            username TEXT,
            email TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        ''')

        await db.execute('''
        CREATE TABLE IF NOT EXISTS server_settings (
            guild_id TEXT PRIMARY KEY,
            deadline_channel_id TEXT,
            admin_role_id TEXT,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        ''')

        # Auto migration: Tự động thêm cột guild_id nếu database cũ chưa có
        try:
            await db.execute("ALTER TABLE deadlines ADD COLUMN guild_id TEXT NOT NULL DEFAULT 'global'")
        except Exception:
            pass  # Cột đã tồn tại

        try:
            await db.execute("ALTER TABLE assignment_log ADD COLUMN guild_id TEXT NOT NULL DEFAULT 'global'")
        except Exception:
            pass  # Cột đã tồn tại

        try:
            await db.execute("ALTER TABLE users ADD COLUMN guild_id TEXT NOT NULL DEFAULT 'global'")
        except Exception:
            pass  # Cột đã tồn tại

        # Tạo Index để tối ưu hóa truy vấn theo Guild
        await db.execute("CREATE INDEX IF NOT EXISTS idx_deadlines_guild ON deadlines(guild_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_log_guild ON assignment_log(guild_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_guild ON users(guild_id)")

        await db.commit()
    finally:
        await db.close()
