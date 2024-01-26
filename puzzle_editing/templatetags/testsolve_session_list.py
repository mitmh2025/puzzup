from django import template
from django.db.models import Exists, OuterRef, Subquery

from puzzle_editing.models import TestsolveParticipation, User, get_user_role

register = template.Library()


@register.inclusion_tag("tags/testsolve_session_list.html", takes_context=True)
def testsolve_session_list(
    context,
    sessions,
    user,
    show_notes=False,
    show_leave_button=False,
    show_ratings=False,
    coordinator=False,
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

    # handroll participants join to avoid queries
    # TODO: getting important tag names is also costing one query per session
    # (but that one's a lot more annoying to implement and handrolling its
    # prefetch may not be worth the performance gain)
    sessions = list(sessions)

    for session in sessions:
        session.opt_participants = []

    id_to_index = {session.id: i for i, session in enumerate(sessions)}

    for testsolve in sorted(
        set().union(*(session.participations.all() for session in sessions)),
        key=lambda p: p.pk,
    ):
        session = sessions[id_to_index[testsolve.session_id]]
        if get_user_role(testsolve.user, session.puzzle) in [
            None,
            "postprodder",
            "factchecker",
        ]:
            session.opt_participants.append(
                (testsolve.user.username, testsolve.user.credits_name)
            )

    for session in sessions:
        session.participants_html = (
            str(len(session.opt_participants))
            + (
                " participant: "
                if len(session.opt_participants) == 1
                else " participants: "
            )
            + ", ".join([u[1] if u[1] else u[0] for u in session.opt_participants])
        )

    return {
        "perms": context["perms"],
        "sessions": sessions,
        "show_notes": show_notes,
        "show_leave": show_leave_button,
        "show_ratings": show_ratings,
        "coordinator": coordinator,
    }
