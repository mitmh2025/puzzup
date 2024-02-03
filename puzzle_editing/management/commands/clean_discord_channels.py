import contextlib
import logging

from django.core.management.base import BaseCommand

from puzzle_editing import discord_integration as discord
from puzzle_editing.models import DiscordCategoryCache, DiscordTextChannelCache, Puzzle


class Command(BaseCommand):
    help = """Clean up discord status channels."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.logger = logging.getLogger("puzzle_editing.commands")
        self.dry_run = True
        self.client = discord.get_client()

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

    def organize_puzzles(self) -> None:
        """Fix up puzzle channels in discord.

        If sync is True, fix each puzzle channel's name and permissions, and
        move each puzzle channel to the correct category.
        """
        puzzles = Puzzle.objects.all()
        self.logger.info(f"Organizing {len(puzzles)} puzzles...")
        for p in puzzles:
            cached_channel = None
            if p.discord_channel_id:
                with contextlib.suppress(DiscordTextChannelCache.DoesNotExist):
                    cached_channel = DiscordTextChannelCache.objects.get(
                        id=p.discord_channel_id
                    )
            if not cached_channel:
                # channel id is empty OR points to an id that doesn't exist
                if p.discord_channel_id:
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
                discord.sync_puzzle_channel(self.client, p)

    def organize_categories(self, delete_empty: bool, sort_cats: bool):
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
            new_order = (
                DiscordCategoryCache.objects.exclude(puzzle_status="")
                .order_by("puzzle_status", "puzzle_status_index")
                .all()
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
                self.client._request(
                    "patch",
                    f"/guilds/{self.client.guild_id}/channels",
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
                    self.client.delete_channel(cat.id)

    def handle(self, *args, **options):
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
        self.organize_puzzles()
        # Process categories
        if delete_cats or sort_cats:
            self.organize_categories(delete_cats, sort_cats)
