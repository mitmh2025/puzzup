from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentry_sdk._types import Event, Hint


def before_send_transaction(event: Event, hint: Hint) -> Event | None:
    if "user" not in event or not event["user"] or not event["user"]["id"]:
        return None
    return event
