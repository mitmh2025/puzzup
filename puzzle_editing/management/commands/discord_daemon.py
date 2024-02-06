import asyncio

from django.core.management.base import BaseCommand

from puzzle_editing import discord_daemon


class Command(BaseCommand):
    def handle(self, *args, **options):
        asyncio.run(discord_daemon.asyncio_main())
