"""Discord command to trigger and monitor a Render deployment."""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    RENDER_API_KEY,
    RENDER_DEPLOY_HOOK_URL,
    RENDER_SERVICE_ID,
    get_configured_admin_user_ids,
)
from database.queries import (
    create_deploy_run,
    get_pending_deploy_runs,
    update_deploy_run,
)
from utils.embed_builder import create_error_embed, create_success_embed


_DEPLOY_COOLDOWN_SECONDS = 60
_DEPLOY_POLL_INTERVAL_SECONDS = 10
_DEPLOY_MAX_WAIT_SECONDS = 30 * 60
_DEPLOY_TERMINAL_SUCCESS = {"live"}
_DEPLOY_TERMINAL_FAILURE = {
    "deactivated",
    "build_failed",
    "update_failed",
    "canceled",
    "pre_deploy_failed",
}
_deploy_lock = asyncio.Lock()
_last_deploy_at = 0.0


class RenderApiError(RuntimeError):
    """Raised when Render status cannot be read reliably."""

    def __init__(self, status: int, message: str = ""):
        super().__init__(message or f"Render API HTTP {status}")
        self.status = status


def _read_json_response(response: Any) -> Any:
    raw = response.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, (dict, list)) else {}


def _post_deploy_hook(url: str) -> tuple[int, Optional[str]]:
    """Trigger Render and return its deploy ID when Render provides one."""
    request = Request(
        url,
        data=b"",
        headers={"User-Agent": "giao-deadline-discord-bot"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        payload = _read_json_response(response)

    deploy_id = payload.get("deployId") or payload.get("id")
    if not deploy_id and isinstance(payload.get("deploy"), dict):
        deploy_id = payload["deploy"].get("id")
    return response.status, str(deploy_id) if deploy_id else None


def _render_api_get(api_key: str, url: str) -> tuple[int, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "giao-deadline-discord-bot",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, _read_json_response(response)
    except HTTPError as error:
        try:
            payload = _read_json_response(error)
        except Exception:
            payload = {}
        return error.code, payload


def _extract_deploy(payload: dict[str, Any]) -> dict[str, Any]:
    deploy = payload.get("deploy")
    if isinstance(deploy, dict):
        return deploy
    return payload


def _parse_timestamp(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return None


def _find_recent_deploy_id(payload: Any, requested_at: float) -> Optional[str]:
    """Find a hook-created deploy after a 202 response queued the request."""
    candidates: list[dict[str, Any]] = []
    raw_items = payload if isinstance(payload, list) else payload.get("deploys", [])
    if not isinstance(raw_items, list):
        return None

    for item in raw_items:
        deploy = _extract_deploy(item) if isinstance(item, dict) else {}
        deploy_id = deploy.get("id")
        created_at = _parse_timestamp(deploy.get("createdAt"))
        if (
            deploy_id
            and created_at is not None
            and created_at >= requested_at - 60
            and deploy.get("trigger") == "deploy_hook"
        ):
            candidates.append(deploy)

    if not candidates:
        return None
    candidates.sort(key=lambda item: _parse_timestamp(item.get("createdAt")) or 0)
    return str(candidates[-1]["id"])


def _render_status_message(status: str, deploy_id: Optional[str]) -> tuple[bool, discord.Embed]:
    status = status.lower()
    short_id = f" `{deploy_id}`" if deploy_id else ""
    if status in _DEPLOY_TERMINAL_SUCCESS:
        return True, create_success_embed(
            f"✅ Deploy Render đã hoàn tất thành công{short_id}. Bot đang chạy phiên bản mới."
        )
    if status in _DEPLOY_TERMINAL_FAILURE:
        return False, create_error_embed(
            f"❌ Deploy Render thất bại với trạng thái `{status}`{short_id}. "
            "Hãy mở mục Deploys trên Render để xem log build."
        )
    if status == "tracking_timeout":
        return False, create_error_embed(
            "Render chưa trả về trạng thái cuối sau 30 phút. Bot vẫn sẽ tiếp tục theo dõi "
            "và báo kết quả qua DM khi có kết quả."
        )
    return False, create_error_embed(
        f"Không thể theo dõi trạng thái deploy Render{short_id}. "
        "Hãy kiểm tra lại `RENDER_API_KEY` và `RENDER_SERVICE_ID`."
    )


async def _fetch_deploy_state(
    api_key: str,
    service_id: str,
    deploy_id: str,
) -> tuple[Optional[str], Optional[str]]:
    endpoint = (
        "https://api.render.com/v1/services/"
        f"{quote(service_id, safe='')}/deploys/{quote(deploy_id, safe='')}"
    )
    status_code, payload = await asyncio.to_thread(_render_api_get, api_key, endpoint)
    if status_code != 200:
        if status_code in {401, 403, 404}:
            raise RenderApiError(status_code)
        return None, None

    deploy = _extract_deploy(payload)
    status = deploy.get("status")
    return (str(status).lower() if status else None), str(deploy.get("id") or deploy_id)


async def _find_queued_deploy_id(
    api_key: str,
    service_id: str,
    requested_at: float,
) -> Optional[str]:
    endpoint = (
        "https://api.render.com/v1/services/"
        f"{quote(service_id, safe='')}/deploys?limit=20"
    )
    status_code, payload = await asyncio.to_thread(_render_api_get, api_key, endpoint)
    if status_code in {401, 403}:
        raise RenderApiError(status_code)
    if status_code != 200:
        return None
    return _find_recent_deploy_id(payload, requested_at)


async def _poll_deploy(
    api_key: str,
    service_id: str,
    deploy_id: Optional[str],
    requested_at: float,
) -> tuple[Optional[str], str]:
    deadline = time.monotonic() + _DEPLOY_MAX_WAIT_SECONDS
    current_id = deploy_id

    while time.monotonic() < deadline:
        if not current_id:
            current_id = await _find_queued_deploy_id(
                api_key,
                service_id,
                requested_at,
            )
            if not current_id:
                await asyncio.sleep(_DEPLOY_POLL_INTERVAL_SECONDS)
                continue

        status, resolved_id = await _fetch_deploy_state(
            api_key,
            service_id,
            current_id,
        )
        current_id = resolved_id or current_id
        if status in _DEPLOY_TERMINAL_SUCCESS | _DEPLOY_TERMINAL_FAILURE:
            return current_id, status
        await asyncio.sleep(_DEPLOY_POLL_INTERVAL_SECONDS)

    return current_id, "tracking_timeout"


class Deploy(commands.Cog):
    """Trigger a Render deployment and report its final status."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._resume_started = False

    async def _notify_completion(
        self,
        run: dict[str, Any],
        status: str,
        deploy_id: Optional[str],
        interaction: Optional[discord.Interaction] = None,
    ) -> None:
        _success, embed = _render_status_message(status, deploy_id)
        notified = False

        if interaction is not None:
            try:
                await interaction.followup.send(embed=embed, ephemeral=True)
                notified = True
            except Exception as error:
                print(f"[Deploy] Không gửi được followup sau deploy: {error}", flush=True)

        # If the bot restarted during its own deploy, the original interaction
        # cannot be used anymore. DM the authorized user from the persisted run.
        if not notified:
            try:
                user = self.bot.get_user(int(run["requested_by"]))
                if user is None:
                    user = await self.bot.fetch_user(int(run["requested_by"]))
                await user.send(embed=embed)
                notified = True
            except Exception as error:
                print(f"[Deploy] Không gửi được DM kết quả deploy: {error}", flush=True)

        if notified and status != "tracking_timeout":
            await update_deploy_run(
                int(run["id"]),
                notified_at=datetime.now(timezone.utc).isoformat(),
            )

    async def _track_run(
        self,
        run: dict[str, Any],
        interaction: Optional[discord.Interaction] = None,
    ) -> None:
        try:
            requested_at = _parse_timestamp(run.get("requested_at")) or time.time()
            deploy_id, status = await _poll_deploy(
                RENDER_API_KEY.strip(),
                RENDER_SERVICE_ID.strip(),
                run.get("deploy_id"),
                requested_at,
            )
        except RenderApiError as error:
            status = "tracking_error"
            deploy_id = run.get("deploy_id")
            print(f"[Deploy] Render API status check failed: HTTP {error.status}", flush=True)
        except (URLError, TimeoutError, OSError) as error:
            status = "tracking_error"
            deploy_id = run.get("deploy_id")
            print(f"[Deploy] Render API network error: {error}", flush=True)

        if status == "tracking_timeout":
            await update_deploy_run(
                int(run["id"]),
                deploy_id=deploy_id,
                status="tracking",
                last_status=status,
            )
            await self._notify_completion(run, status, deploy_id, interaction)
            asyncio.create_task(self._continue_tracking(run, deploy_id))
            return

        await update_deploy_run(
            int(run["id"]),
            deploy_id=deploy_id,
            status=status,
            last_status=status,
        )
        await self._notify_completion(run, status, deploy_id, interaction)

    async def _continue_tracking(
        self,
        run: dict[str, Any],
        deploy_id: Optional[str],
    ) -> None:
        """Continue in the background when an unusually long deploy times out."""
        while True:
            await asyncio.sleep(60)
            try:
                requested_at = _parse_timestamp(run.get("requested_at")) or time.time()
                deploy_id, status = await _poll_deploy(
                    RENDER_API_KEY.strip(),
                    RENDER_SERVICE_ID.strip(),
                    deploy_id,
                    requested_at,
                )
            except (RenderApiError, URLError, TimeoutError, OSError) as error:
                print(f"[Deploy] Tiếp tục theo dõi deploy thất bại: {error}", flush=True)
                continue

            if status == "tracking_timeout":
                continue

            await update_deploy_run(
                int(run["id"]),
                deploy_id=deploy_id,
                status=status,
                last_status=status,
            )
            await self._notify_completion(run, status, deploy_id)
            return

    @commands.Cog.listener()
    async def on_ready(self):
        if self._resume_started:
            return
        self._resume_started = True
        try:
            pending_runs = await get_pending_deploy_runs()
        except Exception as error:
            print(f"[Deploy] Không đọc được deploy đang theo dõi: {error}", flush=True)
            return

        for run in pending_runs:
            if run.get("last_status") and run.get("status") != "tracking":
                status = str(run["last_status"])
                asyncio.create_task(
                    self._notify_completion(run, status, run.get("deploy_id"))
                )
            else:
                asyncio.create_task(self._track_run(run))

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

        if not (RENDER_API_KEY or "").strip() or not (RENDER_SERVICE_ID or "").strip():
            return await interaction.response.send_message(
                embed=create_error_embed(
                    "Bot cần thêm `RENDER_API_KEY` và `RENDER_SERVICE_ID` để báo chính xác "
                    "khi deploy hoàn tất."
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

            requested_at = datetime.now(timezone.utc)
            try:
                response_status, deploy_id = await asyncio.to_thread(
                    _post_deploy_hook,
                    hook_url,
                )
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

            if response_status not in (200, 202):
                return await interaction.followup.send(
                    embed=create_error_embed(
                        f"Render trả về HTTP {response_status}; deploy chưa được xác nhận."
                    ),
                    ephemeral=True,
                )

            _last_deploy_at = time.monotonic()
            run_id = await create_deploy_run(
                requested_by=user_id,
                guild_id=str(interaction.guild_id) if interaction.guild_id else None,
                channel_id=str(interaction.channel_id) if interaction.channel_id else None,
                deploy_id=deploy_id,
                requested_at=requested_at.isoformat(),
            )

        run = {
            "id": run_id,
            "deploy_id": deploy_id,
            "requested_by": user_id,
            "requested_at": requested_at.isoformat(),
            "status": "tracking",
        }
        await interaction.followup.send(
            embed=create_success_embed(
                "🚀 Render đã nhận yêu cầu deploy. Bot đang theo dõi trạng thái và sẽ báo "
                "khi build hoàn tất hoặc thất bại."
            ),
            ephemeral=True,
        )
        await self._track_run(run, interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(Deploy(bot))
