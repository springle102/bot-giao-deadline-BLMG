"""
Script độc lập để xóa TRIỆT ĐỂ toàn bộ Slash Commands cũ trên Discord API.

Cách dùng:
    python reset_slash_commands.py

Script sẽ:
  1. Xóa tất cả Global Slash Commands.
  2. Xóa tất cả Guild Slash Commands trên mọi Server bot đang tham gia.
  3. (Tùy chọn) Nạp lại Cogs và đăng ký danh sách lệnh mới.

Yêu cầu:
  - File .env chứa DISCORD_TOKEN và (tùy chọn) GUILD_ID.
  - Thư viện discord.py đã cài đặt.
"""

import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import os

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

# Danh sách Cogs (giống bot.py)
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



class ResetBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        """Xóa toàn bộ slash commands cũ và đăng ký lệnh mới."""
        print(f"\n{'═' * 60}")
        print("  🛠️  SCRIPT RESET SLASH COMMANDS - BẮT ĐẦU")
        print(f"{'═' * 60}\n")

        # ── BƯỚC 1: Xóa sạch Global Commands ──
        print("🧹 [1/4] Đang xóa toàn bộ GLOBAL slash commands...")
        self.tree.clear_commands(guild=None)
        await self.tree.sync(guild=None)
        print("   ✅ Đã xóa sạch Global commands trên Discord API.\n")

        # ── BƯỚC 2: Xóa sạch Guild Commands trên TẤT CẢ server ──
        print("🧹 [2/4] Đang xóa Guild commands trên tất cả server bot tham gia...")
        for g in self.guilds:
            try:
                guild_obj = discord.Object(id=g.id)
                self.tree.clear_commands(guild=guild_obj)
                await self.tree.sync(guild=guild_obj)
                print(f"   ✅ Server: {g.name} (ID: {g.id}) — đã xóa sạch guild commands.")
            except Exception as e:
                print(f"   ⚠️ Server: {g.name} (ID: {g.id}) — lỗi: {e}")

        # Nếu có GUILD_ID trong .env nhưng bot không thấy guild đó trong self.guilds
        if GUILD_ID and GUILD_ID.strip().isdigit():
            target_id = int(GUILD_ID.strip())
            if not any(g.id == target_id for g in self.guilds):
                try:
                    guild_obj = discord.Object(id=target_id)
                    self.tree.clear_commands(guild=guild_obj)
                    await self.tree.sync(guild=guild_obj)
                    print(f"   ✅ Server ID {target_id} (từ .env) — đã xóa sạch guild commands.")
                except Exception as e:
                    print(f"   ⚠️ Server ID {target_id} (từ .env) — lỗi: {e}")
        print()

        # ── BƯỚC 3: Nạp lại Cogs mới ──
        print("🔄 [3/4] Đang nạp lại tất cả Cogs...")
        for cog in COGS:
            try:
                await self.load_extension(cog)
                print(f"   ✅ Loaded: {cog}")
            except Exception as e:
                print(f"   ❌ Failed: {cog} — {e}")
        print()

        # ── BƯỚC 4: Sync lệnh mới ──
        print("🚀 [4/4] Đang đăng ký slash commands mới...")
        if GUILD_ID and GUILD_ID.strip().isdigit():
            guild_obj = discord.Object(id=int(GUILD_ID.strip()))
            # Keep one command scope only; guild registration would duplicate
            # the same command while an old global record is propagating.
            synced = await self.tree.sync()
            print(f"   ✅ Đã sync {len(synced)} lệnh mới lên Server ID {GUILD_ID.strip()}")
        else:
            synced = await self.tree.sync()
            print(f"   ✅ Đã sync {len(synced)} lệnh mới (Global)")

        # ── KẾT THÚC ──
        print(f"\n{'═' * 60}")
        print("  🎉 RESET HOÀN TẤT!")
        print("  ℹ️  Discord có thể cần 1-2 phút để cập nhật menu gợi ý.")
        print("  ℹ️  Hãy thử: Thoát Discord hoàn toàn → Mở lại → Gõ /add")
        print(f"{'═' * 60}\n")

        # Tự đóng bot sau khi xong
        await self.close()


async def main():
    if not DISCORD_TOKEN:
        print("❌ Lỗi: Không tìm thấy DISCORD_TOKEN trong file .env!")
        return

    bot = ResetBot()
    print("🔄 Đang kết nối tới Discord API...")
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Đã hủy bởi người dùng.")
