from __future__ import annotations

import contextlib
import itertools
import time
from collections.abc import Iterable
from enum import Enum
from typing import Any

import requests
from discord import PermissionOverwrite as DiscordPermissionOverrite
from discord import Permissions
from django import urls
from django.conf import settings
from django.db.models import Count, Q

from puzzle_editing import status
from puzzle_editing.discord.client import (
    DiscordError,
    JsonDict,
    MsgPayload,
    sanitize_channel_name,
)

from . import models as m
from .discord import Client


class PermissionOverwriteType(Enum):
    role = 0
    user = 1


class PermissionOverwrite:
    entity: str
    entity_type: PermissionOverwriteType
    permission: DiscordPermissionOverrite

    def __init__(
        self,
        /,
        entity: str,
        entity_type: PermissionOverwriteType,
        permission: DiscordPermissionOverrite | None = None,
    ):
        if permission is None:
            permission = DiscordPermissionOverrite()
        self.entity = entity
        self.entity_type = entity_type
        self.permission = permission

    def __eq__(self, __value: object) -> bool:
        if not isinstance(__value, PermissionOverwrite):
            return NotImplemented
        return (
            self.entity == __value.entity
            and self.entity_type == __value.entity_type
            and self.permission == __value.permission
        )

    def __hash__(self) -> int:
        return hash((self.entity, self.entity_type, self.permission.pair()))

    @classmethod
    def from_cache(cls, cached_overwrite: JsonDict) -> PermissionOverwrite:
        return cls(
            cached_overwrite["id"],
            PermissionOverwriteType[cached_overwrite["type"]],
            DiscordPermissionOverrite.from_pair(
                allow=Permissions(cached_overwrite["allow"]),
                deny=Permissions(cached_overwrite["deny"]),
            ),
        )

    @classmethod
    def from_api(cls, api_overwrite: JsonDict) -> PermissionOverwrite:
        return cls(
            api_overwrite["id"],
            PermissionOverwriteType(api_overwrite["type"]),
            DiscordPermissionOverrite.from_pair(
                allow=Permissions(int(api_overwrite["allow"])),
                deny=Permissions(int(api_overwrite["deny"])),
            ),
        )

    def to_api(self) -> JsonDict:
        allow, deny = self.permission.pair()
        return {
            "id": self.entity,
            "type": self.entity_type.value,
            "allow": str(allow.value),
            "deny": str(deny.value),
        }

    def to_cache(self) -> JsonDict:
        allow, deny = self.permission.pair()
        return {
            "id": self.entity,
            "type": self.entity_type.name,
            "allow": allow.value,
            "deny": deny.value,
        }


def get_client() -> Client | None:
    """Gets a discord client, or None if discord isn't enabled."""
    discord_bot_token = settings.DISCORD_BOT_TOKEN
    discord_guild_id = settings.DISCORD_GUILD_ID
    if discord_bot_token is None or discord_guild_id is None:
        return None
    return Client(
        discord_bot_token,
        discord_guild_id,
    )


def init_perms(c: Client, u: m.User):
    """Update u's visibility on every puzzle they're an author/editor on.

    This can be slow, so we only call it if a user's discord_user_id changes.
    """
    if not c or not u.discord_user_id:
        return
    must_see_puzzle = (
        Q(authors__pk=u.pk) | Q(editors__pk=u.pk) | Q(factcheckers__pk=u.pk)
    )
    puzzles = set(m.Puzzle.objects.filter(must_see_puzzle))
    for p in puzzles:
        sync_puzzle_channel(c, p)


def mention_user(discord_id: str) -> str:
    """Formats a discord id as a tag for a message."""
    return f"<@!{discord_id}>"


def mention_users(users: Iterable[m.User], skip_missing: bool = True) -> list[str]:
    """Get discord @tags from a bunch of users.

    Users without discord ids will be skipped, unless skip_missing is False, in
    which case their names will be returned instead of discord tags.
    """
    items = []
    for user in users:
        if user.discord_user_id:
            items.append(mention_user(user.discord_user_id))
        elif not skip_missing:
            items.append(str(user))
    return items


