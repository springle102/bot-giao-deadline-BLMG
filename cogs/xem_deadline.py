"""
Cog xử lý lệnh /xem-deadline
Hiển thị danh sách deadline đang được giao cho user.
"""

import discord
from discord import app_commands
from discord.ext import commands

from database.queries import get_user_deadlines
from utils.embed_builder import create_deadline_pages


class DeadlinePaginationView(discord.ui.View):
    """Allow the command owner to browse every deadline in one message."""

    def __init__(self, pages: list[discord.Embed], user_id: int):
        super().__init__(timeout=15 * 60)
        self.pages = pages
        self.user_id = user_id
        self.page_index = 0
        self.message = None

        self.previous_button = discord.ui.Button(
            label="Trang trước",
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )
        self.next_button = discord.ui.Button(
            label="Trang sau",
            emoji="▶️",
            style=discord.ButtonStyle.secondary,
            disabled=len(pages) <= 1,
        )
        self.previous_button.callback = self._previous_page
        self.next_button.callback = self._next_page
        self.add_item(self.previous_button)
        self.add_item(self.next_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Đây là bảng deadline riêng của người khác.",
                ephemeral=True,
            )
            return False
        return True

    def _update_buttons(self) -> None:
        self.previous_button.disabled = self.page_index == 0
        self.next_button.disabled = self.page_index >= len(self.pages) - 1

    async def _show_current_page(self, interaction: discord.Interaction) -> None:
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.pages[self.page_index],
            view=self,
        )

    async def _previous_page(self, interaction: discord.Interaction) -> None:
        if self.page_index > 0:
            self.page_index -= 1
        await self._show_current_page(interaction)

    async def _next_page(self, interaction: discord.Interaction) -> None:
        if self.page_index < len(self.pages) - 1:
            self.page_index += 1
        await self._show_current_page(interaction)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.HTTPException, discord.NotFound):
                pass


class XemDeadline(commands.Cog):
    """Cog xử lý lệnh xem deadline."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="xem-dl",
        description="Xem deadline bạn đang làm và đã nộp",
    )
    async def xem_deadline(self, interaction: discord.Interaction):
        """Xem danh sách deadline của user."""
        await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id) if interaction.guild_id else "global"
        deadlines = await get_user_deadlines(user_id, guild_id=guild_id)

        pages = create_deadline_pages(deadlines, interaction.user)
        view = (
            DeadlinePaginationView(pages, interaction.user.id)
            if len(pages) > 1
            else None
        )
        message = await interaction.followup.send(
            embed=pages[0],
            view=view,
            ephemeral=True,
            wait=True,
        )
        if view:
            view.message = message


async def setup(bot: commands.Bot):
    await bot.add_cog(XemDeadline(bot))
