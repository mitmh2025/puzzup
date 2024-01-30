# flake8: noqa
from settings.base import SITE_PASSWORD
from settings.base import *

DEBUG = True

EMAIL_SUBJECT_PREFIX = "[DEVELOPMENT] "

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

INTERNAL_IPS = [
    "localhost",
    "127.0.0.1",
]
if not SITE_PASSWORD:
    SITE_PASSWORD = "racecar"

INSTALLED_APPS.append("debug_toolbar")
MIDDLEWARE.append("debug_toolbar.middleware.DebugToolbarMiddleware")

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# Allow for local (per-user) override
try:
    from settings_local import *  # type: ignore
except ImportError:
    pass
