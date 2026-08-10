"""
Cog xử lý lệnh /xin-deadline.
Cho phép member xin deadline với button xác nhận.
"""

import asyncio
import uuid

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    ROLE_TYPES,
    ROLE_CHOICES,
    CONFIRM_TIMEOUT_SECONDS,
    DRIVE_SHARE_FAILURE_COOLDOWN_HOURS,
)
from database.queries import (
    get_available_deadlines,
    set_pending_deadlines,
    confirm_deadlines,
    cancel_pending_deadlines,
    rollback_deadline_assignment,
    get_user_email,
    get_user_active_count,
    record_drive_share_failure,
    resolve_drive_share_failure,
)
from utils.time_helper import calculate_deadline, calculate_total_days
from utils.embed_builder import (
    create_deadline_preview,
    create_deadline_confirm,
    create_error_embed,
)
from utils.google_drive import (
    clean_drive_error_message,
    friendly_drive_error,
    grant_drive_permission,
    is_transient_drive_error,
    revoke_drive_permission,
    should_block_drive_link,
    extract_drive_id,
)


_DRIVE_SHARE_LOCK = asyncio.Lock()


class DriveShareError(RuntimeError):
    """Raised when at least one Drive permission cannot be granted."""

    def __init__(self, message: str, *, link_blocked: bool = False):
        super().__init__(message)
        self.link_blocked = link_blocked


def _add_drive_status_field(embed: discord.Embed, status_messages: list[str]) -> None:
    """Add Drive status without exceeding Discord's 1024-character field limit."""
    if not status_messages:
        return

    status_text = "\n".join(status_messages)
    if len(status_text) > 1024:
        status_text = status_text[:980] + "\n… *(Nội dung lỗi đã được rút gọn)*"

    embed.add_field(
        name="📧 Cấp Quyền Google Drive",
        value=status_text,
        inline=False,
    )


def _log_drive_share_failure(link: str, error: object, link_blocked: bool) -> None:
    """Log structured classification without logging recipient email data."""
    drive_key = extract_drive_id(link) or link
    status = getattr(error, "status", None)
    reasons = sorted(getattr(error, "reasons", ()) or ())
    print(
        f"[DriveShare] failed drive={drive_key} blocked={link_blocked} "
        f"transient={is_transient_drive_error(error)} status={status} "
        f"reasons={reasons} message={str(error)[:240]}",
        flush=True,
    )


