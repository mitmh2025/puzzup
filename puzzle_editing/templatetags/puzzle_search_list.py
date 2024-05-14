import json

from django import template
from django.utils.safestring import mark_safe

from puzzle_editing.models import Puzzle

register = template.Library()


@register.simple_tag()
def puzzle_search_list():
    return mark_safe(json.dumps(list(Puzzle.objects.values("id", "codename"))))
