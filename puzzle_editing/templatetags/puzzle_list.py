import random

from django import template
from django.db.models import Exists, Max, OuterRef, Subquery

from puzzle_editing import status
from puzzle_editing.models import PuzzleTag, PuzzleVisited, User

register = template.Library()


def make_puzzle_data(puzzles, user, do_query_filter_in, show_factcheck=False):
    puzzles = (
        puzzles.order_by("priority")
        .annotate(
            is_spoiled=Exists(
                User.objects.filter(spoiled_puzzles=OuterRef("pk"), id=user.id)
            ),
            is_author=Exists(
                User.objects.filter(authored_puzzles=OuterRef("pk"), id=user.id)
            ),
            is_editing=Exists(
                User.objects.filter(editing_puzzles=OuterRef("pk"), id=user.id)
            ),
            is_factchecking=Exists(
                User.objects.filter(factchecking_puzzles=OuterRef("pk"), id=user.id)
            ),
            is_postprodding=Exists(
                User.objects.filter(postprodding_puzzles=OuterRef("pk"), id=user.id)
            ),
            last_comment_date=Max("comments__date"),
            last_visited_date=Subquery(
                PuzzleVisited.objects.filter(puzzle=OuterRef("pk"), user=user).values(
                    "date"
                )
            ),
        )
        .prefetch_related("answers")
        # This prefetch is super slow.
        # .prefetch_related("authors", "editors",
        #     Prefetch(
        #         "tags",
        #         queryset=PuzzleTag.objects.filter(important=True).only("name"),
        #         to_attr="prefetched_important_tags",
        #     ),
        # )
        .defer("description", "notes", "editor_notes")
    )

    puzzles = list(puzzles)

    for puzzle in puzzles:
        puzzle.prefetched_important_tag_names = []

    puzzle_ids = [puzzle.id for puzzle in puzzles]
    id_to_index = {puzzle.id: i for i, puzzle in enumerate(puzzles)}

    # Handrolling prefetches because
    # (1) we can aggressively skip model construction
    # (2) (actually important, from my tests) if we know we're listing all
    #     puzzles, skipping the puzzles__in constraint massively improves
    #     performance. (I want to keep it in other cases so that we don't
    #     regress.)
    tagships = PuzzleTag.objects.filter(important=True)
    if do_query_filter_in:
        tagships = tagships.filter(puzzles__in=puzzle_ids)
    for tag_name, puzzle_id in tagships.values_list("name", "puzzles"):
        if puzzle_id in id_to_index:
            puzzles[id_to_index[puzzle_id]].prefetched_important_tag_names.append(
                tag_name
            )

    for puzzle in puzzles:
        # These are dictionaries username -> (username, display_name)
        puzzle.opt_authors = {}
        puzzle.opt_editors = {}
        puzzle.opt_factcheckers = {}

    authors = (
        User.objects.all()
        .prefetch_related("authored_puzzles")
        .prefetch_related("led_puzzles")
    )
    if do_query_filter_in:
        authors = authors.filter(authored_puzzles__in=puzzle_ids)
    for author in authors:
        username = author.username
        display_name = str(author)
        for puzzle in author.authored_puzzles.all():
            if puzzle.pk in id_to_index:
                puzzles[id_to_index[puzzle.pk]].opt_authors[username] = (
                    username,
                    display_name,
                )
        # Augment name with (L) if lead author
        for puzzle in author.led_puzzles.all():
            if puzzle.pk in id_to_index:
                puzzles[id_to_index[puzzle.pk]].opt_authors[username] = (
                    username + " (L)",
                    display_name + " (L)",
                )

    editorships = User.objects
    if do_query_filter_in:
        editorships = editorships.filter(editing_puzzles__in=puzzle_ids)
    for username, display_name, puzzle_id in editorships.values_list(
        "username", "display_name", "editing_puzzles"
    ):
        if puzzle_id in id_to_index:
            puzzles[id_to_index[puzzle_id]].opt_editors[username] = (
                username,
                display_name,
            )

    if show_factcheck:
        factcheckerships = User.objects
        for username, display_name, puzzle_id in factcheckerships.values_list(
            "username", "display_name", "factchecking_puzzles"
        ):
            if puzzle_id in id_to_index:
                puzzles[id_to_index[puzzle_id]].opt_factcheckers[username] = (
                    username,
                    display_name,
                )

    def sort_key(user):
        """Sort by lead, then display name, then username"""
        username, display_name = user
        if display_name.endswith("(L)"):
            return ("", "")  # Earliest string

        return (display_name.lower(), username.lower())

    for puzzle in puzzles:
        authors = sorted(puzzle.opt_authors.values(), key=sort_key)
        editors = sorted(puzzle.opt_editors.values(), key=sort_key)
        puzzle.authors_html = User.html_user_list_of_flat(authors, linkify=False)
        puzzle.editors_html = User.html_user_list_of_flat(editors, linkify=False)
        if show_factcheck:
            factcheckers = sorted(puzzle.opt_factcheckers.values(), key=sort_key)
            puzzle.factcheck_html = User.html_user_list_of_flat(
                factcheckers, linkify=False
            )

    return puzzles


# TODO: There's gotta be a better way of generating a unique ID for each time
# this template gets rendered...


@register.inclusion_tag("tags/puzzle_list.html", takes_context=True)
def puzzle_list(
    context,
    puzzles,
    user,
    with_new_link=False,
    show_last_status_change=True,
    show_summary=True,
    show_editors=True,
    show_round=False,
    show_flavor=False,
    show_factcheck=False,
):
    req = context["request"]
    perms = context["perms"]
    limit = None
    if req.method == "GET" and "limit" in req.GET:
        try:
            limit = int(req.GET["limit"])
        except ValueError:
            limit = 50

    return {
        "perms": perms,
        "limit": limit,
        "puzzles": make_puzzle_data(
            puzzles,
            user,
            do_query_filter_in=req.path != "/all",
            show_factcheck=show_factcheck,
        ),
        "new_puzzle_link": with_new_link,
        "dead_status": status.DEAD,
        "deferred_status": status.DEFERRED,
        "past_needs_solution_statuses": [
            st["value"]
            for st in status.ALL_STATUSES
            if status.get_status_rank(st["value"])
            > status.get_status_rank(status.NEEDS_SOLUTION)
        ],
        "random_id": "%016x" % random.randrange(16**16),
        "show_last_status_change": show_last_status_change,
        "show_summary": show_summary,
        "show_editors": show_editors,
        "show_round": show_round,
        "show_flavor": show_flavor,
        "show_factcheck": show_factcheck,
    }
