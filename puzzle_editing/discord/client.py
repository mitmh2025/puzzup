import re
import time
from typing import Any, TypeVar

import pydantic
import requests

from .cache import TimedCache
from .channel import Category, Channel, TextChannel, Thread, VoiceChannel

# Rough approximation of a json dictionary
JsonDict = dict[str, Any]

# A message payload can be a structure or just a string - strings will be
# treated like dict(content=payload)
MsgPayload = str | JsonDict

C = TypeVar("C", bound=Channel)

SUPPRESS_EMBEDS = 1 << 2


class DiscordError(Exception):
    """Generic discord integration failure."""


TextChannelCache = TimedCache[str, TextChannel]
VoiceChannelCache = TimedCache[str, VoiceChannel]
ThreadCache = TimedCache[str, Thread]


class ChannelData(pydantic.BaseModel):
    tcs: dict[str, TextChannel] = pydantic.Field(default_factory=dict)
    vcs: dict[str, VoiceChannel] = pydantic.Field(default_factory=dict)
    cats: dict[str, Category] = pydantic.Field(default_factory=dict)
    other: dict[str, Channel] = pydantic.Field(default_factory=dict)

    @property
    def total(self):
        return len(self.tcs) + len(self.vcs) + len(self.cats) + len(self.other)


def sanitize_channel_name(name: str) -> str:
    """A rough approximation of discord channel sanitization.

    The text is lowercased, spaces become hyphens, multiple hyphens are
    collapsed, and certain special characters are removed.

    This is to reduce false positives when we look at a puzzle's title and its
    corresponding channel name to see if we need to push an update.

    >>> sanitize_channel_name("somename")
    'somename'
    >>> sanitize_channel_name("Some NAME!")
    'some-name'
    >>> sanitize_channel_name("   A very {} spacious {} name    ".format(chr(9), chr(10)))
    'a-very-spacious-name'
    >>> sanitize_channel_name("Puzzle(🧩) Name! 👀💯💯💯")
    'puzzle🧩-name-👀💯💯💯'
    >>> sanitize_channel_name("---foo----bar---{}[]\\\\$%---")
    '-foo-bar-'
    """
    name = name.lower().strip()
    name = re.sub(r"\s", "-", name)
    name = re.sub(r"[#!,()'\":?<>{}|[\]@$%^&*=+/\\;.]", "", name)
    name = re.sub(r"-+", "-", name)
    return name


def delta(old: C, new: C) -> JsonDict:
    """Returns a dict of changed fields only.

    Specifically, the return value will only contain "id" plus the value of
    'new' for any field where 'new' differs from 'old'.

    >>> c1 = TextChannel(id="12345", guild_id="guild")
    >>> c2 = c1.copy(deep=True)
    >>> delta(c1, c2)
    {'id': '12345'}
    >>> c1.name = "puzzle-name-🧩"
    >>> c2.name = "  @Puzzle. .Name! 🧩!!@#$%!"
    >>> delta(c1, c2)
    {'id': '12345'}
    >>> c2.name = "P!name🧩"
    >>> delta(c1, c2)
    {'id': '12345', 'name': 'P!name🧩'}
    >>> c2.make_private()
    >>> c2.topic = "http://www.google.com/"
    >>> delta(c1, c2) == dict(
    ...     id = '12345',
    ...     name = 'P!name🧩',
    ...     permission_overwrites = [{'id': 'guild', 'type': 0, 'allow': '0', 'deny': '1024'}],
    ...     topic = "http://www.google.com/")
    True
    >>> c1.make_private()
    >>> c1.name = "pname🧩"
    >>> delta(c1, c2)
    {'id': '12345', 'topic': 'http://www.google.com/'}
    """
    d1 = old.dict()
    d2 = new.dict()

    def include(field: str):
        """Returns True if field should be included.

        A field is included if it's "id", or if the two dicts differ in it; if
        the field is 'name', then equality is tested on the sanitized versions.
        """
        if field == "id":
            return True
        if field == "name":
            n1 = sanitize_channel_name(getattr(old, "name", "") or "")
            n2 = sanitize_channel_name(getattr(new, "name", "") or "")
            return n1 != n2
        return d1.get(field) != d2.get(field)

    result = {}
    for field in d2:
        if include(field):
            result[field] = d2[field]
    return result


