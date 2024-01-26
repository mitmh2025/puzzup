import json
from pathlib import Path

from django.core.management.base import BaseCommand

from puzzle_editing.models import Puzzle, PuzzleComment, User


class Command(BaseCommand):
    help = """Import JSON feedback."""

    def add_arguments(self, parser):
        parser.add_argument("filename", type=str)
        parser.add_argument("user", type=str)

    def handle(self, *args, **options):
        print(options)
        user = User.objects.get(username=options["user"])
        with Path(options["filename"]).open() as f:
            data = json.load(f)

        for line in data:
            puzzleid, comment, fun, diff = line
            content = (
                f"Feedback from BTS:\n\n{comment}\n\n"
                f"Fun: {fun} / Difficulty: {diff}"
            )
            comment = PuzzleComment.objects.create(
                puzzle=Puzzle.objects.get(id=puzzleid),
                author=user,
                is_system=True,
                content=content,
            )
            comment.save()