def _build_puzzle_channel_updates(
    puzzle: m.Puzzle,
) -> tuple[m.DiscordTextChannelCache | None, dict[str, Any]]:
    """Generate the set of updates (or creation parameters) for a puzzle channel"""
    current = None
    with contextlib.suppress(m.DiscordTextChannelCache.DoesNotExist):
        current = m.DiscordTextChannelCache.objects.get(id=puzzle.discord_channel_id)

    updates: dict[str, Any] = {}

    name = f"{puzzle.codename:.96}-{puzzle.id:03d}"
    if not current or sanitize_channel_name(current.name) != sanitize_channel_name(
        name
    ):
        updates["name"] = name

    topic = f"{puzzle.name}: {settings.PUZZUP_URL}{puzzle.get_absolute_url()}"
    if current and current.topic != topic:
        updates["topic"] = topic

    overwrites: dict[str, PermissionOverwrite] = {}
    if current:
        for cached_overwrite in current.permission_overwrites:
            overwrite = PermissionOverwrite.from_cache(cached_overwrite)
            overwrites[overwrite.entity] = overwrite
    else:
        assert settings.DISCORD_GUILD_ID is not None
        # Default to private for newly created channels
        overwrites[settings.DISCORD_GUILD_ID] = PermissionOverwrite(
            entity=settings.DISCORD_GUILD_ID,
            entity_type=PermissionOverwriteType.role,
            permission=DiscordPermissionOverrite(view_channel=False),
        )

    # Update individual user permissions
    # every author/editor MUST see the channel
    # this discord bot MUST see the channel
    autheds = itertools.chain(
        puzzle.authors.all(),
        puzzle.editors.all(),
        puzzle.factcheckers.all(),
    )
    must_see = {a.discord_user_id for a in autheds if a.discord_user_id}
    if settings.DISCORD_CLIENT_ID:
        must_see.add(settings.DISCORD_CLIENT_ID)
    # anyone who is spoiled CAN see the channel
    can_see = {s.discord_user_id for s in puzzle.spoiled.all() if s.discord_user_id}
    for uid in (
        m.User.get_eics()
        .exclude(discord_user_id="")
        .values_list("discord_user_id", flat=True)
    ):
        can_see.add(uid)
    # Loop over all users who must see and all who currently have overwrites;
    # add VIEW_CHANNEL to those who must have it and remove VIEW_CHANNEL from
    # those who can't have it. If someone is a spoiled user but not an author
    # or an editor, their status will be unchanged.
    for uid in must_see | overwrites.keys():
        overwrite = overwrites.setdefault(
            uid, PermissionOverwrite(uid, PermissionOverwriteType.user)
        )
        if uid in must_see:
            overwrite.permission.update(view_channel=True, manage_messages=True)
        elif uid not in can_see:
            overwrite.permission.update(view_channel=False, manage_messages=False)
    if not current or frozenset(overwrites.values()) != frozenset(
        PermissionOverwrite.from_cache(o) for o in current.permission_overwrites
    ):
        updates["permission_overwrites"] = [o.to_api() for o in overwrites.values()]

    return (current, updates)


