from django import template
from django.db.models import Exists, OuterRef, Subquery

from puzzle_editing.models import TestsolveParticipation, User

register = template.Library()


@register.inclusion_tag("tags/testsolve_session_list.html")
def testsolve_session_list(
    sessions,
    user,
    show_notes=False,
    show_leave_button=False,
    show_ratings=False,
    show_status=False,
    is_testsolve_admin=False,
):
    sessions = (
        sessions.annotate(
            is_author=Exists(
                User.objects.filter(
                    authored_puzzles__testsolve_sessions=OuterRef("pk"), id=user.id
                )
            ),
            is_spoiled=Exists(
                User.objects.filter(
                    spoiled_puzzles__testsolve_sessions=OuterRef("pk"), id=user.id
                )
            ),
        )
        .order_by("puzzle__priority")
        .select_related("puzzle")
        .prefetch_related("participations__user")
        .prefetch_related("puzzle__spoiled")
        .prefetch_related("puzzle__authors")
        .prefetch_related("puzzle__editors")
        .prefetch_related("puzzle__postprodders")
        .prefetch_related("puzzle__factcheckers")
        .prefetch_related("puzzle__tags")
    )

    if show_ratings:
        part_subquery = TestsolveParticipation.objects.filter(
            session=OuterRef("pk"), user=user
        )[:1]
        sessions = sessions.annotate(
            fun_rating=Subquery(part_subquery.values("fun_rating")),
            difficulty_rating=Subquery(part_subquery.values("difficulty_rating")),
        )

    return {
        "sessions": sessions,
        "show_notes": show_notes,
        "show_leave": show_leave_button,
        "show_ratings": show_ratings,
        "show_status": show_status,
        "is_testsolve_admin": is_testsolve_admin,
    }
