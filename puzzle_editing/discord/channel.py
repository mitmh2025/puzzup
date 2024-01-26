import typing as t

from pydantic import BaseModel, Field, validator

from .perm import Overwrite, Overwrites, Permission


class Channel(BaseModel):
    """Base discord channel."""

    id: str = ""
    name: str = ""
    type: int
    guild_id: str = ""
    position: int | None = None
    permission_overwrites: Overwrites = Field(default_factory=Overwrites)

    @validator("permission_overwrites", pre=True)
    def unpack_overwrites(cls, v):  # pylint: disable=no-self-argument
        """Unpack list of overwrites to an Overwrites object."""
        if isinstance(v, list):
            v = Overwrites.from_discord(v)
        return v

    class Config:
        """This tells pydantic to keep any extra attrs it finds.

        This is why we can parse a whole Channel structure and not lose the
        fields that aren't coded up here."""

        extra = "allow"

    @property
    def perms(self) -> Overwrites:
        return self.permission_overwrites

    def make_private(self):
        """Deny the VIEW_CHANNEL permission to @everyone."""
        self.perms.update_role(self.guild_id, deny="VIEW_CHANNEL")

    def make_public(self):
        """Release the VIEW_CHANNEL permission to @everyone.

        Note that technically, this just removes the denial, so the channel
        will inherit from its parent - if this is a text/voice channel in a
        category, the channel will only be visible to people who can see the
        parent category.
        """
        self.perms.update_role(self.guild_id, ignore="VIEW_CHANNEL")

    def is_public(self):
        p: Overwrite = self.perms.get_role(self.guild_id)
        return Permission.VIEW_CHANNEL not in p.deny

    def add_visibility(self, uids: t.Collection[str]):
        """Grant all specified discord ids VIEW_CHANNEL permission."""
        for uid in uids:
            self.perms.update_user(uid, allow="VIEW_CHANNEL")

    def add_visibility_roles(self, uids: t.Collection[str]):
        for uid in uids:
            self.perms.update_role(uid, allow="VIEW_CHANNEL")

    def rm_visibility(self, uids: t.Collection[str]):
        for uid in uids:
            self.perms.update_user(uid, ignore="VIEW_CHANNEL")

    def rm_visibility_roles(self, uids: t.Collection[str]):
        for uid in uids:
            self.perms.update_role(uid, ignore="VIEW_CHANNEL")


class ChildChannel(Channel):
    parent_id: str | None = None


class TextChannel(ChildChannel):
    """A Text Channel."""

    type: t.Literal[0] = 0
    topic: str | None = None


class VoiceChannel(ChildChannel):
    """A Voice Channel."""

    type: t.Literal[2] = 2


class Category(Channel):
    """A Category "channel" (discord models categories as channels)."""

    type: t.Literal[4] = 4


class Thread(BaseModel):
    """A Thread in a Text Channel."""

    type: int = 11
    id: str = ""
    name: str = ""
    guild_id: str = ""
    parent_id: str = ""

    class Config:
        """This tells pydantic to keep any extra attrs it finds.
        This is why we can parse a whole Channel structure and not lose the
        fields that aren't coded up here."""

        extra = "allow"