def _set_puzzle_channel_category(
    c: Client, puzzle: m.Puzzle, current_category_id: str | None
) -> None:
    """Take an existing channel and make sure it's in the right category

    This will attempt to put the channel in a category matching `status`,
    creating that category if it doesn't exist. If the category is full, it
    will try category-1, then category-2, etc. until it finds one that has
    space.
    """
    categories = (
        m.DiscordCategoryCache.objects.filter(puzzle_status=puzzle.status)
        .annotate(text_channels_count=Count("text_channels"))
        .in_bulk()
    )

    categories_by_index = {cat.puzzle_status_index: cat for cat in categories.values()}
    for i in range(10):
        if category := categories_by_index.get(i):
            category_id = category.id
        else:
            # Need to make the category
            name = f"{settings.DISCORD_CATEGORY_PREFIX or ""}{status.get_display(puzzle.status)}{"" if i == 0 else f"-{i}"}"
            new_category = c.create_category(name)
            cache, _ = m.DiscordCategoryCache.objects.get_or_create(
                id=int(new_category["id"]),
                defaults={
                    "name": new_category["name"],
                    "position": new_category["position"],
                },
            )
            category_id = cache.id

        if current_category_id == category_id:
            # We're already in the right category
            return

        if category and category.text_channels_count >= 50:
            # This category seems full, try the next one
            continue

        # Try to move the channel to the category
        try:
            c.update_channel(puzzle.discord_channel_id, {"parent_id": category_id})
            m.DiscordTextChannelCache.objects.filter(
                id=puzzle.discord_channel_id, category_id=current_category_id
            ).update(category_id=category_id)
            return
        except requests.HTTPError as e:
            msg = e.response.json()
            pids = msg.get("errors", {}).get("parent_id", {})
            errs = pids.get("_errors", [])
            max_ch_code = "CHANNEL_PARENT_MAX_CHANNELS"
            if errs and errs[0].get("code") == max_ch_code:
                # This channel has too many children, so keep going, but sleep to try and avoid rate limits
                time.sleep(0.1)
                continue
            # Something else went wrong, just raise it.
            raise
    # If we get to here, then we tried 10 possible categories and they
    # were all full, which means the server is maxed out on channels.
    msg = f"All 500 channels are in status {puzzle.status}?!"
    raise DiscordError(msg)


def _find_puzzle_info_post(c: Client, channel: str) -> str | None:
    pins = c.get_channel_pins(channel)
    for pin in pins:
        if pin["author"]["id"] == settings.DISCORD_CLIENT_ID:
            return pin["id"]

    messages = c.get_channel_messages(channel, after="0", limit=1)
    for message in messages:
        if message["author"]["id"] == settings.DISCORD_CLIENT_ID:
            return message["id"]

    return None


def _sync_puzzle_info_post(c: Client | None, puzzle: m.Puzzle) -> None:
    if not c or not puzzle.discord_channel_id:
        return

    author_tags = mention_users(puzzle.authors.all(), False)
    message_content = {
        "content": "",
        "embeds": [
            {
                "type": "rich",
                "description": (
                    f'Here are some useful links for "{puzzle.name}":\n'
                    "\n"
                    f"* [PuzzUp entry]({settings.PUZZUP_URL}{urls.reverse("puzzle", kwargs={"id": puzzle.id})})\n"
                    f"* Here's a Google Doc where you can write your puzzle content: [Puzzle content]({settings.PUZZUP_URL}{urls.reverse('puzzle_content', kwargs={'id': puzzle.id})})\n"
                    f"* And another Google Doc for your solution here: [Puzzle solution]({settings.PUZZUP_URL}{urls.reverse('puzzle_solution', kwargs={'id': puzzle.id})})\n"
                    f"* Finally, a Google Drive folder where you can put any additional resources: [Puzzle resources]({settings.PUZZUP_URL}{urls.reverse('puzzle_resource', kwargs={'id': puzzle.id})})\n"
                ),
                "fields": [
                    {
                        "name": "Author(s)",
                        "value": ", ".join(author_tags),
                    },
                ],
            }
        ],
    }

    message_id: str | None = puzzle.discord_info_message_id or _find_puzzle_info_post(
        c, puzzle.discord_channel_id
    )
    if message_id:
        try:
            c.edit_message(puzzle.discord_channel_id, message_id, message_content)
        except requests.HTTPError as e:
            msg = e.response.json()
            if msg.get("code") == 30046:
                # We've edited this message too many times
                pass
            else:
                raise
    else:
        message_id = c.post_message(puzzle.discord_channel_id, message_content)["id"]
    if message_id and not puzzle.discord_info_message_id:
        c.pin_message(puzzle.discord_channel_id, message_id)
        puzzle.discord_info_message_id = message_id


