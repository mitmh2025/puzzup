# ruff: noqa: F401

from __future__ import annotations

from urllib.parse import urljoin

from celery import Celery

# set the default Django settings module for the 'celery' program.
celery_app = Celery("puzzle_editing")
celery_app.config_from_object("django.conf:settings", namespace="CELERY")


@celery_app.task
def export_all_task(user_id):
    from django.conf import settings

    from .messaging import send_mail_wrapper
    from .models import PuzzlePostprod, User
    from .utils import postprod

    user = User.objects.get(id=user_id)
    branch = postprod.export_all()

    if branch:
        subject = "Complete: postprod fixture export for all puzzles"
        context = {
            "success": True,
            "puzzle_title": "all puzzles",
            "branch": branch,
            "branch_url": urljoin(settings.HUNT_REPO_URL_HTTPS + "/", "tree/" + branch),
        }

    else:
        subject = "Failed: postprod export for all puzzles"
        context = {"success": False}

    send_mail_wrapper(
        subject,
        "emails/postprod_complete",
        context,
        [user.email],
    )


@celery_app.task
def export_puzzle_task(
    user_id,
    pp_id,
    puzzle_directory,
    puzzle_html="",
    solution_html="",
    max_image_width=400,
):
    from django.conf import settings

    from .messaging import send_mail_wrapper
    from .models import PuzzlePostprod, User
    from .utils import postprod

    user = User.objects.get(id=user_id)
    pp = PuzzlePostprod.objects.get(id=pp_id)

    branch = postprod.export_puzzle(
        pp,
        puzzle_directory,
        puzzle_html=puzzle_html,
        solution_html=solution_html,
        max_image_width=max_image_width,
    )

    if branch:
        subject = f"Complete: postprod export for {pp.puzzle.spoilery_title}"
        context = {
            "success": True,
            "puzzle_title": pp.puzzle.spoilery_title,
            "branch": branch,
            "branch_url": urljoin(settings.HUNT_REPO_URL_HTTPS + "/", "tree/" + branch),
        }

    else:
        subject = f"Failed: postprod export for {pp.puzzle.spoilery_title}"
        context = {"success": False}

    send_mail_wrapper(
        subject,
        "emails/postprod_complete",
        context,
        [user.email],
    )
