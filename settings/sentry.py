from typing import Any

from django.core.exceptions import DisallowedHost


def before_send_transaction(event, hint):
    if "user" not in event or not event["user"]:
        return None
    return event


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    if "exc_info" in hint:
        exc_type, exc_value, tb = hint["exc_info"]
        if isinstance(exc_value, DisallowedHost):
            return None
    return event
