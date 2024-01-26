from __future__ import annotations

from django import urls
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

from puzzle_editing import models as m


class AuthenticatedHttpRequest(HttpRequest):
    user: m.User


def external_puzzle_url(request: HttpRequest, puzzle: m.Puzzle) -> str:
    """Get an external URL for a puzzle."""
    pth = urls.reverse("puzzle", kwargs={"id": puzzle.id})
    return request.build_absolute_uri(pth)


def group_required(*group_names: str):
    """Requires user membership in at least one of the groups passed in."""

    def in_groups(u: AbstractUser):
        if u.is_authenticated:
            if u.is_superuser or u.groups.filter(name__in=group_names).exists():
                return True
            msg = f"Need membership in one of [{', '.join(group_names)}]."
            raise PermissionDenied(msg)
        raise PermissionDenied

    return user_passes_test(in_groups)  # type: ignore