class ConfirmDeadlineView(discord.ui.View):
    """View with the confirm button."""

    def __init__(
        self,
        deadline_ids: list[int],
        user_id: int,
        username: str,
        role_type: str,
        chap_count: int,
        chapters: list[dict],
        deadline_at,
        total_days: int,
        original_interaction: discord.Interaction,
        guild_id: str = "global",
    ):
        super().__init__(timeout=CONFIRM_TIMEOUT_SECONDS)
        self.deadline_ids = deadline_ids
        self.user_id = user_id
        self.username = username
        self.role_type = role_type
        self.chap_count = chap_count
        self.chapters = chapters
        self.deadline_at = deadline_at
        self.total_days = total_days
        self.original_interaction = original_interaction
        self.guild_id = guild_id
        self.is_responded = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only the original user may press the button."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ Bạn không thể tương tác với nút này!", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Xác nhận", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Confirm receiving the deadline and complete Drive sharing atomically."""
        if self.is_responded:
            await interaction.response.send_message(
                "Yêu cầu nhận deadline này đã được xử lý rồi nè!", ephemeral=True
            )
            return

        # Lock the view before any await so two clicks cannot run two
        # competing Drive/database transactions at the same time.
        self.is_responded = True
        user_email = await get_user_email(str(self.user_id), guild_id=self.guild_id)
        if not user_email:
            self.is_responded = False
            await interaction.response.send_message(
                "Hình như tình yêu chưa đăng ký mail phải hông, gõ /dangky để đăng ký mail nho!",
                ephemeral=True,
            )
            return
        user_email = user_email.strip().lower()

        await interaction.response.defer()

        for btn in self.children:
            btn.disabled = True

        batch_id = str(uuid.uuid4()) if len(self.deadline_ids) > 1 else None
        created_links: list[str] = []
        drive_status_msgs: list[str] = []

        try:
            # Keep rows pending until every Drive link succeeds. This makes a
            # partial share compensatable without leaving an assigned deadline.
            unique_links = sorted(
                {
                    str(chapter.get("drive_link")).strip()
                    for chapter in self.chapters
                    if chapter.get("drive_link") and str(chapter.get("drive_link")).strip()
                }
            )

            for link in unique_links:
                drive_key = extract_drive_id(link) or "invalid-link"
                print(
                    f"[DriveDiag] share_start drive={drive_key}",
                    flush=True,
                )
                try:
                    async with _DRIVE_SHARE_LOCK:
                        result = await asyncio.to_thread(
                            grant_drive_permission, link, user_email, "writer", True
                        )
                except Exception as share_error:
                    print(
                        f"[DriveDiag] share_exception drive={drive_key} "
                        f"type={type(share_error).__name__}",
                        flush=True,
                    )
                    share_message = friendly_drive_error(
                        share_error, email=user_email, drive_url=link
                    )
                    link_blocked = should_block_drive_link(share_error)
                    _log_drive_share_failure(link, share_error, link_blocked)
                    if link_blocked:
                        await record_drive_share_failure(self.guild_id, link, share_message)
                    raise DriveShareError(
                        share_message,
                        link_blocked=link_blocked,
                    ) from share_error

                if not isinstance(result, tuple) or len(result) < 2:
                    share_message = "Google Drive trả về kết quả cấp quyền không hợp lệ."
                    _log_drive_share_failure(link, share_message, False)
                    raise DriveShareError(share_message, link_blocked=False)

                success, msg = result[0], result[1]
                print(
                    f"[DriveDiag] share_result drive={drive_key} success={success is True}",
                    flush=True,
                )
                drive_status_msgs.append(f"• {msg}")
                if success is not True:
                    share_message = clean_drive_error_message(
                        msg, email=user_email, drive_url=link
                    )
                    link_blocked = should_block_drive_link(msg)
                    _log_drive_share_failure(link, msg, link_blocked)
                    if link_blocked:
                        await record_drive_share_failure(self.guild_id, link, share_message)
                    raise DriveShareError(
                        share_message,
                        link_blocked=link_blocked,
                    )

                # A previous ambiguous/recipient-specific failure may have
                # blacklisted this Drive ID. Successful sharing is authoritative
                # evidence that the item is healthy, so clear that stale block.
                await resolve_drive_share_failure(self.guild_id, link)

                # grant_drive_permission reports pre-existing access with an
                # "Email ..." message. Do not revoke permissions that predate
                # this request during compensation.
                if not str(msg).strip().lower().startswith("email "):
                    created_links.append(link)

            deadline_at_str = self.deadline_at.strftime("%Y-%m-%d %H:%M:%S")
            confirmed = await confirm_deadlines(
                self.deadline_ids,
                str(self.user_id),
                self.username,
                deadline_at_str,
                batch_id=batch_id,
                guild_id=self.guild_id,
            )
            if confirmed is not True:
                raise RuntimeError("Không thể chuyển deadline từ pending sang assigned.")

        except Exception as error:
            print(f"[ERROR] confirm_btn failed: {error!s}")

            revoke_errors: list[str] = []
            for link in reversed(created_links):
                try:
                    revoke_result = await asyncio.to_thread(
                        revoke_drive_permission, link, user_email
                    )
                    revoke_ok, revoke_msg = revoke_result[0], revoke_result[1]
                    if not revoke_ok:
                        revoke_errors.append(f"{link}: {revoke_msg}")
                except Exception as revoke_error:
                    revoke_errors.append(
                        f"{link}: {friendly_drive_error(revoke_error, email=user_email, drive_url=link)}"
                    )

            await rollback_deadline_assignment(
                self.deadline_ids,
                str(self.user_id),
                guild_id=self.guild_id,
                reason="assignment_failed_drive_share",
            )

            if isinstance(error, DriveShareError):
                if error.link_blocked:
                    error_message = (
                        "Không thể giao deadline vì một link Google Drive không được chia sẻ thành công. "
                        f"Deadline sẽ được cho nhận lại sau {DRIVE_SHARE_FAILURE_COOLDOWN_HOURS} giờ kể từ lúc ghi nhận lỗi. "
                        "Trong thời gian này, link lỗi được tạm tránh ở các lần xin tiếp theo."
                        f"\n\nChi tiết: {error}"
                    )
                else:
                    error_message = (
                        "Google Drive đang tạm thời không phản hồi hoặc bị giới hạn. "
                        "Deadline đã được hủy và trả về kho; link không bị đánh dấu là link lỗi. "
                        "Bạn hãy thử xin lại sau ít phút."
                        f"\n\nChi tiết: {error}"
                    )
            else:
                error_message = (
                    "Không thể hoàn tất giao deadline vì trạng thái dữ liệu đã thay đổi. "
                    "Deadline đã được hủy và trả về kho; quyền Drive đã được dọn nếu cần. "
                    "Bạn hãy xin lại deadline."
                )
            if revoke_errors:
                error_message += (
                    "\n\n⚠️ Một số quyền Drive chưa thể thu hồi tự động; admin cần kiểm tra log: "
                    + "; ".join(revoke_errors)
                )

            try:
                await interaction.edit_original_response(
                    embed=create_error_embed(error_message), view=self
                )
            except Exception:
                pass
            self.stop()
            return

        try:
            embed = create_deadline_confirm(
                self.chapters,
                self.role_type,
                self.deadline_at,
                interaction.user,
                self.total_days,
            )
            _add_drive_status_field(embed, drive_status_msgs)
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as error:
            # At this point the business transaction already succeeded. A
            # Discord rendering failure must not revoke a valid assignment.
            print(f"[ERROR] confirmation display failed: {error!s}")
        finally:
            self.stop()

    async def on_timeout(self):
        """Automatically cancel pending deadlines after the confirmation timeout."""
        if not self.is_responded:
            for btn in self.children:
                btn.disabled = True

            await cancel_pending_deadlines(self.deadline_ids, guild_id=self.guild_id)

            try:
                message = await self.original_interaction.original_response()
                embed = create_error_embed(
                    "⏰ Đã hết 6 giờ xác nhận. Deadline đã được tự động hủy và trả về pool."
                )
                await message.edit(embed=embed, view=self)
            except Exception:
                pass


