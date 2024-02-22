from django import template

from puzzle_editing.models import Puzzle

register = template.Library()


@register.inclusion_tag("tags/answer_list.html")
def formatted_answer_list(puzzle: Puzzle):
    """Displays a formatted version of all of the (potentially multiple) answers for a single puzzle"""

    return {
        "puzzle": puzzle,
        "answers": puzzle.answers.all(),
    }
