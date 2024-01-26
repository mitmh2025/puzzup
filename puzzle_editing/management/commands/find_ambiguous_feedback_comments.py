import re

from django.core.management.base import BaseCommand
from django.db.models import Q

from puzzle_editing.models import PuzzleComment, TestsolveParticipation

SESSION_NUMBER = re.compile(r"Testsolving session #(?P<session_num>\d+)")


class Command(BaseCommand):
    def handle(self, *args, **options):
        comment: PuzzleComment
        for comment in PuzzleComment.objects.all():
            if "General feedback" not in comment.content:
                continue

            if SESSION_NUMBER.search(comment.content):
                # Known session number, so no need to do more.
                continue

            # Check if the user-puzzle pair is unique
            possible_participations = TestsolveParticipation.objects.filter(
                Q(user=comment.author) & Q(session__puzzle_id=comment.puzzle_id)
            )
            if len(possible_participations) >= 2:
                print(comment.id, comment.author.credits_name, comment.puzzle.id)
