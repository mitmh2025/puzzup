import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

from settings.base import *  # pylint: disable=unused-wildcard-import,wildcard-import

DEBUG = False
SECURE_SSL_REDIRECT = True

ALLOWED_HOSTS = ["puzzup.letswriteahunt.com"]

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
)