def sync_puzzle_channel(c: Client | None, puzzle: m.Puzzle) -> None:
    """Ensure that a channel exists for the puzzle with the right configuration."""
    if not c:
        return

    cache, updates = _build_puzzle_channel_updates(puzzle)

    skip_create = False
    if puzzle.status in (status.DEFERRED, status.DEAD):
        skip_create = True
    if len(set(puzzle.authors.all()) | set(puzzle.editors.all())) <= 1:
        skip_create = True
    if not cache and skip_create:
        # don't create a new channel if we don't already have one
        return

    channel = None
    category_id = None
    if cache:
        category_id = cache.category_id
        if updates:
            channel = c.update_channel(cache.id, updates)
    else:
        channel = c.create_channel(updates)

    if channel:
        category_id = channel["parent_id"]
        # This will race with discord_daemon and that's fine - we want it to
        # overwrite us
        m.DiscordTextChannelCache.objects.get_or_create(
            id=channel["id"],
            defaults={
                "name": channel["name"],
                "position": channel["position"],
                "topic": channel["topic"] or "",
                "category_id": category_id,
                "permission_overwrites": [
                    PermissionOverwrite.from_api(o).to_cache()
                    for o in channel["permission_overwrites"]
                ],
            },
        )

    _sync_puzzle_info_post(c, puzzle)
    if channel and puzzle.discord_channel_id != channel["id"]:
        puzzle.discord_channel_id = channel["id"]
        puzzle.save()

    _set_puzzle_channel_category(c, puzzle, category_id)


def set_puzzle_visibility(
    c: Client | None, puzzle: m.Puzzle, user: m.User, visible: bool
) -> None:
    """Set the visibility of a puzzle channel for a user.

    This will add or remove the user from the channel's permission overwrites
    as appropriate.
    """
    if not c or not user.discord_user_id:
        return
    channel_id = puzzle.discord_channel_id
    if visible:
        c.set_channel_permission(
            channel_id,
            user.discord_user_id,
            PermissionOverwrite(
                entity=user.discord_user_id,
                entity_type=PermissionOverwriteType.user,
                permission=DiscordPermissionOverrite(view_channel=True),
            ).to_api(),
        )
    else:
        c.delete_channel_permission(channel_id, user.discord_user_id)


def make_testsolve_thread(c: Client | None, session: m.TestsolveSession) -> None:
    """Create a thread for a testsolve session."""
    if not c:
        return
    assert settings.DISCORD_TESTSOLVE_CHANNEL_ID is not None

    thread = c.create_thread(
        channel=settings.DISCORD_TESTSOLVE_CHANNEL_ID,
        params={
            "name": f"Session {session.id} - Puzzle {session.puzzle.id} ({session.puzzle.codename})",
            "type": 12,  # private thread
            "invitable": False,
        },
    )

    session.discord_thread_id = thread["id"]
    session.save()

    return


def announce_ppl(
    c: Client | None,
    channel_id: str,
    authors: Iterable[m.User] = (),
    editors: Iterable[m.User] = (),
    factcheckers: Iterable[m.User] = (),
):
    """Announces new spoiled users and editors.

    If c is None we do nothing.
    """
    if c is None:
        return
    msg = []
    authors = set(authors)
    editors = set(editors)
    factcheckers = set(factcheckers)
    if authors:
        tags = mention_users(authors, skip_missing=False)
        msg.append(f"New author(s): {', '.join(tags)}")
    if editors:
        tags = mention_users(editors, skip_missing=False)
        msg.append(f"New editor(s): {', '.join(tags)}")
    if factcheckers:
        tags = mention_users(factcheckers, skip_missing=False)
        msg.append(f"New factcheckers(s): {', '.join(tags)}")
    if msg:
        c.post_message(channel_id, "\n".join(msg))


def safe_post_message(
    c: Client | None,
    channel_id: str | None,
    payload: MsgPayload,
) -> JsonDict | None:
    """Post a message to a channel"""
    if c is None or not channel_id:
        return None
    try:
        return c.post_message(channel_id, payload)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return None
        raise