class Client:
    """
    A barebones discord API library.
    """

    _api_base_url = "https://discord.com/api/v10"
    _browser_base_url = "https://discord.com"

    def __init__(
        self,
        token: str,
        guild_id: str,
        tc_cache: TextChannelCache,
        vc_cache: VoiceChannelCache,
        thread_cache: ThreadCache,
    ):
        """Initialise the Discord client object"""
        self._token = token
        self.guild_id = guild_id
        self._tc_cache = tc_cache
        self._vc_cache = vc_cache
        self._thread_cache = thread_cache

    def _cache_tc(self, ch: TextChannel):
        """Save a channel to our cache."""
        self._tc_cache.set(ch.id, ch)

    def _cache_vc(self, ch: VoiceChannel):
        self._vc_cache.set(ch.id, ch)

    def _cache_thread(self, thread: Thread):
        """Save a thread to our cache."""
        self._thread_cache.set(thread.id, thread)

    def _raw_request(
        self, method: str, endpoint: str, json: Any = None
    ) -> requests.Response:
        """Send a request to discord and return the response"""
        headers = {
            "Authorization": f"Bot {self._token}",
            "X-Audit-Log-Reason": "via Puzzup integration",
        }
        api_url = f"{self._api_base_url}{endpoint}"
        if method in ["get", "delete"]:
            return requests.request(method, api_url, headers=headers)
        elif method in ["patch", "post", "put"]:
            headers["Content-Type"] = "application/json"
            return requests.request(method, api_url, headers=headers, json=json)
        msg = f"Unknown method {method}"
        raise ValueError(msg)

    def _request(self, method: str, endpoint: str, json: Any = None) -> Any:
        resp = self._raw_request(method, endpoint, json)
        if resp.status_code == 204:  # No Content
            return {}
        content = resp.json()
        resp.raise_for_status()
        return content

    def load_all_channels(self) -> ChannelData:
        """Load all channels in our guild."""
        cd = ChannelData()
        channels = self._request("get", f"/guilds/{self.guild_id}/channels")
        for ch in channels:
            if ch["type"] == 4:  # Category
                cd.cats[ch["id"]] = Category.parse_obj(ch)
            elif ch["type"] == 0:  # Text channel
                tc = TextChannel.parse_obj(ch)
                cd.tcs[tc.id] = tc
                self._cache_tc(tc)
            else:  # Voice and others
                cd.other[ch["id"]] = Channel.parse_obj(ch)
        return cd

    def get_all_text_channels(self) -> dict[str, TextChannel]:
        return self.load_all_channels().tcs

    def get_all_cats(self) -> dict[str, Category]:
        """Get all category channels, by id."""
        return self.load_all_channels().cats

    def _get_channel(self, channel_id: str) -> JsonDict:
        """Get a specific channel"""
        return self._request("get", f"/channels/{channel_id}")

    def get_text_channel(self, channel_id: str) -> TextChannel:
        """Get a text channel.

        Note that multiple calls to get_text_channel(id) will return DISTINCT
        objects, not multiple copies of the same object.
        """
        tc = self._tc_cache.get(channel_id)
        if tc is None:
            tc = TextChannel.parse_obj(self._get_channel(channel_id))
            self._cache_tc(tc)
        return tc.copy(deep=True)

    def get_voice_channel(self, channel_id: str) -> VoiceChannel:
        vc = self._vc_cache.get(channel_id)
        if vc is None:
            vc = VoiceChannel.parse_obj(self._get_channel(channel_id))
            self._cache_vc(vc)
        return vc.copy(deep=True)

    def get_thread(self, thread_id: str) -> Thread:
        thread = self._thread_cache.get(thread_id)
        if thread is None:
            thread = Thread.parse_obj(self._get_channel(thread_id))
            self._thread_cache.set(thread.id, thread)
        return thread.copy(deep=True)

    def save_channel_to_cat(
        self, ch: C, catname: str, cats: dict[str, Category] = None
    ) -> C:
        """Just like save_channel, but specifying a category by name.

        This will attempt to put the channel in the category `catname`,
        creating that category if it doesn't exist. If the category is full, it
        will try `catname`-1, then `catname`-2, etc. until it finds one that
        has space.
        """
        name_re = re.compile(r"^" + re.escape(catname) + r"(-\d+)?$", re.I)
        cats = cats or self.get_all_cats()
        parent = cats.get(ch.parent_id)
        if parent and name_re.match(parent.name):
            # We're already in a matching channel
            return self.save_channel(ch)

        cats_by_name = {cat.name: cat for cat in cats.values()}
        cat = None
        for i in range(10):
            name = catname if i == 0 else f"{catname}-{i}"
            cat = cats_by_name.get(name)
            if cat is not None:
                if parent is cat:
                    # This is the channel we're already in - save normally.
                    return self.save_channel(ch)
                # A channel by this name exists - try to move to it.
                ch.parent_id = cat.id
                try:
                    return self.save_channel(ch)
                except requests.HTTPError as e:
                    msg = e.response.json()
                    pids = msg.get("errors", {}).get("parent_id", {})
                    errs = pids.get("_errors", [])
                    max_ch_code = "CHANNEL_PARENT_MAX_CHANNELS"
                    if errs and errs[0].get("code") == max_ch_code:
                        # This channel has too many children, so keep going
                        continue
                    # Something else went wrong, just raise it.
                    raise
            else:
                # A category doesn't exist -> create it and move to it.
                new_cat = self.create_category(name)
                ch.parent_id = new_cat.id
                return self.save_channel(ch)
        # If we get to here, then we tried 10 possible categories and they
        # were all full, which means the server is maxed out on channels.
        msg = f"All 500 channels are in category {cat}?!"
        raise DiscordError(msg)

    def save_channel(self, ch: C) -> C:
        """Saves a channel.

        If the channel has no id, this will create a new channel from it. If it
        DOES have an id, this will patch the existing channel.

        Note that this will include all fields on the channel, even ones
        pydantic doesn't recognize, if they were provided at creation. This
        means that loading a channel and then saving it will preserve its
        properties, even the ones we didn't model.

        For patching existing channels, values that were never set will not be
        posted.

        Thus, save_channel(TextChannel(id="x", guild_id="y", topic="foo")) will
        update the topic of channel x WITHOUT modifying any other features -
        specifically, the update we send to discord will be a dict with just
        id, guild_id, and topic).

        Pydantic is smart enough to know what's been set, so e.g. in the above
        example including parent_id=None means that the save will clear the
        parent_id (even though None is the default value for parent_id). In
        other words, if you want to set a value to whatever the TextChannel
        default is for that value, you must specify it explicitly, otherwise we
        treat it as "keep the current value."
        """
        if not ch.id:
            pth = f"/guilds/{self.guild_id}/channels"
            rawch = self._request("post", pth, ch.dict(exclude={"id"}))
        else:
            # Clunky, but need fast fix
            if isinstance(ch, TextChannel):
                old = self.get_text_channel(ch.id)
            elif isinstance(ch, VoiceChannel):
                old = self.get_voice_channel(ch.id)
            else:
                old = pydantic.BaseModel()
            diff = delta(old, ch)
            if list(diff.keys()) == ["id"]:
                # Nothing changed -> no-op
                return ch
            rawch = self._request("patch", f"/channels/{ch.id}", diff)

        newch = ch.parse_obj(rawch)

        if isinstance(newch, TextChannel):
            self._cache_tc(newch)
        elif isinstance(newch, VoiceChannel):
            self._cache_vc(newch)

        return newch

    def save_thread(self, thread: Thread, message_id: str | None = None) -> Thread:
        if not thread.id:
            if not message_id:
                pth = f"/channels/{thread.parent_id}/threads"
            else:
                pth = f"/channels/{thread.parent_id}/messages/{message_id}/threads"
            rawch = self._request("post", pth, thread.dict(exclude={"id"}))
        else:
            # TODO
            return thread
        new_thread = Thread.parse_obj(rawch)
        self._cache_thread(new_thread)
        return new_thread

    def create_category(self, name: str) -> Category:
        """Creates a new category channel in the guild."""
        json = {"name": name, "type": 4}
        pth = f"/guilds/{self.guild_id}/channels"
        return Category.parse_obj(self._request("post", pth, json))

    def get_channels_in_guild(self) -> list:
        """Get the first 1000 channels in the guild."""
        return self._request("get", f"/guilds/{self.guild_id}/channels?limit=1000")

    def get_members_in_guild(self) -> list:
        """Get the first 1000 members in the guild."""
        return self._request("get", f"/guilds/{self.guild_id}/members?limit=1000")

    def get_member_by_id(self, discord_id: str) -> JsonDict | None:
        """Find a member by discord id."""
        members = self.get_members_in_guild()
        for member in members:
            if member["user"]["id"] == discord_id.strip():
                return member
        return None

    def get_channel_messages(
        self,
        channel_id: str,
        message_limit: int = 100,
        max_total: int | None = 100,
    ) -> list[JsonDict]:
        """Get messages in a channel.

        Retrieves the last `message_limit` messages; if message_limit is large,
        (usually >500) discord will rate limit us, and we'll just stop there.
        """
        message_list: list[JsonDict] = []
        last_message = False
        while not max_total or len(message_list) < max_total:
            url = f"/channels/{channel_id}/messages?limit={message_limit}"
            if last_message:
                url = f"{url}&before={last_message}"
            resp = self._raw_request("get", url)
            if resp.status_code in [429, 204]:
                # Ran out of messages (204 No Content), OR hit a rate limit (429)
                break
            messages = resp.json()
            if not messages:
                # Empty message list for some reason
                break
            message_list.extend(messages)
            last_message = min(m["id"] for m in messages)

            if len(messages) < message_limit:
                # Handle the common case where we don't have exactly message_limit
                # remaining
                break

            if resp.headers.get("x-ratelimit-remaining") == 0:
                # Naively sleep for 1 second (empirical backoff)
                time.sleep(1)
        return sorted(message_list, key=lambda msg: msg["id"])

    def get_message_authors_in_channel(
        self, channel_id: str, message_limit: int = 100
    ) -> set[str]:
        """Get message authors in a channel.

        Retrieves authors for the last `message_limit` messages, or however
        many we can load before getting rate-limited."""
        messages = self.get_channel_messages(channel_id, message_limit)
        from_people = [m for m in messages if "webook_id" not in m]
        authors = {m["author"]["id"] for m in from_people}
        return authors

    def delete_channel(self, channel_id: str) -> dict:
        """Delete a channel"""
        self._tc_cache.drop(channel_id)
        return self._request("delete", f"/channels/{channel_id}")

    def get_guild_roles(self) -> list:
        return self._request("get", f"/guilds/{self.guild_id}/roles")

    def post_message(self, channel_id: str, payload: MsgPayload) -> JsonDict:
        """Post a message to a channel.

        Messages will be split into chunks of at most 2000 characters.

        Payload can be a dict following the discord API, or a string; a string
        will be treated as dict(content=payload).
        """
        pth = f"/channels/{channel_id}/messages"
        # handle whether it's a string or JsonDict
        message = payload if isinstance(payload, str) else payload.get("content", "")
        offset = 0
        while True:
            chunk = message[offset : offset + 2000]
            # possibly chunk but try to preserve whole words
            # inspiration from https://github.com/fixmyrights/discord-bot/issues/11
            if offset + 2000 < len(message):
                reversed_chunk = chunk[::-1]
                length = min(reversed_chunk.find("\n"), reversed_chunk.find(" "))
                chunk = chunk[: 2000 - length]
                offset += 2000 - length
            else:
                offset = len(message)
            # if it's a JsonDict, try to keep the other attributes around
            if isinstance(payload, str):
                payload = {"content": chunk}
            else:
                payload["content"] = chunk
            if offset == len(message):
                return self._request("post", pth, payload)
            else:
                self._request("post", pth, payload)

    def delete_message(self, channel_id: str, message_id: str):
        """Delete a message"""
        self._request("delete", f"/channels/{channel_id}/messages/{message_id}")

    def add_member_to_thread(self, thread_id: str, user_id: str):
        self._request("put", f"/channels/{thread_id}/thread-members/{user_id}")

    def pin_message(self, channel_id: str, message_id: str):
        self._request("put", f"/channels/{channel_id}/pins/{message_id}")

    def get_thread_link(self, thread_id: str):
        return f"{self._browser_base_url}/channels/{self.guild_id}/{thread_id}"

    def remove_embeds(self, channel_id: str, message_id: str):
        payload = {"flags": SUPPRESS_EMBEDS}
        self._request("patch", f"/channels/{channel_id}/messages/{message_id}", payload)
