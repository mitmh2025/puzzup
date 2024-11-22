import json

from django import template
from django.db.models import Q
from django.utils.safestring import mark_safe

from puzzle_editing import status
from puzzle_editing.models import Puzzle, User

register = template.Library()


@register.simple_tag()
def puzzle_search_list(user: User):
    if not user.is_authenticated:
        return "[]"
    if user.is_eic or user.has_perm("puzzle_editing.list_puzzle"):
        puzzles = Puzzle.objects.filter()
    else:
        puzzles = Puzzle.objects.filter(
            Q(status__in=[s for s in status.STATUSES if status.past_testsolving(s)])
            | Q(spoiled=user)
        )
    return mark_safe(json.dumps(list(puzzles.values("id", "codename").distinct())))
