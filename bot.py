"""
Discord Bot Giao Deadline - Entry Point
Bot tự động quản lý và giao deadline cho team edit truyện tranh webtoon.
"""

import asyncio
import traceback
import discord
from discord.ext import commands
from discord import app_commands

from config import DISCORD_TOKEN, FORCE_COMMAND_SYNC_ON_STARTUP, GUILD_ID
from database.db import init_db
from utils.scheduler import DeadlineScheduler


# ── Danh sách Cogs ──────────────────────────────────────────────
COGS = [
    "cogs.xin_deadline",
    "cogs.xin_tre_deadline",
    "cogs.doi_deadline",
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
    "cogs.deploy",
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
        self._command_sync_task = None

    async def _sync_commands_if_needed(self) -> None:
        """Run one bounded command sync only when explicitly requested."""
        if not FORCE_COMMAND_SYNC_ON_STARTUP:
            print("  ⏭️ Bỏ qua sync slash commands khi khởi động.")
            return

        sync_guild = None
        if GUILD_ID and GUILD_ID.strip().isdigit():
            sync_guild = discord.Object(id=int(GUILD_ID.strip()))
            # Đăng ký đúng bộ lệnh hiện tại trực tiếp cho Guild mục tiêu.
            # Cách này không dùng route Global đang bị Discord rate-limit.
            self.tree.copy_global_to(guild=sync_guild)
            print(
                f"  🔄 Ép sync slash commands cho Guild {GUILD_ID.strip()}..."
            )
        else:
            print("  🔄 Ép sync slash commands Global một lần theo cấu hình...")

        self._command_sync_task = asyncio.create_task(
            self.tree.sync(guild=sync_guild)
        )
        try:
            await asyncio.wait_for(
                self._command_sync_task,
                # Lần ép sync thủ công có thể phải chờ Retry-After ngắn của
                # Discord; cho phép tối đa 90 giây để request hoàn tất.
                timeout=90,
            )
        except asyncio.TimeoutError:
            self._command_sync_task.cancel()
            print(
                "  ⏳ Discord đang rate-limit slash commands; "
                "đã hủy retry để bot tiếp tục online."
            )
        except discord.HTTPException as error:
            print(
                f"  ⚠️ Không thể sync slash commands: HTTP {error.status}. "
                "Bot vẫn tiếp tục hoạt động với command hiện có."
            )
        finally:
            self._command_sync_task = None

    async def setup_hook(self):
        print("[BOOT] Drive diagnostics enabled: v2", flush=True)
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

        await self._sync_commands_if_needed()

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


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    """Log and report slash-command errors instead of Discord's terse default log.

    ``CommandTree`` logs ``Ignoring exception in command tree`` when it cannot
    resolve the command from the interaction (for example, a stale Discord
    command or a command schema mismatch).  The default logger often hides the
    useful traceback in hosted logs, so print structured context explicitly.
    """
    command = interaction.command
    command_name = getattr(command, "qualified_name", None)
    if not command_name:
        command_name = str((interaction.data or {}).get("name") or "unknown")

    guild_id = interaction.guild_id or "DM"
    user_id = getattr(interaction.user, "id", "unknown")
    print(
        f"[AppCommandError] command={command_name!r} guild={guild_id} "
        f"user={user_id} type={type(error).__name__}: {error}"
    )
    traceback.print_exception(type(error), error, error.__traceback__)

    if isinstance(error, app_commands.CommandNotFound):
        message = (
            "Lệnh Discord này đã cũ hoặc không còn tồn tại. "
            "Bạn hãy đóng/mở lại Discord rồi thử lại."
        )
    elif isinstance(error, app_commands.CommandSignatureMismatch):
        message = (
            "Lệnh Discord đang lệch phiên bản với bot. "
            "Bot cần được đồng bộ lại slash commands."
        )
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = (
            f"Bạn đang dùng lệnh quá nhanh. Hãy thử lại sau "
            f"{max(1, int(error.retry_after))} giây."
        )
    elif isinstance(error, app_commands.CheckFailure):
        message = "Bạn không có quyền hoặc không đủ điều kiện dùng lệnh này."
    elif isinstance(error, app_commands.TransformerError):
        message = "Dữ liệu nhập vào không hợp lệ. Hãy kiểm tra lại các tham số."
    else:
        message = (
            "Bot gặp lỗi khi xử lý lệnh. Admin hãy kiểm tra traceback trong log Render."
        )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception as response_error:
        print(
            f"[AppCommandError] Không thể gửi thông báo lỗi cho command="
            f"{command_name!r}: {response_error}"
        )
        traceback.print_exception(
            type(response_error), response_error, response_error.__traceback__
        )


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
