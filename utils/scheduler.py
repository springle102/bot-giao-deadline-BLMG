"""
Scheduler - Nhắc nhở deadline tự động.
Sử dụng discord.ext.tasks.loop để check deadline mỗi giờ.
"""

import discord
from discord.ext import tasks
from datetime import datetime

from config import (
    ROLE_TYPES, DEADLINE_CHANNEL_ID,
    REMINDER_THRESHOLD_HOURS, COLOR_WARNING, COLOR_ERROR,
    PENDING_EXPIRE_MINUTES,
)
from database.queries import (
    get_nearing_deadlines,
    clean_expired_pending,
)
from utils.integrity_checker import DeadlineIntegrityChecker
from utils.time_helper import format_remaining, get_now


class DeadlineScheduler:
    """Quản lý scheduler nhắc nhở deadline tự động."""

    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.integrity_checker = DeadlineIntegrityChecker(bot)
        self._already_reminded_stages: set[tuple[int, str]] = set()  # Track (deadline_id, stage) đã nhắc ("6h", "3h")

    def clear_reminded(self, deadline_ids: list[int]):
        """Xóa danh sách deadline ID khỏi cache đã nhắc nhở (dùng khi xin trễ deadline)."""
        for dl_id in deadline_ids:
            self._already_reminded_stages.discard((dl_id, "6h"))
            self._already_reminded_stages.discard((dl_id, "3h"))

    def start(self):
        """Bắt đầu scheduler."""
        self.check_deadlines.start()
        self.check_integrity.start()

    def stop(self):
        """Dừng scheduler."""
        self.check_deadlines.cancel()
        self.check_integrity.cancel()

    @tasks.loop(minutes=10)
    async def check_deadlines(self):
        """Check deadline mỗi 10 phút."""
        try:
            # 1. Dọn pending expired (> 6 giờ)
            cleaned = await clean_expired_pending(minutes=PENDING_EXPIRE_MINUTES)
            if cleaned > 0:
                print(f"[Scheduler] Đã dọn {cleaned} pending expired")

            # 2. Check deadline sắp hết hạn (mốc 6h và 3h)
            await self._check_nearing_deadlines()

            # 3. Check deadline quá hạn
            await self._check_overdue_deadlines()

        except Exception as e:
            print(f"[Scheduler] Lỗi: {e}")

    @check_deadlines.before_loop
    async def before_check(self):
        """Chờ bot ready trước khi chạy scheduler."""
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=30)
    async def check_integrity(self):
        """Repair legacy data and verify Drive access without a user command."""
        try:
            result = await self.integrity_checker.run()
            if result["extension_repairs"] or result["drive_notifications"]:
                print(f"[SelfCheck] Result: {result}")
        except Exception as e:
            print(f"[SelfCheck] Error: {e!s}")

    @check_integrity.before_loop
    async def before_integrity_check(self):
        await self.bot.wait_until_ready()

    async def _check_nearing_deadlines(self):
        """Nhắc nhở user khi deadline sắp hết hạn (mốc 6 tiếng và 3 tiếng)."""
        nearing = await get_nearing_deadlines(hours_left=REMINDER_THRESHOLD_HOURS)

        if not nearing:
            return

        # Gộp theo user_id và role_type
        grouped = {}
        for dl in nearing:
            key = (dl["assigned_to"], dl["role_type"])
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(dl)

        now = get_now()

        for (user_id, role_type), deadlines in grouped.items():
            # Tính thời gian còn lại (lấy deadline gần nhất trong nhóm)
            earliest_deadline = min(
                deadlines,
                key=lambda d: d["deadline_at"]
            )
            earliest_dt = datetime.fromisoformat(earliest_deadline["deadline_at"])
            remaining_seconds = (earliest_dt - now).total_seconds()

            # Xác định mốc nhắc (stage): 3h hay 6h
            if remaining_seconds <= 3 * 3600:
                stage = "3h"
                time_label = "3 tiếng"
            else:
                stage = "6h"
                time_label = "6 tiếng"

            # Kiểm tra nhóm deadline này đã nhắc mốc này chưa
            stage_keys = set((dl["id"], stage) for dl in deadlines)
            if stage_keys.issubset(self._already_reminded_stages):
                continue

            role_config = ROLE_TYPES.get(role_type, {})
            role_name = role_config.get("name", role_type)
            reminder_unit = role_config.get("reminder_unit", "chap")

            try:
                user = await self.bot.fetch_user(int(user_id))
                if not user:
                    continue

                remaining = format_remaining(earliest_dt)

                # Tạo embed nhắc nhở
                embed = discord.Embed(
                    title=f"⏰ Nộp deadline coi! <:florkbat:1533445482804940902> (Còn {time_label})!",
                    color=COLOR_WARNING,
                )

                series_names = set(dl["series_name"] for dl in deadlines)

                # Kiểm tra xem có chap nào thuộc batch_id không
                batch_id = deadlines[0].get("batch_id") if deadlines else None
                progress_info = ""
                if batch_id:
                    from database.queries import get_batch_progress
                    progress = await get_batch_progress(batch_id)
                    if progress:
                        progress_info = f"\n📊 Tiến độ batch: **{progress['submitted']}/{progress['total']}** chap đã nộp (còn **{progress['remaining_count']}** chap)"

                if reminder_unit == "day":
                    # Role 2 chap/ngày: gộp nhắc, không nhắc từng chap
                    embed.description = (
                        f"Bạn có **{len(deadlines)} chap {role_name}** cần nộp{progress_info}\n"
                        f"📚 Truyện: {', '.join(series_names)}\n"
                        f"⏰ Hạn còn: **{remaining}**"
                    )
                else:
                    # Role khác: liệt kê từng chap
                    chap_list = "\n".join(
                        f"📖 {dl['chapter_name']} - {dl['series_name']}"
                        for dl in deadlines
                    )
                    embed.description = (
                        f"Bạn có **{len(deadlines)} chap {role_name}** sắp hết hạn!{progress_info}\n\n"
                        f"{chap_list}\n\n"
                        f"⏰ Hạn còn: **{remaining}**"
                    )

                embed.set_footer(text="Hong nộp là tui gõ đầu đó nha!")

                await user.send(embed=embed)

                # Đánh dấu đã nhắc mốc này
                self._already_reminded_stages.update(stage_keys)

            except discord.Forbidden:
                print(f"[Scheduler] Không thể DM user {user_id} (đã tắt DM)")
            except Exception as e:
                print(f"[Scheduler] Lỗi nhắc user {user_id}: {e}")

    async def _check_overdue_deadlines(self):
        """Kiểm tra, tự động thu hồi deadline quá hạn về kho chung và thông báo cho User & Server Channel."""
        from database.queries import auto_return_overdue_deadlines
        overdue = await auto_return_overdue_deadlines()

        if not overdue:
            return

        # Gom nhóm deadline quá hạn bị thu hồi theo (user_id, role_type)
        grouped = {}
        for dl in overdue:
            user_id = dl.get("assigned_to")
            if not user_id:
                continue
            role_type = dl.get("role_type", "")
            key = (user_id, role_type)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(dl)

        for (user_id, role_type), deadlines in grouped.items():
            role_config = ROLE_TYPES.get(role_type, {})
            role_name = role_config.get("name", role_type)
            guild_id_val = deadlines[0].get("guild_id", "global") if deadlines else "global"

            # Tự động thu hồi quyền Drive cho các chap bị auto-return
            try:
                from database.queries import get_user_email, check_user_active_drive_link
                from utils.google_drive import revoke_drive_permission
                import asyncio

                user_email = await get_user_email(str(user_id), guild_id=guild_id_val)
                if user_email:
                    cancelled_links = set(dl.get("drive_link") for dl in deadlines if dl.get("drive_link"))
                    for link in cancelled_links:
                        still_active = await check_user_active_drive_link(str(user_id), link, guild_id=guild_id_val)
                        if not still_active:
                            await asyncio.to_thread(revoke_drive_permission, link, user_email)
            except Exception as e:
                print(f"[Scheduler] Lỗi revoke Drive user {user_id}: {e}")

            # 1. Gửi DM thông báo thu hồi cho user
            try:
                user = await self.bot.fetch_user(int(user_id))
                if user:
                    embed = discord.Embed(
                        title="🔴 Deadline Đã Quá Hạn & Bị Tự Động Thu Hồi!",
                        color=COLOR_ERROR,
                    )
                    chap_list = "\n".join(
                        f"• 📖 {dl['chapter_name']} ({dl['series_name']})"
                        for dl in deadlines
                    )
                    embed.description = (
                        f"Do đã quá thời hạn nộp, hệ thống đã **tự động hủy gán và thu hồi {len(deadlines)} chap {role_name}** của bạn "
                        f"để trả về kho deadline chung (`🟢 Available`) cho các thành viên khác nhận:\n\n"
                        f"{chap_list}\n\n"
                        f"Vui lòng chú ý thời hạn ở các đợt nhận deadline sau nhé! 🙏"
                    )
                    embed.set_footer(text="Hệ thống tự động quản lý deadline")
                    try:
                        await user.send(embed=embed)
                    except discord.Forbidden:
                        pass
            except Exception as e:
                print(f"[Scheduler] Lỗi gửi DM thu hồi user {user_id}: {e}")

            # 2. Gửi thông báo tới Channel của Server
            channel = None
            guild_id_val = deadlines[0].get("guild_id") if deadlines else None
            if guild_id_val and guild_id_val != "global" and guild_id_val.isdigit():
                guild = self.bot.get_guild(int(guild_id_val))
                if guild:
                    try:
                        from database.queries import get_server_setting
                        setting = await get_server_setting(guild_id_val)
                        cfg_chan_id = setting.get("deadline_channel_id") if setting else None
                        if cfg_chan_id and str(cfg_chan_id).isdigit():
                            channel = guild.get_channel(int(cfg_chan_id))
                    except Exception as e:
                        print(f"[Scheduler] Lỗi lấy channel_id từ DB: {e}")

            if not channel and DEADLINE_CHANNEL_ID and str(DEADLINE_CHANNEL_ID).isdigit():
                channel = self.bot.get_channel(int(DEADLINE_CHANNEL_ID))

            if channel:
                try:
                    embed = discord.Embed(
                        title="🔴 Tự Động Thu Hồi Deadline Quá Hạn",
                        color=COLOR_ERROR,
                        description=(
                            f"Hệ thống đã tự động thu hồi **{len(deadlines)} chap {role_name}** từ <@{user_id}> "
                            f"do quá hạn nộp và trả về kho deadline (`🟢 Available`):\n\n"
                            + "\n".join(
                                f"• 📖 {dl['chapter_name']} - {dl['series_name']}"
                                for dl in deadlines
                            )
                        ),
                    )
                    await channel.send(embed=embed)
                except Exception as e:
                    print(f"[Scheduler] Lỗi gửi tin nhắn channel thu hồi: {e}")
