import logging
import re
from collections import Counter
from operator import attrgetter, itemgetter

from django.core.management.base import BaseCommand

from puzzle_editing import discord_integration as discord
from puzzle_editing import status
from puzzle_editing.discord import Category
from puzzle_editing.discord.client import C
from puzzle_editing.models import Puzzle

_stats = "|".join([re.escape(status.get_display(s)) for s in status.STATUSES])
_cat_re = re.compile(rf"^(?P<status>{_stats})(-(?P<num>\d+))?$")

_stat_order = {status.get_display(s): status.STATUSES.index(s) for s in status.STATUSES}


class DryRunError(Exception):
    """Indicates a dry run tried to write to the server."""


class DryRunClient:
    """A discord client that caches things like the set of all channels.

    Not useful for the site in general, because channels can change in other
    ways, but e.g. they probably won't change *during one run* of a management
    command (if they do, the command might break, but that's a small price to
    pay for making a command much, much faster).
    """

    dry_run: bool

    def __init__(self, client: discord.Client, dry_run=True, logger=None):
        self.dry_run = dry_run
        self.logger = logger or logging.getLogger("puzzle_editing.commands")
        self._client = client
        channel_data = client.load_all_channels()
        self.channels = channel_data.tcs
        self.cats = channel_data.cats

    def _debug(self, msg: str, *args, **kwargs):
        if self.dry_run:
            msg = "DRY_RUN: " + msg
        self.logger.debug(msg, *args, **kwargs)

    def save_channel(self, c: C) -> C:
        self._debug(f"Saving channel {c.name}")
        if self.dry_run:
            return c
        return self._client.save_channel(c)

    def save_channel_to_cat(
        self, c: C, catname: str, cats: dict[str, Category] = None
    ) -> C:
        self._debug(f"Saving channel {c.name} to category {catname}")
        if self.dry_run:
            return c
        return self._client.save_channel_to_cat(c, catname, cats)

    def delete_channel(self, channel_id: str) -> dict:
        """Delete a channel"""
        if channel_id not in self.channels:
            self._debug(f"Channel {channel_id} does not exist")
            return {}
        ch = self.channels[channel_id]
        self._debug(f"Deleting channel {ch.name}")
        if self.dry_run:
            return {}
        return self._client.delete_channel(channel_id)


class Command(BaseCommand):
    help = """Clean up discord status channels."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.logger = logging.getLogger("puzzle_editing.commands")
        self.d: DryRunClient = None  # type: ignore
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

    def organize_puzzles(self):
        """Fix up puzzle channels in discord.

        If sync is True, fix each puzzle channel's name and permissions, and
        move each puzzle channel to the correct category.
        """
        puzzles = Puzzle.objects.all()
        self.logger.info(f"Organizing {len(puzzles)} puzzles...")
        p: Puzzle
        for p in puzzles:
            ch = self.d.channels.get(p.discord_channel_id)
            if not ch:
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
                continue

            ch = discord.sync_puzzle_channel(p, ch.copy(deep=True))
            cat = p.get_status_display()
            self.d.save_channel_to_cat(ch, cat, cats=self.d.cats)

    def organize_categories(self, delete_empty: bool, sort_cats: bool):
        """Organize the status categories.

        If delete_empty is True, status categories (i.e. any category whose
        name is the display name of a status, optionally followed by -N for
        some integer N) without channels in them will be deleted.

        If sort_cats is True, status categories will be sorted, by status order
        and then by number, so that e.g. Initial Idea will be first, followed
        by Initial Idea-1, Initial Idea-2 etc. if those exist, then the same
        for Awaiting Editor, etc.
        """
        # Get categories and parents of channels
        cat_count = Counter()
        status_cat_ids = set()
        cats = []
        for c in self.d.channels.values():
            if c.parent_id:
                cat_count[c.parent_id] += 1
        for cat in self.d.cats.values():
            match = _cat_re.match(cat.name)
            # This is a status category
            if match:
                cat_info = cat.dict()
                stat = match.group("status")
                num = match.group("num") or 0
                num = int(num)
                cat_info["puzzup_status"] = match.group("status")
                cat_info["status_sort_key"] = (_stat_order[stat], num)
                cat_info["og_cat"] = cat
                cats.append(cat_info)
                status_cat_ids.add(cat.id)

        if delete_empty:
            self.logger.info(f"Checking {len(cats)} categories for emptiness.")
            for cat in list(cats):
                if cat["status_sort_key"][1] == 0:
                    # Don't delete the base 'Initial Idea', etc.
                    continue
                if not cat_count[cat["id"]]:
                    self.logger.info(f"Deleting empty category {cat['name']}")
                    self.d.delete_channel(cat["id"])
                    cats.remove(cat)

        if sort_cats:
            cats.sort(key=itemgetter("status_sort_key"))
            minpos = min(c["position"] for c in cats)

            # cats among or after status cats, but not status cats (hopefully empty)
            others = [
                c
                for c in self.d.cats.values()
                if c.position >= minpos and c.id not in status_cat_ids
            ]
            others.sort(key=attrgetter("position"))
            self.logger.info(
                f"Rearranging {len(cats)} status categories and"
                f" {len(others)} post-status categories."
            )

            for i, cat in enumerate(others):
                cat.position = minpos + i
                self.d.save_channel(cat)
            minpos += len(others)
            for i, cat in enumerate(cats):
                og_cat: Category = cat["og_cat"]
                og_cat.position = minpos + i
                self.d.save_channel(og_cat)

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
        # Set up our client as the default
        self.d = DryRunClient(
            discord.get_client(),
            dry_run=self.dry_run,
            logger=self.logger,
        )
        # Clean up each puzzle
        self.organize_puzzles()
        # Process categories
        if delete_cats or sort_cats:
            self.organize_categories(delete_cats, sort_cats)
