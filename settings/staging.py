import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

from settings.base import *  # noqa: F403

DEBUG = True

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_PORT = True

ALLOWED_HOSTS = ["puzzup-staging.letswriteahunt.com"]

sentry_sdk.init(
    dsn="https://4bc4e50087f7beb721fe439457e9350d@o4506595254599680.ingest.sentry.io/4506645028012032",
    integrations=[DjangoIntegration()],
    # Set traces_sample_rate to 1.0 to capture 100%
    # of transactions for performance monitoring.
    # We recommend adjusting this value in production.
    traces_sample_rate=1.0,
    # If you wish to associate users to errors (assuming you are using
    # django.contrib.auth) you may enable sending PII data.
    send_default_pii=True,
)
