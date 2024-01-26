# ruff: noqa: F403

import os

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

from settings.base import *  # pylint: disable=unused-wildcard-import,wildcard-import

DEBUG = False
SECURE_SSL_REDIRECT = True

ALLOWED_HOSTS = ["ttbnl-2024-puzzup.herokuapp.com"]
PUZZUP_URL = "https://ttbnl-2024-puzzup.herokuapp.com"

sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN", default=""),
    integrations=[DjangoIntegration()],
    # Set traces_sample_rate to 1.0 to capture 100%
    # of transactions for performance monitoring.
    # We recommend adjusting this value in production.
    traces_sample_rate=1.0,
    # To set a uniform sample rate
    # Set profiles_sample_rate to 1.0 to profile 100%
    # of sampled transactions.
    # We recommend adjusting this value in production
    profiles_sample_rate=1.0,
    # If you wish to associate users to errors (assuming you are using
    # django.contrib.auth) you may enable sending PII data.
    send_default_pii=True,
)
