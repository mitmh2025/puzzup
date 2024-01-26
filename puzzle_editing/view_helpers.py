from functools import wraps

from django import urls
from django.conf import settings
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

from . import models as m


def external_puzzle_url(request: HttpRequest, puzzle: m.Puzzle) -> str:
    """Get an external URL for a puzzle."""
    pth = urls.reverse("puzzle", kwargs={"id": puzzle.id})
    return request.build_absolute_uri(pth)


def group_required(*group_names):
    """Requires user membership in at least one of the groups passed in."""

    def in_groups(u):
        if u.is_authenticated:
            if u.is_superuser or u.groups.filter(name__in=group_names).exists():
                return True
            raise PermissionDenied

    return user_passes_test(in_groups)


def auto_postprodding_required(f):
    """Requires that auto-postprodding be enabled."""

    @wraps(f)
    def check(*args, **kwargs):
        if settings.HUNT_REPO_URL == "":
            raise PermissionDenied
        return f(*args, **kwargs)

    return check


def _user_can_testsolve(u):
    if u.is_authenticated and u.has_perm("puzzle_editing.change_testsolvesession"):
        return True
    if not m.SiteSetting.get_bool_setting("TESTSOLVING_DISABLED"):
        return True
    raise PermissionDenied


require_testsolving_enabled = user_passes_test(_user_can_testsolve)
