import logging
import re
from typing import Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

# Rough approximation of a json dictionary
JsonDict = dict[str, Any]

# A message payload can be a structure or just a string - strings will be
# treated like dict(content=payload)
MsgPayload = str | JsonDict


class DiscordError(Exception):
    """Generic discord integration failure."""


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


class Client:
    """
    A barebones discord API library.
    """

    _api_base_url = "https://discord.com/api/v10"

    def __init__(
        self,
        token: str,
        guild_id: str,
    ):
        """Initialise the Discord client object"""
        self._token = token
        self.guild_id = guild_id

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
        if resp.status_code >= 400:
            logger.error(
                "Discord request returned error code %s: %s",
                content.get("code", "Unknown"),
                content.get("message", ""),
                exc_info=True,
            )
        resp.raise_for_status()
        return content

    def create_thread(self, channel: str, params: JsonDict) -> JsonDict:
        pth = f"/channels/{channel}/threads"
        return self._request("post", pth, params)

    def create_channel(self, params: JsonDict) -> JsonDict:
        """Create a new channel in the guild."""
        pth = f"/guilds/{self.guild_id}/channels"
        return self._request("post", pth, params)

    def update_channel(self, channel: str, updates: JsonDict) -> JsonDict:
        """Update a channel's settings."""
        pth = f"/channels/{channel}"
        return self._request("patch", pth, updates)

    def create_category(self, name: str) -> JsonDict:
        """Creates a new category channel in the guild."""
        json = {"name": name, "type": 4}
        pth = f"/guilds/{self.guild_id}/channels"
        return self._request("post", pth, json)

    def get_member_by_id(self, discord_id: str) -> JsonDict | None:
        """Find a member by discord id."""
        try:
            return self._request("get", f"/guilds/{self.guild_id}/members/{discord_id}")
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    def delete_channel(self, channel_id: str) -> dict:
        """Delete a channel"""
        return self._request("delete", f"/channels/{channel_id}")

    def post_message(self, channel_id: str, payload: MsgPayload) -> JsonDict:
        """Post a message to a channel.

        Messages will be truncated at 2000 characters.

        Payload can be a dict following the discord API, or a string; a string
        will be treated as dict(content=payload).
        """
        if isinstance(payload, str):
            payload = {"content": payload}
        payload["content"] = payload.get("content", "")[:2000]
        pth = f"/channels/{channel_id}/messages"
        return self._request("post", pth, payload)

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        """Add a reaction to a message"""
        self._request(
            "put", f"/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me"
        )

    def edit_message(
        self, channel_id: str, message_id: str, payload: dict[str, Any]
    ) -> JsonDict:
        return self._request(
            "patch", f"/channels/{channel_id}/messages/{message_id}", payload
        )

    def delete_message(self, channel_id: str, message_id: str):
        """Delete a message"""
        self._request("delete", f"/channels/{channel_id}/messages/{message_id}")

    def add_member_to_thread(self, thread_id: str, user_id: str):
        self._request("put", f"/channels/{thread_id}/thread-members/{user_id}")

    def remove_member_from_thread(self, thread_id: str, user_id: str):
        self._request("delete", f"/channels/{thread_id}/thread-members/{user_id}")

    def pin_message(self, channel_id: str, message_id: str):
        self._request("put", f"/channels/{channel_id}/pins/{message_id}")

    def get_channel_pins(self, channel_id: str) -> list[JsonDict]:
        return self._request("get", f"/channels/{channel_id}/pins")

    def get_channel_messages(
        self,
        channel_id: str,
        before: str | None = None,
        after: str | None = None,
        around: str | None = None,
        limit: int = 50,
    ) -> list[JsonDict]:
        params: dict[str, Any] = {}
        if before:
            params["before"] = before
        if after:
            params["after"] = after
        if around:
            params["around"] = around
        if limit:
            params["limit"] = limit
        return self._request(
            "get", f"/channels/{channel_id}/messages?{urlencode(params)}"
        )

    def set_channel_permission(
        self, channel_id: str, entity_id: str, permission: JsonDict
    ) -> None:
        self._request(
            "put", f"/channels/{channel_id}/permissions/{entity_id}", permission
        )

    def delete_channel_permission(self, channel_id: str, entity_id: str) -> None:
        self._request("delete", f"/channels/{channel_id}/permissions/{entity_id}")
