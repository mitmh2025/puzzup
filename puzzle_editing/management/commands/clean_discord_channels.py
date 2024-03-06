import datetime
import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from puzzle_editing import discord_integration as discord
from puzzle_editing import status
from puzzle_editing.models import DiscordCategoryCache, DiscordTextChannelCache, Puzzle


class Command(BaseCommand):
    help = """Clean up discord status channels."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.logger = logging.getLogger("puzzle_editing.commands")
        self.dry_run = True

    def add_arguments(self, parser):
        parser.add_argument(
            "--dryrun",
            "--dry-run",
            action="store_true",
            help=(
                "Don't actually changing anything (use --verbosity=3 to see "
                "what would change)."
            ),
        )
        parser.add_argument(
            "--delete-cats",
            action="store_true",
            help="Delete empty status categories",
        )
        parser.add_argument(
            "--sort-cats",
            action="store_true",
            help="Sort all status categories by status and suffix",
        )
        parser.add_argument(
            "--all", action="store_true", help="Shorthand for setting all the modes"
        )

    def organize_puzzles(self, client: discord.Client) -> None:
        """Fix up puzzle channels in discord.

        If sync is True, fix each puzzle channel's name and permissions, and
        move each puzzle channel to the correct category.
        """
        puzzles = Puzzle.objects.all()
        self.logger.info(f"Organizing {len(puzzles)} puzzles...")
        for p in puzzles:
            if p.discord_channel_id and p.status == status.DEAD:
                status_change_comment = (
                    p.comments.filter(status_change=status.DEAD)
                    .order_by("-date")
                    .first()
                )
                if (
                    status_change_comment
                    and status_change_comment.date
                    < timezone.now() - datetime.timedelta(days=7)
                ):
                    if self.dry_run:
                        self.logger.info(
                            f"Would delete channel for dead puzzle {p.name}"
                        )
                    else:
                        client.delete_channel(p.discord_channel_id)
                        p.discord_channel_id = ""
                        p.save()
                    continue

            if (
                p.discord_channel_id
                and len(set(p.authors.all()) | set(p.editors.all())) <= 1
            ):
                # If there have been no non-bot messages, then we can delete the channel
                messages = client.get_channel_messages(p.discord_channel_id)
                if all(
                    m["author"]["id"] == settings.DISCORD_CLIENT_ID for m in messages
                ):
                    if self.dry_run:
                        self.logger.info(
                            f"Would delete channel for single-author puzzle {p.name}"
                        )
                    else:
                        client.delete_channel(p.discord_channel_id)
                        p.discord_channel_id = ""
                        p.save()
                    continue

            if (
                p.discord_channel_id
                and not DiscordTextChannelCache.objects.filter(
                    id=p.discord_channel_id
                ).exists()
            ):
                # channel id is empty OR points to an id that doesn't exist
                self.logger.warning(
                    (
                        f"Puzzle {p.id} ({p.name}) has bad channel id"
                        f" ({p.discord_channel_id})"
                    ),
                )
                if self.dry_run:
                    self.logger.warning("Refusing to fix in dryrun mode.")
                else:
                    p.discord_channel_id = ""
                    p.save()
                continue

            if not self.dry_run:
                discord.sync_puzzle_channel(client, p)

    def organize_categories(
        self, client: discord.Client, delete_empty: bool, sort_cats: bool
    ) -> None:
        """Organize the status categories.

        If sort_cats is True, status categories will be sorted, by status order
        and then by number, and all categories will be placed at the end of the
        list.

        If delete_empty is True, status categories without channels in them will
        be deleted.

        If sort_cats is True, status categories will be sorted, by status order
        and then by number, so that e.g. Initial Idea will be first, followed by
        Initial Idea-1, Initial Idea-2 etc. if those exist, then the same for
        Awaiting Editor, etc.
        """

        # Put all status categories after the current max position. Sort first
        # so we don't have to worry about cache consistency. Discord doesn't
        # require new positions to be consequtive, so we can just start higher
        # than the possible number of channels
        starting_position = 1000
        if sort_cats:
            new_order = sorted(
                DiscordCategoryCache.objects.exclude(puzzle_status="").all(),
                key=lambda c: (
                    status.get_status_rank(c.puzzle_status),
                    c.puzzle_status_index,
                ),
            )
            new_order_request = []
            for i, cat in enumerate(new_order):
                if self.dry_run:
                    self.logger.info(
                        f"Would move category {cat.name} to position {starting_position + i}"
                    )
                new_order_request.append(
                    {
                        "id": cat.id,
                        "position": starting_position + i,
                    }
                )
            if not self.dry_run:
                client._request(
                    "patch",
                    f"/guilds/{client.guild_id}/channels",
                    json=new_order_request,
                )

        # Delete any status categories with no channels in them
        if delete_empty:
            for cat in DiscordCategoryCache.objects.filter(
                text_channels__isnull=True
            ).exclude(puzzle_status=""):
                if self.dry_run:
                    self.logger.info(f"Would delete category {cat.name}")
                else:
                    client.delete_channel(cat.id)

    def handle(self, *args, **options) -> None:
        delete_cats = options["delete_cats"] or options["all"]
        sort_cats = options["sort_cats"] or options["all"]
        self.dry_run = options["dryrun"]
        # Configure Logger
        vb = options.get("verbosity", 1)
        levels = {
            0: logging.ERROR,
            1: logging.WARN,
            2: logging.INFO,
            3: logging.DEBUG,
        }
        self.logger.setLevel(levels.get(vb, logging.DEBUG))
        client = discord.get_client()
        if not client:
            self.logger.error("No discord client found. Exiting.")
            return
        # Clean up each puzzle
        self.organize_puzzles(client)
        # Process categories
        if delete_cats or sort_cats:
            self.organize_categories(client, delete_cats, sort_cats)
