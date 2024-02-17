from typing import Any

import sentry_sdk
from django.core.exceptions import DisallowedHost
from sentry_sdk.integrations.django import DjangoIntegration

from settings.base import *  # noqa: F403

DEBUG = True

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_PORT = True

ALLOWED_HOSTS = ["puzzup-staging.letswriteahunt.com"]


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


sentry_sdk.init(
    dsn="https://d3e924ca08ac12b462b61e5e17c73523@o4506595254599680.ingest.sentry.io/4506595264954368",
    integrations=[DjangoIntegration()],
    # Set traces_sample_rate to 1.0 to capture 100%
    # of transactions for performance monitoring.
    # We recommend adjusting this value in production.
    traces_sample_rate=1.0,
    # If you wish to associate users to errors (assuming you are using
    # django.contrib.auth) you may enable sending PII data.
    send_default_pii=True,
    environment="staging",
    before_send=before_send,
    before_send_transaction=before_send_transaction,
)