class XinDeadline(commands.Cog):
    """Cog xử lý lệnh xin deadline."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="xin-dl",
        description="Xin nhận deadline cho một vị trí cụ thể",
    )
    @app_commands.describe(
        role="Vị trí bạn muốn xin deadline",
        so_luong="Số lượng chap muốn nhận (mặc định 1, tối đa 2)",
    )
    @app_commands.choices(role=ROLE_CHOICES)
    async def xin_deadline(
        self,
        interaction: discord.Interaction,
        role: app_commands.Choice[str],
        so_luong: app_commands.Range[int, 1, 2] = 1,
    ):
        """Handle /xin-deadline."""
        await interaction.response.defer()

        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"
        role_type = role.value
        role_name = role.name

        if so_luong > 2:
            return await interaction.followup.send(
                embed=create_error_embed(
                    "Cục dừng chỉ được nhận 2 chap cùng lúc thôi hong được nhận nhiều đâu nha!"
                )
            )

        user_id = str(interaction.user.id)
        active_count = await get_user_active_count(user_id, guild_id=guild_id)
        if active_count > 0:
            return await interaction.followup.send(
                embed=create_error_embed(
                    "Hong có chơi zậy nha, cục dừng nhận 2 chap thôi, làm xong 2 chap rồi mới được xin tiếp nha!"
                )
            )

        available = await get_available_deadlines(role_type, so_luong, guild_id=guild_id)

        if not available:
            return await interaction.followup.send(
                embed=create_error_embed(f"Đã hết deadline {role_name}!")
            )

        if len(available) < so_luong:
            return await interaction.followup.send(
                embed=create_error_embed(
                    f"Chỉ còn **{len(available)}** chap {role_name}! "
                    f"Bạn yêu cầu {so_luong} chap."
                )
            )

        deadline_ids = [deadline["id"] for deadline in available]
        reserved = await set_pending_deadlines(deadline_ids, user_id, guild_id=guild_id)
        if reserved is False:
            return await interaction.followup.send(
                embed=create_error_embed(
                    "Các chap bạn vừa chọn đã được người khác nhận trước. "
                    "Bạn hãy xin lại deadline để bot chọn chap còn trống nhé!"
                )
            )

        deadline_at = calculate_deadline(role_type, so_luong)
        total_days = calculate_total_days(role_type, so_luong)
        embed = create_deadline_preview(
            available, role_type, deadline_at, interaction.user, total_days
        )

        view = ConfirmDeadlineView(
            deadline_ids=deadline_ids,
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            role_type=role_type,
            chap_count=so_luong,
            chapters=available,
            deadline_at=deadline_at,
            total_days=total_days,
            original_interaction=interaction,
            guild_id=guild_id,
        )

        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(XinDeadline(bot))
