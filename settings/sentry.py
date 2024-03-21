from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import DisallowedHost

if TYPE_CHECKING:
    from sentry_sdk._types import Event, Hint


def before_send_transaction(event: Event, hint: Hint) -> Event | None:
    if "user" not in event or not event["user"]:
        return None
    return event


def before_send(event: Event, hint: Hint) -> Event | None:
    if "exc_info" in hint:
        exc_type, exc_value, tb = hint["exc_info"]
        if isinstance(exc_value, DisallowedHost):
            return None
    return event
