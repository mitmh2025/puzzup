from django.conf import settings
from django.http import HttpRequest

from .models import SiteSetting


def site_password_set(request: HttpRequest):
    return {"SITE_PASSWORD_SET": bool(settings.SITE_PASSWORD)}


def auto_postprodding_enabled(_request):
    return {"AUTO_POSTPRODDING_ENABLED": settings.HUNT_REPO_URL != ""}


def testsolving_allowed(request: HttpRequest):
    allowed_by_user = request.user.is_authenticated and request.user.has_perm(
        "puzzle_editing.change_testsolvesession"
    )
    allowed_by_site_setting = not SiteSetting.get_bool_setting("TESTSOLVING_DISABLED")
    return {"TESTSOLVING_ALLOWED": allowed_by_user or allowed_by_site_setting}
