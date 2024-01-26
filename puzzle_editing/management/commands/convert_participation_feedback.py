import re

from django.core.management.base import BaseCommand
from django.db.models import Q

from puzzle_editing.models import (
    PuzzleComment,
    TestsolveFeedback,
    TestsolveParticipation,
)

FEEDBACK_PATTERN = re.compile(
    r"""
(Finished\ testsolve\.\s+)?
\*\*General\ feedback\*\*:\s+
(?P<general_feedback>.*?)\s*
(
(
\*\*Accessibility\ feedback\*\*:\s+
(?P<accessibility_feedback>.*)\s*
\*\*Solve\ path\*\*:\s+
(?P<solve_path>.*)\s*
)
|
(
\*\*Misc\ feedback\*\*:\s+
(?P<misc_feedback>.*)\s*
)
)?
(?P<stats>Fun:.*)
(Testsolving\ session\ \#(?P<session_id>\d+))?
""",
    re.VERBOSE | re.MULTILINE | re.DOTALL,
)


class Command(BaseCommand):
    """Convert TestsolveParticipation comments into TestsolveFeedback"""

    def add_arguments(self, parser):
        pass

    def handle(self, *args, **options):
        comment: PuzzleComment
        for comment in PuzzleComment.objects.all():
            # This comment is not a feedback, ignore.
            if "General feedback" not in comment.content:
                continue

            # This comment has already been processed
            if hasattr(comment, "testsolve_feedback"):
                continue

            content = comment.content.replace("\r", "")
            match = FEEDBACK_PATTERN.match(content)
            if not match:
                print(comment.id)
                continue

            possible_participations = TestsolveParticipation.objects.filter(
                Q(user=comment.author_id) & Q(session__puzzle=comment.puzzle)
            )
            participation = None

            # We know which session this feedback belongs to.
            if match.group("session_id"):
                participation = TestsolveParticipation.objects.get(
                    session_id=int(match.group("session_id")), user=comment.author
                )
            # This user-puzzle pair has only one session.
            elif possible_participations.count() == 1:
                participation = TestsolveParticipation.objects.get(
                    Q(user=comment.author) & Q(session__puzzle=comment.puzzle)
                )
            # Ambiguous. Feed this into the last (highest id) user-puzzle participation.
            elif possible_participations.count() >= 2:
                participation = possible_participations.latest("ended")

            if not participation:
                print(
                    "No matching TestsolveParticipation:",
                    comment.id,
                    comment.author_id,
                    comment.puzzle_id,
                )
                continue

            feedback = TestsolveFeedback(
                participation=participation,
                comment=comment,
                general_feedback=match.group("general_feedback").strip(),
            )

            if match.group("misc_feedback"):
                feedback.general_feedback += "\n" + match.group("misc_feedback").strip()
                feedback.aspects_accessibility = ""
                feedback.solve_path = ""

            elif match.group("solve_path"):
                feedback.aspects_accessibility = match.group(
                    "accessibility_feedback"
                ).strip()
                feedback.solve_path = match.group("solve_path").strip()

            feedback.save()
            comment.testsolve_feedback = feedback
            comment.save()
            feedback.date = comment.date
            feedback.save()
