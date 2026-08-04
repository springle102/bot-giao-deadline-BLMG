"""
Discord Bot Giao Deadline - Entry Point
Bot tự động quản lý và giao deadline cho team edit truyện tranh webtoon.
"""

import asyncio
import discord
from discord.ext import commands

from config import DISCORD_TOKEN, GUILD_ID
from database.db import init_db
from utils.scheduler import DeadlineScheduler


# ── Danh sách Cogs ──────────────────────────────────────────────
COGS = [
    "cogs.xin_deadline",
    "cogs.xin_tre_deadline",
    "cogs.add_deadline",
    "cogs.nop_deadline",
    "cogs.xem_deadline",
    "cogs.thongke",
    "cogs.huy_deadline",
    "cogs.xoa_deadline",
    "cogs.dangky",
    "cogs.reset_data",
    "cogs.cauhinh",
    "cogs.help",
]




class DeadlineBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix="!",
            intents=intents,
            description="Bot Giao Deadline - Webtoon Editing Team",
        )
        self.scheduler = None

    async def setup_hook(self):
        """Khởi tạo cogs, db và sync slash commands chuẩn xác."""
        print(f"{'─' * 50}")
        print("🔄 Đang nạp các module (Cogs)...")
        for cog in COGS:
            try:
                await self.load_extension(cog)
                print(f"  ✅ Loaded: {cog}")
            except Exception as e:
                print(f"  ❌ Failed to load {cog}: {e}")

        # Khởi tạo database
        await init_db()
        print("  ✅ Database đã khởi tạo")

        # ── Wipe & Re-sync Slash Commands triệt để ──────────────
        # Bước 1: Xóa sạch TOÀN BỘ Global Commands trên Discord API
        #   (Xóa các lệnh cũ đã từng sync ở cấp Global trước đây)
        print("  🧹 Bước 1/3: Xóa sạch Global Commands trên Discord API...")
        self.tree.clear_commands(guild=None)
        await self.tree.sync(guild=None)
        print("  ✅ Global commands đã xóa sạch trên Discord API")

        if GUILD_ID and GUILD_ID.strip().isdigit():
            guild = discord.Object(id=int(GUILD_ID.strip()))
            try:
                # Bước 2: Xóa sạch TOÀN BỘ Guild Commands cũ trên Discord API
                #   (Xóa mọi lệnh cũ đã từng sync ở cấp Guild cho Server này)
                print(f"  🧹 Bước 2/3: Xóa sạch Guild Commands trên Server ID {GUILD_ID.strip()}...")
                self.tree.clear_commands(guild=guild)
                await self.tree.sync(guild=guild)
                print(f"  ✅ Guild commands đã xóa sạch trên Server ID {GUILD_ID.strip()}")

                # Bước 3: Nạp lại toàn bộ Cogs mới vào tree và sync lên Guild
                print("  🔄 Bước 3/3: Nạp lại lệnh mới và sync lên Guild...")
                for cog in COGS:
                    await self.reload_extension(cog)

                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                print(f"  ✅ Đã sync {len(synced)} slash commands mới lên Server ID {GUILD_ID.strip()}")
            except Exception as e:
                print(f"  ⚠️ Lỗi sync guild: {e}")
                # Fallback: sync global nếu guild sync thất bại
                for cog in COGS:
                    try:
                        await self.reload_extension(cog)
                    except Exception:
                        pass
                synced = await self.tree.sync()
                print(f"  ✅ Fallback: Đã sync {len(synced)} slash commands (Global)")
        else:
            # Không có GUILD_ID → Sync global
            # Bước 2 & 3: Nạp lại lệnh mới và sync lên Global
            print("  🔄 Bước 2-3/3: Nạp lại lệnh mới và sync Global...")
            for cog in COGS:
                try:
                    await self.reload_extension(cog)
                except Exception:
                    pass
            synced = await self.tree.sync()
            print(f"  ✅ Đã sync {len(synced)} slash commands (Global)")

        # Khởi tạo scheduler
        self.scheduler = DeadlineScheduler(self)
        self.scheduler.start()
        print("  ✅ Scheduler nhắc nhở tự động đã chạy")
        print(f"{'─' * 50}")

    async def on_ready(self):
        """Khi bot sẵn sàng hoàn toàn."""
        from utils.embed_builder import get_current_month_str
        month_str = get_current_month_str()
        try:
            await self.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name=f"📅 Deadline {month_str} | /help"
                )
            )
        except Exception as e:
            print(f"  ⚠️ Lỗi set activity: {e}")

        print(f"\n{'═' * 50}")
        print(f"  🤖 Bot đã Online: {self.user}")
        print(f"  📡 Đang kết nối ở {len(self.guilds)} Server(s)")
        print(f"  📅 Đợt Deadline: {month_str}")
        print(f"  🚀 BOT ĐÃ SẴN SÀNG HOẠT ĐỘNG! BẠN CÓ THỂ DÙNG /HELP")
        print(f"{'═' * 50}\n")


bot = DeadlineBot()


@bot.event
async def on_command_error(ctx, error):
    """Xử lý lỗi command."""
    if isinstance(error, commands.CommandNotFound):
        return
    print(f"[Error] {error}")


from keep_alive import keep_alive


# ── Main ────────────────────────────────────────────────────────
async def main():
    """Entry point chính."""
    print("\n🌐 Đang khởi chạy HTTP Keep-Alive Server...")
    keep_alive()

    print("\n🔄 Đang khởi động Bot Giao Deadline...")
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Bot đã được tắt bởi người dùng (Ctrl + C). Tạm biệt!")
