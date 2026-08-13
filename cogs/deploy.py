"""Discord command to trigger a Render deployment through a deploy hook."""

import asyncio
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import discord
from discord import app_commands
from discord.ext import commands

from config import RENDER_DEPLOY_HOOK_URL, get_configured_admin_user_ids
from utils.embed_builder import create_error_embed, create_success_embed


_DEPLOY_COOLDOWN_SECONDS = 60
_deploy_lock = asyncio.Lock()
_last_deploy_at = 0.0


def _post_deploy_hook(url: str) -> int:
    """Send the POST request without exposing the secret hook URL."""
    request = Request(
        url,
        data=b"",
        headers={"User-Agent": "giao-deadline-discord-bot"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return response.status


class Deploy(commands.Cog):
    """Trigger a Render deployment for the configured bot service."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="deploy",
        description="Deploy phiên bản mới lên Render (chỉ ADMIN_USER_ID)",
    )
    async def deploy_command(self, interaction: discord.Interaction):
        """Start a Render deploy for an authorized user."""
        user_id = str(getattr(interaction.user, "id", ""))
        if user_id not in get_configured_admin_user_ids():
            return await interaction.response.send_message(
                embed=create_error_embed(
                    "Bạn không có quyền dùng lệnh này. Chỉ user ID trong `ADMIN_USER_ID` được phép deploy."
                ),
                ephemeral=True,
            )

        hook_url = (RENDER_DEPLOY_HOOK_URL or "").strip()
        parsed_url = urlparse(hook_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            return await interaction.response.send_message(
                embed=create_error_embed(
                    "Bot chưa được cấu hình `RENDER_DEPLOY_HOOK_URL`."
                ),
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        global _last_deploy_at
        async with _deploy_lock:
            remaining = _DEPLOY_COOLDOWN_SECONDS - (time.monotonic() - _last_deploy_at)
            if remaining > 0:
                return await interaction.followup.send(
                    embed=create_error_embed(
                        f"Vui lòng chờ {int(remaining) + 1} giây trước khi deploy lại."
                    ),
                    ephemeral=True,
                )

            try:
                status = await asyncio.to_thread(_post_deploy_hook, hook_url)
            except HTTPError as error:
                return await interaction.followup.send(
                    embed=create_error_embed(
                        f"Render từ chối yêu cầu deploy (HTTP {error.code}). Hãy kiểm tra lại Deploy Hook."
                    ),
                    ephemeral=True,
                )
            except (URLError, TimeoutError, OSError):
                return await interaction.followup.send(
                    embed=create_error_embed(
                        "Không thể kết nối tới Render để bắt đầu deploy."
                    ),
                    ephemeral=True,
                )

            if status not in (200, 202):
                return await interaction.followup.send(
                    embed=create_error_embed(
                        f"Render trả về HTTP {status}; deploy chưa được xác nhận."
                    ),
                    ephemeral=True,
                )

            _last_deploy_at = time.monotonic()
            return await interaction.followup.send(
                embed=create_success_embed(
                    "🚀 Đã gửi yêu cầu deploy lên Render. Render sẽ build và khởi động lại bot từ commit mới nhất."
                ),
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Deploy(bot))
