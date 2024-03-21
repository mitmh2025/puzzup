from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING

import discord
import discord.http
from django.conf import settings

from puzzle_editing.models import DiscordCategoryCache, DiscordTextChannelCache, User

if TYPE_CHECKING:
    from twisted.python.failure import Failure

logger = logging.getLogger(__name__)


class Client(discord.Client):
    guild_id: int

    def __init__(self, guild_id: int) -> None:
        intents = discord.Intents.default()
        intents.members = True
        self.guild_id = guild_id
        super().__init__(intents=intents)

    async def cache_user(self, user: discord.Member | discord.User) -> None:
        try:
            puzzup_user = await User.objects.aget(discord_user_id=str(user.id))
        except User.DoesNotExist:
            return

        puzzup_user.discord_username = (
            user.name
            if user.discriminator == "0"
            else f"{user.name}#{user.discriminator}"
        )
        if isinstance(user, discord.Member):
            puzzup_user.discord_nickname = user.nick or ""
        await puzzup_user.asave()

    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != self.guild_id:
            return

        await self.cache_user(member)

    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        if after.guild.id != self.guild_id:
            return

        await self.cache_user(after)

    async def on_user_update(self, before: discord.User, after: discord.User) -> None:
        if after.id != self.guild_id:
            return

        await self.cache_user(after)

    async def cache_category(self, category: discord.CategoryChannel) -> None:
        if category.guild.id != self.guild_id:
            return
        await DiscordCategoryCache.objects.aupdate_or_create(
            id=str(category.id),
            defaults={
                "name": category.name,
                "position": category.position,
            },
        )

    async def cache_text_channel(self, channel: discord.TextChannel) -> None:
        permission_overwrites = []
        for entity, overwrite in channel.overwrites.items():
            allow, deny = overwrite.pair()
            type = "role" if isinstance(entity, discord.Role) else "user"
            permission_overwrites.append(
                {
                    "id": str(entity.id),
                    "type": type,
                    "allow": allow.value,
                    "deny": deny.value,
                }
            )
        await DiscordTextChannelCache.objects.aupdate_or_create(
            id=str(channel.id),
            defaults={
                "name": channel.name,
                "topic": channel.topic or "",
                "position": channel.position,
                "category_id": str(channel.category_id),
                "permission_overwrites": permission_overwrites,
            },
        )

    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        if channel.guild.id != self.guild_id:
            return

        if isinstance(channel, discord.CategoryChannel):
            await self.cache_category(channel)
        elif isinstance(channel, discord.TextChannel):
            await self.cache_text_channel(channel)

    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        if after.guild.id != self.guild_id:
            return

        if isinstance(after, discord.CategoryChannel):
            await self.cache_category(after)
        elif isinstance(after, discord.TextChannel):
            await self.cache_text_channel(after)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if channel.guild.id != self.guild_id:
            return

        if isinstance(channel, discord.CategoryChannel):
            await DiscordCategoryCache.objects.filter(id=str(channel.id)).adelete()
        elif isinstance(channel, discord.TextChannel):
            await DiscordTextChannelCache.objects.filter(id=str(channel.id)).adelete()

    async def on_ready(self) -> None:
        logger.info("Connected to Discord")
        guild = self.get_guild(self.guild_id)
        if guild:
            for member in guild.members:
                await self.cache_user(member)

            cached_categories = {
                id
                async for id in DiscordCategoryCache.objects.values_list(
                    "id", flat=True
                )
            }
            cached_text_channels = {
                id
                async for id in DiscordTextChannelCache.objects.values_list(
                    "id", flat=True
                )
            }
            for channel in guild.channels:
                if isinstance(channel, discord.CategoryChannel):
                    await self.cache_category(channel)
                    cached_categories.discard(str(channel.id))
                elif isinstance(channel, discord.TextChannel):
                    await self.cache_text_channel(channel)
                    cached_text_channels.discard(str(channel.id))
            await DiscordCategoryCache.objects.filter(
                id__in=cached_categories
            ).adelete()
            await DiscordTextChannelCache.objects.filter(
                id__in=cached_text_channels
            ).adelete()


async def asyncio_main() -> None:
    if not settings.DISCORD_BOT_TOKEN or not settings.DISCORD_GUILD_ID:
        return

    async with Client(int(settings.DISCORD_GUILD_ID)) as client:
        await client.start(settings.DISCORD_BOT_TOKEN)


def twisted_errback(e: Failure) -> None:
    sys.excepthook(e.type, e.value, e.tb)


def twisted_main() -> None:
    from twisted.internet.defer import Deferred

    d = Deferred.fromFuture(asyncio.ensure_future(asyncio_main()))
    d.addErrback(twisted_errback)
