from __future__ import annotations

import contextlib
import datetime
import random
import re
import statistics
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml
from django import urls
from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Manager, Prefetch
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from github import Auth, Github

import puzzle_editing.discord_integration as discord
import puzzle_editing.google_integration as google
from puzzle_editing import status
from puzzle_editing.messaging import send_mail_wrapper
from puzzle_editing.view_helpers import AuthenticatedHttpRequest

DisplayFn = Callable[[], str]


class PuzzupUserManager(UserManager["User"]):
    def get_queryset(self, *args, **kwargs):
        # Prefetches the permission groups
        return super().get_queryset(*args, **kwargs).prefetch_related("groups")


class CustomUsernameValidator(UnicodeUsernameValidator):
    """Allows # (from discord)."""

    regex = r"^[\w.@#+-\\ ]+$"
    message = _(
        "Enter a valid username. This value may contain only letters, "
        "numbers, spaces, and \\/@/#/./+/-/_ characters."
    )


class User(AbstractUser):
    id: int
    username = models.CharField(
        _("username"),
        max_length=150,
        unique=True,
        help_text=_(
            "Required. 150 characters or fewer. Letters, digits and @/./#/+/-/_ only."
        ),
        validators=[CustomUsernameValidator()],
        error_messages={
            "unique": _("A user with that username already exists."),
        },
    )

    # All of these are populated by the discord sync.
    discord_username = models.CharField(max_length=500, blank=True)
    discord_nickname = models.CharField(max_length=500, blank=True)
    discord_user_id = models.CharField(max_length=500, blank=True)
    avatar_url = models.CharField(max_length=500, blank=True)

    credits_name = models.CharField(
        max_length=80,
        help_text=(
            "How you want your name to appear in puzzle credits, e.g. Ben Bitdiddle"
        ),
    )
    bio = models.TextField(
        blank=True,
        help_text=(
            "Tell us about yourself. What kinds of puzzle genres or "
            "subject matter do you like?"
        ),
    )
    enable_keyboard_shortcuts = models.BooleanField(default=False)

    github_username = models.CharField(max_length=39, blank=True)

    authored_puzzles: Manager[Puzzle]
    editing_puzzles: Manager[Puzzle]
    discussing_puzzles: Manager[Puzzle]
    spoiled_puzzles: Manager[Puzzle]
    spoiled_rounds: Manager[Puzzle]
    other_credits: Manager[PuzzleCredit]
    assigned_support_requests: Manager[SupportRequest]

    current: bool
    stats: list[int]  # users_statuses

    objects = PuzzupUserManager()  # type: ignore

    class Meta:
        # make Django always use the objects manager (so that we prefetch)
        base_manager_name = "objects"

    @property
    def is_eic(self):
        return any(g.name == "EIC" for g in self.groups.all())

    @property
    def is_editor(self):
        return any(g.name == "Editor" for g in self.groups.all())

    @property
    def is_artist(self):
        return any(g.name == "Art" for g in self.groups.all())

    @property
    def is_art_lead(self):
        return any(g.name == "Art Lead" for g in self.groups.all())

    @property
    def is_testsolve_coordinator(self):
        return any(g.name == "Testsolve Coordinators" for g in self.groups.all())

    @property
    def is_factcheck_coordinator(self):
        return any(g.name == "Factcheck Coordinators" for g in self.groups.all())

    @property
    def hat(self):
        if self.is_eic:
            return "🎩"
        elif self.is_editor:
            return "👒"
        elif self.is_staff:
            return "🧢"
        return ""

    # Some of this templating is done in an inner loop, so doing it with
    # inclusion tags turns out to be a big performance hit. They're also small
    # enough to be pretty easy to write in Python. Separating out the versions
    # that don't even bother taking a User and just take two strings might be a
    # bit premature, but I think skipping prefetching and model construction is
    # worth it in an inner loop...
    @staticmethod
    def html_user_display_of_flat(username, display_name, linkify):
        ret = (
            format_html('<span title="{}">{}</span>', username, display_name)
            if display_name
            else username
        )

        if linkify:
            return format_html(
                '<a href="{}">{}</a>', urls.reverse("user", args=[username]), ret
            )
        else:
            return ret

    @staticmethod
    def html_user_display_of(user, linkify):
        return User.html_user_display_of_flat(user.username, user.credits_name, linkify)

    @staticmethod
    def html_user_list_of_flat(ud_pairs, linkify):
        # iterate over ud_pairs exactly once
        s = format_html_join(
            ", ",
            "{}",
            ((User.html_user_display_of_flat(un, dn, linkify),) for un, dn in ud_pairs),
        )
        return s or mark_safe('<span class="empty">--</span>')

    @staticmethod
    def html_user_list_of(users, linkify):
        return User.html_user_list_of_flat(
            ((user.username, user.credits_name) for user in users),
            linkify,
        )

    @staticmethod
    def html_avatar_list_of(users, linkify):
        def fmt_user(u):
            img = "<img src='{}' width='40' height='40'/>"
            if linkify:
                url = urls.reverse("user", args=[u.username])
                return format_html('<a href="{}">' + img + "</a>", url, u.avatar_url)
            return format_html(img, u.avatar_url)

        s = format_html_join(" ", "{}", ((fmt_user(u),) for u in users))
        return s or mark_safe('<span class="empty">--</span>')

    def get_avatar_url_via_discord(self, discord_avatar_hash, size: int = 0) -> str:
        """Generates and returns the discord avatar url if possible
        Accepts an optional argument that defines the size of the avatar returned, between 16 and 4096 (in powers of 2),
        though this can be set when hotlinked."""

        cdn_base_url = "https://cdn.discordapp.com"

        if not self.discord_user_id:
            # we'll only "trust" information given to us by the discord API; users who haven't linked that way won't have any avatar
            return "a"

        if self.discord_username and not discord_avatar_hash:
            # This is a user with no avatar hash; accordingly, we will give them the default avatar
            try:
                discriminator = self.discord_username.split("#")[1]
            except IndexError:
                return "b"

            return f"{cdn_base_url}/embed/avatars/{discriminator}.png"

        if discord_avatar_hash and self.discord_user_id:
            if size > 0:
                size = size - (size % 2)
                size = 16 if size < 16 else size
                size = 4096 if size > 4096 else size

            return (
                f"{cdn_base_url}/avatars/{self.discord_user_id}/{discord_avatar_hash}.png"
                + (f"?size={size}" if size > 0 else "")
            )

        return "d"

    @property
    def full_display_name(self):
        return "{}{}".format(
            (self.credits_name or self.username),
            (f" (@{self.discord_username})" if self.discord_username else ""),
        ).strip()

    @property
    def safe_credits_name(self):
        return self.credits_name or self.username

    @property
    def discord_tag(self):
        return f"<@{self.discord_user_id}>"


class Round(models.Model):
    """A round of answers feeding into the same metapuzzle or set of metapuzzles."""

    id: int
    name = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    spoiled = models.ManyToManyField(
        User,
        blank=True,
        related_name="spoiled_rounds",
        help_text="Users spoiled on the round's answers.",
    )
    editors = models.ManyToManyField(User, related_name="editors", blank=True)
    puzzle_template = models.CharField(
        max_length=500,
        help_text="Path to puzzle template in the hunt repo for autopostprod",
        blank=True,
    )
    solution_template = models.CharField(
        max_length=500,
        help_text="Path to sol template in the hunt repo for autopostprod",
        blank=True,
    )

    answers: Manager[PuzzleAnswer]

    def __str__(self):  # pylint: disable=invalid-str-returned
        return self.name


class PuzzleAnswer(models.Model):
    """An answer. Can be assigned to zero, one, or more puzzles."""

    id: int
    answer = models.CharField(max_length=500, blank=True)
    round = models.ForeignKey(Round, on_delete=models.PROTECT, related_name="answers")
    notes = models.TextField(blank=True)
    case_sensitive = models.BooleanField(
        default=False,
        help_text=(
            "Whether or not this answer needs to be submitted with the correct casing."
        ),
    )
    whitespace_sensitive = models.BooleanField(
        default=False,
        help_text="Whether or not this answer shouldn't ignore whitespaces.",
    )
    special_sensitive = models.BooleanField(
        default=False,
        help_text="Whether or not this answer shouldn't ignore special characters.",
    )

    puzzles: Manager[Puzzle]

    def __str__(self):  # pylint: disable=invalid-str-returned
        return self.answer

    def normalize(self, answer):
        normalized = answer
        if not self.special_sensitive:
            normalized = "".join(c for c in normalized if c.isalnum() or c.isspace())
        if not self.whitespace_sensitive:
            normalized = "".join(c for c in normalized if not c.isspace())
        if not self.case_sensitive:
            normalized = normalized.upper()

        return normalized

    def is_correct(self, guess):
        normalized_guess = self.normalize(guess)
        normalized_answer = self.normalize(self.answer)
        return normalized_answer == normalized_guess


class PseudoAnswer(models.Model):
    """
    Possible answers a solver might input that don't mark the puzzle as correct,
    but need handling.
    For example, they might provide a nudge for teams that are on the right
    track, or special instructions for how to obtain the correct answer.
    """

    id: int
    puzzle = models.ForeignKey(
        "Puzzle", on_delete=models.CASCADE, related_name="pseudo_answers"
    )
    puzzle_id: int
    answer = models.TextField(max_length=100)
    response = models.TextField()

    case_sensitive = models.BooleanField(
        default=False,
        help_text=(
            "Whether or not this pseudoanswer needs to be submitted with the correct"
            " casing."
        ),
    )
    whitespace_sensitive = models.BooleanField(
        default=False,
        help_text="Whether or not this pseudoanswer shouldn't ignore whitespaces.",
    )
    special_sensitive = models.BooleanField(
        default=False,
        help_text=(
            "Whether or not this pseudoanswer shouldn't ignore special characters."
        ),
    )

    class Meta:
        unique_together = ("puzzle", "answer")
        ordering = ["puzzle", "answer"]

    def __str__(self):
        return f'"{self.puzzle.name}" ({self.answer})'

    def get_yaml_data(self):
        return {
            "model": "spoilr_core.pseudoanswer",
            "pk": self.id,
            "fields": {
                "puzzle": self.puzzle_id,
                "answer": self.answer,
                "response": self.response,
            },
        }

    def normalize(self, text: str):
        normalized = text
        if not self.special_sensitive:
            normalized = "".join(c for c in normalized if c.isalnum() or c.isspace())
        if not self.whitespace_sensitive:
            normalized = "".join(c for c in normalized if not c.isspace())
        if not self.case_sensitive:
            normalized = normalized.upper()
        return normalized

    def is_correct(self, guess):
        normalized_guess = self.normalize(guess)
        normalized_answer = self.normalize(self.answer)
        return normalized_answer == normalized_guess


class PuzzleTag(models.Model):
    """A tag to classify puzzles."""

    name = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    important = models.BooleanField(
        default=False,
        help_text="Important tags are displayed prominently with the puzzle title.",
    )

    puzzles: Manager[Puzzle]

    def __str__(self):
        return f"Tag: {self.name}"


def generate_codename():
    with Path(settings.BASE_DIR, "puzzle_editing/data/nouns-eng.txt").open() as f:
        nouns = [line.strip() for line in f.readlines()]
    random.shuffle(nouns)

    with Path(settings.BASE_DIR, "puzzle_editing/data/adj-eng.txt").open() as g:
        adjs = [line.strip() for line in g.readlines()]
    random.shuffle(adjs)

    try:
        name = adjs.pop() + " " + nouns.pop()
        while Puzzle.objects.filter(codename=name).exists():
            name = adjs.pop() + " " + nouns.pop()
    except IndexError:
        return "Make up your own name!"

    return name


class Puzzle(models.Model):
    """A puzzle, that which Puzzup keeps track of the writing process of."""

    id: int
    name = models.CharField(max_length=500)
    codename = models.CharField(
        max_length=500,
        default=generate_codename,
        help_text="A non-spoilery name. Feel free to use the autogenerated one.",
    )
    discord_channel_id = models.CharField(
        max_length=19,
        blank=True,
    )
    discord_emoji = models.CharField(
        default=":question:",
        max_length=100,
        help_text=(
            "The emoji that'll be used in Discord notifications. Please leave in string"
            " form, e.g. `:question:`. You can use multiple emojis if necessary (e.g."
            " `:question::question`), but try to keep it to 1 (and no more than 2)."
        ),
    )
    authors = models.ManyToManyField(User, related_name="authored_puzzles", blank=True)
    authors_addl = models.CharField(
        max_length=200,
        help_text=(
            "The second line of author credits. Only use in cases where a standard"
            " author credit isn't accurate."
        ),
        blank=True,
    )

    editors = models.ManyToManyField(User, related_name="editing_puzzles", blank=True)
    discussion_editors = models.ManyToManyField(
        User, related_name="discussing_puzzles", blank=True
    )
    needed_editors = models.IntegerField(default=2)
    spoiled = models.ManyToManyField(
        User,
        related_name="spoiled_puzzles",
        blank=True,
        help_text="Users spoiled on the puzzle.",
    )
    quickcheckers = models.ManyToManyField(
        User, related_name="quickchecking_puzzles", blank=True
    )
    factcheckers = models.ManyToManyField(
        User, related_name="factchecking_puzzles", blank=True
    )
    postprodders = models.ManyToManyField(
        User, related_name="postprodding_puzzles", blank=True
    )

    # .get_status_display() is a built-in syntax that will get the human-readable text
    status = models.CharField(
        max_length=status.MAX_LENGTH,
        choices=status.DESCRIPTIONS.items(),
        default=status.INITIAL_IDEA,
    )
    get_status_display: DisplayFn
    status_mtime = models.DateTimeField(editable=False)
    last_updated = models.DateTimeField(auto_now=True)

    summary = models.TextField(
        blank=True,
        help_text=(
            "A **non-spoilery description.** What will solvers see when they open the"
            " puzzle?"
        ),
    )
    description = models.TextField(
        help_text="A **spoilery description** of how the puzzle works."
    )
    editor_notes = models.TextField(
        blank=True,
        verbose_name="Mechanics",
        help_text="A **succinct, spoilery list** of mechanics and themes used.",
    )
    notes = models.TextField(
        blank=True,
        help_text=(
            "Notes and requests to the editors, like for a particular answer or"
            " inclusion in a particular round."
        ),
    )
    answers = models.ManyToManyField(PuzzleAnswer, blank=True, related_name="puzzles")
    tags = models.ManyToManyField(PuzzleTag, blank=True, related_name="puzzles")
    priority = models.IntegerField(
        choices=(
            (1, "Very High"),
            (2, "High"),
            (3, "Medium"),
            (4, "Low"),
            (5, "Very Low"),
        ),
        default=3,
    )
    get_priority_display: DisplayFn
    content = models.TextField(
        blank=True, help_text="The puzzle itself. An external link is fine."
    )
    solution = models.TextField(blank=True)
    is_meta = models.BooleanField(
        verbose_name="Is this a meta?", help_text="Check the box if yes.", default=False
    )
    deep = models.IntegerField(default=0)
    deep_key = models.CharField(max_length=500, verbose_name="DEEP key", blank=True)

    testsolving_admin_thread_id = models.CharField(max_length=19, blank=True)

    postprod_issue_id = models.IntegerField(default=0)

    pseudo_answers: Manager[PseudoAnswer]
    hints: Manager[Hint]
    postprod: PuzzlePostprod
    other_credits: Manager[PuzzleCredit]
    support_requests: Manager[SupportRequest]

    def __str__(self):
        return self.spoiler_free_title()

    # Dynamic attrs used elsewhere
    user_data: list[str]
    unspoiled_count: int
    new_testsolve_url: str
    prefetched_important_tag_names: list[str]
    slug_in_file: str
    metadata_credits: str
    credits_in_file: str

    def spoiler_free_name(self):
        if self.codename:
            return f"({self.id}: {self.codename})"
        return f"({self.id}: {self.name})"

    def spoiler_free_title(self):
        return self.spoiler_free_name()

    @property
    def spoilery_title(self):
        name = self.name
        if self.codename:
            name += f" ({self.id}: {self.codename})"
        else:
            name += f" ({self.id})"
        return name

    def important_tag_names(self):
        if hasattr(self, "prefetched_important_tag_names"):
            return self.prefetched_important_tag_names
        return [t.name for t in self.tags.all() if t.important]

    # This is done in an inner loop, so doing it with inclusion tags turns
    # out to be a big performance hit. They're also small enough to be pretty
    # easy to write in Python.
    def html_display(self):
        return format_html(
            "{} {}",
            format_html_join(
                " ",
                "<sup>[{}]</sup>",
                ((name,) for name in self.important_tag_names()),
            ),
            self.spoiler_free_name(),
        )

    def html_link(self):
        return format_html(
            """<a href="{}" class="puzzle-link">{}</a>""",
            urls.reverse("puzzle", args=[self.id]),
            self.html_display(),
        )

    def html_link_no_tags(self):
        return format_html(
            """<a href="{}" class="puzzle-link">{}</a>""",
            urls.reverse("puzzle", args=[self.id]),
            self.spoiler_free_name(),
        )

    def get_status_rank(self):
        return status.get_status_rank(self.status)

    def get_status_emoji(self):
        return status.get_emoji(self.status)

    def get_blocker(self) -> str:
        # just text describing what the category of blocker is, not a list of
        # Users or anything like that
        return " // ".join(status.get_blocker(self.status))

    def is_unblockable_by(self, user: User) -> bool:
        return bool(self.get_transitions(user))

    def get_transitions(self, user):
        return [
            {
                "status": s,
                "status_display": status.get_display(s),
                "description": description,
            }
            for s, description in status.get_transitions(self.status, user, self)
        ]

    def get_emails(self, exclude_emails=()):
        emails = set(self.authors.values_list("email", flat=True))
        emails |= set(self.editors.values_list("email", flat=True))
        emails |= set(self.factcheckers.values_list("email", flat=True))
        emails |= set(self.postprodders.values_list("email", flat=True))

        emails -= set(exclude_emails)
        emails -= {""}

        return list(emails)

    def has_postprod(self):
        try:
            return self.postprod is not None
        except PuzzlePostprod.DoesNotExist:
            return False

    def has_hints(self):
        return self.hints.count() > 0

    def has_answer(self):
        return self.answers.count() > 0

    @property
    def postprod_url(self):
        if self.has_postprod():
            return self.postprod.get_url(is_solution=False)
        return ""

    @property
    def postprod_solution_url(self):
        if self.has_postprod():
            return self.postprod.get_url(is_solution=True)
        return ""

    @property
    def hints_array(self):
        return [[h.order, h.keywords.split(","), h.content] for h in self.hints.all()]

    @property
    def author_byline(self):
        credits = [u.credits_name for u in self.authors.all()]
        credits.sort(key=lambda u: u.upper())
        if len(credits) == 2:
            return " and ".join(credits)
        else:
            return re.sub(r"([^,]+?), ([^,]+?)$", r"\1, and \2", ", ".join(credits))

    @property
    def answer(self):
        return ", ".join(self.answers.values_list("answer", flat=True)) or None

    @property
    def round(self):
        return next(iter(a.round for a in self.answers.all()), None)

    @property
    def metadata(self):
        credits = [u.credits_name for u in self.authors.all()]
        credits.sort(key=lambda u: u.upper())
        editors = [u.credits_name for u in self.editors.all()]
        editors.sort(key=lambda u: u.upper())
        postprodders = [u.credits_name for u in self.postprodders.all()]
        postprodders.sort(key=lambda u: u.upper())
        answers = list(self.answers.all())
        return {
            "puzzle_title": self.name,
            "main_credits": self.author_byline,
            "answer": (
                ", ".join(sorted([a.answer for a in answers])) if answers else "???"
            ),
            "round": next(iter(a.round_id for a in self.answers.all()), 1),
            "puzzle_idea_id": self.id,
            "other_credits": {
                c.credit_type: [
                    re.sub(
                        r"([^,]+?), ([^,]+?)$",
                        r"\1 and \2",
                        ", ".join([u.credits_name for u in c.users.all()]),
                    ),
                    c.text,
                ]
                for c in self.other_credits.all()
            },
            "additional_authors": self.authors_addl,
            "editors": re.sub(r"([^,]+?), ([^,]+?)$", r"\1 and \2", ", ".join(editors)),
            # "postprodders": re.sub(r"([^,]+?), ([^,]+?)$", r"\1 and \2", ", ".join(postprodders)),
            "puzzle_slug": (
                self.postprod.slug
                if self.has_postprod()
                else re.sub(
                    r'[<>#%\'"|{}\[\])(\\\^?=`;@&,]',
                    "",
                    re.sub(r"[ \/]+", "-", self.name),
                ).lower()
            ),
            "case_sensitive": any(a.case_sensitive for a in answers),
            "whitespace_sensitive": any(a.whitespace_sensitive for a in answers),
            "special_sensitive": any(a.special_sensitive for a in answers),
        }

    @property
    def slug(self):
        return re.sub(r"-+", "-", re.sub(r"[^-\w]+", "", self.codename)).strip("-")

    @property
    def author_list(self):
        return ", ".join(
            [
                a.credits_name if a.credits_name else a.username
                for a in self.authors.all()
            ]
        )

    @property
    def editor_list(self):
        return ", ".join(
            [
                a.credits_name if a.credits_name else a.username
                for a in self.editors.all()
            ]
        )

    def get_yaml_fixture(self):
        metadata = self.metadata
        puzzle_data = {
            "model": "puzzles.puzzle",
            "pk": self.id,
            "fields": {
                "emoji": self.discord_emoji,
                "deep": self.deep,
            },
        }
        # We only try to set this via fixture if it's defined.
        if self.deep_key:
            puzzle_data["fields"]["deep_key"] = self.deep_key

        spoilr_puzzle_data = {
            "model": "spoilr_core.puzzle",
            "pk": self.id,
            "fields": {
                "external_id": self.id,
                "round": metadata["round"],
                "answer": metadata["answer"],
                "name": self.name,
                "main_credits": metadata["main_credits"],
                "other_credits": metadata["additional_authors"],
                "order": self.id,
                "is_meta": self.is_meta,
                "slug": metadata["puzzle_slug"],
                "case_sensitive": metadata["case_sensitive"],
                "whitespace_sensitive": metadata["whitespace_sensitive"],
                "special_sensitive": metadata["special_sensitive"],
                # TODO: don't hardcode metas
                "metas": [],
            },
        }

        hint_data = [hint.get_yaml_data() for hint in self.hints.all()]
        pseudoanswers_data = [
            pseudoanswer.get_yaml_data() for pseudoanswer in self.pseudo_answers.all()
        ]

        return yaml.dump(
            [puzzle_data, spoilr_puzzle_data, *hint_data, *pseudoanswers_data],
            sort_keys=False,
        )

    @property
    def puzzle_type(self):
        return "Meta" if self.is_meta else "Feeder"

    @property
    def round_membership(self):
        return {answer.round for answer in self.answers.all()}

    def add_comment(
        self,
        author: User,
        is_system: bool,
        content: str,
        testsolve_session: TestsolveSession | None = None,
        request=None,
        send_email: bool = True,
        status_change: str = "",
    ) -> PuzzleComment:
        comment = PuzzleComment(
            puzzle=self,
            author=author,
            testsolve_session=testsolve_session,
            is_system=is_system,
            content=content,
            status_change=status_change,
        )
        comment.save()

        if send_email:
            if testsolve_session:
                subject = "New comment on {} (testsolve #{})".format(
                    self.spoiler_free_title(), testsolve_session.id
                )
                emails = testsolve_session.get_emails(exclude_emails=(author.email,))
            else:
                subject = f"New comment on {self.spoiler_free_title()}"
                emails = self.get_emails(exclude_emails=(author.email,))
            send_mail_wrapper(
                subject,
                "new_comment_email",
                {
                    "request": request,
                    "puzzle": self,
                    "author": author,
                    "content": content,
                    "is_system": is_system,
                    "status_change": (
                        status.get_display(status_change) if status_change else None
                    ),
                },
                emails,
            )

        if content and not is_system:
            name = author.credits_name
            if author.discord_user_id:
                name = author.discord_tag

            payload = {
                "content": f"{name} (posted a comment): {content}",
                "parse": [],
            }
            self.post_message_to_channel(payload)

        return comment

    def ensure_testsolving_admin_thread_exists(self):
        c = discord.get_client()
        if not self.testsolving_admin_thread_id:
            admin_thread = discord.build_testsolve_admin_thread(self, c.guild_id)
            admin_thread = c.save_thread(admin_thread)
            self.testsolving_admin_thread_id = admin_thread.id
            self.save()

    def post_message_to_testsolving_admin_thread(self, payload: str | dict[str, Any]):
        self.ensure_testsolving_admin_thread_exists()

        c = discord.get_client()
        c.post_message(self.testsolving_admin_thread_id, payload)

    def post_message_to_channel(self, payload: str | dict[str, Any]):
        c, ch = discord.get_client_and_channel(self)
        if c and ch:
            c.post_message(ch.id, payload)

    def set_status(
        self,
        request: AuthenticatedHttpRequest,
        new_status: str,
        make_comment=True,
    ):
        old_status = self.status
        status_changed = new_status != self.status
        status_display = status.get_display(new_status)

        if not status_changed:
            return

        c = discord.get_client()
        ch = discord.get_channel(c, self)
        rounds = ", ".join(r.name for r in self.round_membership)
        self.status = new_status
        self.save()
        if ch:
            c.save_channel_to_cat(ch, status_display)
            self.post_message_to_channel(f"This puzzle is now **{status_display}**.")

        new_comment_id = -1
        if request and make_comment:
            new_comment = self.add_comment(
                request=request,
                author=request.user,
                is_system=True,
                send_email=False,
                content="",
                status_change=new_status,
            )
            new_comment_id = new_comment.id

        if (
            new_status
            in {
                status.NEEDS_TESTSOLVE_FACTCHECK,
                status.NEEDS_FACTCHECK,
                status.NEEDS_COPY_EDITS,
            }
            and c
        ):
            msg = (
                f"{self.puzzle_type} {self.id} is now **{status_display}**. This puzzle"
                f" is part of {rounds}."
            )
            current_checkers = set(
                list(self.quickcheckers.all()) + list(self.factcheckers.all())
            )
            if current_checkers:
                user_tags = []
                for checker in current_checkers:
                    user_tags.append(discord.tag_id(checker.discord_user_id))
                msg += f" Current checkers: {', '.join(user_tags)}"
            c.post_message(
                settings.DISCORD_FACTCHECK_CHANNEL_ID,
                {
                    "content": msg,
                    "parse": [],
                },
            )

        if new_status == status.NEEDS_POSTPROD:
            if not self.postprod_issue_id:
                g = Github(auth=Auth.Token(settings.GITHUB_TOKEN))
                repo = g.get_repo(settings.HUNT_REPO_NAME)
                puzzup_url = request.build_absolute_uri(
                    urls.reverse("puzzle", args=[self.id])
                )
                issue = repo.create_issue(
                    title=f"Postprod {self.puzzle_type} {self.id}",
                    labels=[repo.get_label("T-puzzle")],
                    body=f"Puzzup: <{puzzup_url}>",
                )
                self.postprod_issue_id = issue.number
                self.save()
            issue_url = urljoin(
                settings.HUNT_REPO_URL_HTTPS + "/", f"issues/{self.postprod_issue_id}"
            )
            msg = (
                f"{self.puzzle_type} {self.id} is now **{status_display}**. This puzzle"
                f" is part of {rounds}. See the Github issue at <{issue_url}>."
            )
            c.post_message(
                settings.DISCORD_POSTPROD_CHANNEL_ID,
                {
                    "content": msg,
                    "parse": [],
                },
            )

        # if (
        #     old_status == status.AWAITING_APPROVAL
        #     and new_status
        #     not in (
        #         status.INITIAL_IDEA,
        #         status.DEFERRED,
        #         status.DEAD,
        #     )
        #     and not self.discussion_editors.exists()
        # ):
        #     msg = (
        #         f"{self.spoilery_title} is now **{status_display}**.\n"
        #         f"Summary: {self.summary}\n"
        #     )
        #     c.post_message(settings.DISCORD_DISC_EDITOR_CHANNEL_ID, msg)

        if new_status == status.TESTSOLVING:
            msg = (
                f"<@&{settings.DISCORD_TESTSOLVE_ADMIN_ROLE}> {self.puzzle_type}"
                f" {self.id} has entered testsolving. This puzzle is part of"
                f" {rounds}. Start a testsolve session at {settings.PUZZUP_URL}"
                f"{urls.reverse('testsolve_new', args=[self.id])}."
            )
            c.post_message(settings.DISCORD_TESTSOLVE_ADMIN_CHANNEL_ID, msg)

            # If the most recently posted comment was the first entry into testing...
            comment_ids = set(
                PuzzleComment.objects.filter(
                    puzzle=self, status_change=status.TESTSOLVING
                ).values_list("id", flat=True)
            ) | {new_comment_id}
            if len(comment_ids) == 1:
                msg = (
                    f"{self.puzzle_type} {self.id} has entered testsolving for the"
                    " first time! Sign up for testsolving in <#1080876876114972722> to"
                    " hear more about it. Good luck in testsolving, authors!"
                )
                c.post_message(settings.DISCORD_GENERAL_CHANNEL_ID, msg)

        if new_status in {
            status.AWAITING_APPROVAL,
            # status.AWAITING_APPROVAL_FOR_TESTSOLVING,
            status.AWAITING_APPROVAL_POST_TESTSOLVING,
            # status.AWAITING_HINTS_APPROVAL,
            # status.AWAITING_POSTPROD_APPROVAL,
        }:
            msg = (
                f"{self.puzzle_type} {self.name} ({self.id}) is now"
                f" **{status_display}**."
            )
            if rounds:
                msg += f" This puzzle is part of {rounds}."
            msg += (
                " View the puzzle at"
                f" {settings.PUZZUP_URL}{urls.reverse('puzzle', args=[self.id])}"
            )
            c.post_message(settings.DISCORD_EIC_ALERT_CHANNEL_ID, msg)

        # Make sure to not rebroadcast when hints are revised
        if (
            old_status == status.AWAITING_TESTSOLVE_REVIEW
            or old_status == status.AWAITING_APPROVAL_POST_TESTSOLVING
        ) and new_status == status.NEEDS_HINTS:
            msg = (
                f"🎉 {self.puzzle_type} {self.id} has graduated testsolving! 🎉"
                " Congratulations to the authors; and thank you to the editors,"
                " factcheckers, testsolvers, and testsolve admins!"
            )
            c.post_message(settings.DISCORD_GENERAL_CHANNEL_ID, msg)

        if (
            old_status == status.AWAITING_HINTS_APPROVAL
            and new_status == status.POSTPROD_BLOCKED
        ):
            msg = (
                "Before sending your puzzle to postprodders, please make sure to review"
                " the Prepping for Postprod Guide"
                " (<https://docs.google.com/document/d/18sBSPfZgJNJz6sxFZmqg3yHT-7xRhWCWXvLec71PR6I/edit>)!"
                " In particular, please make sure that your solution is"
                " website-quality, and that you have a complete list of necessary"
                " attributions (image and otherwise). Once done, go to Puzzup and mark"
                " it as ready for postprod."
            )
            self.post_message_to_channel(msg)


class PuzzleCredit(models.Model):
    """A miscellaneous puzzle credit, such as Art"""

    ART = ("ART", "Art")
    TECH = ("TCH", "Tech")
    OTHER = ("OTH", "Other")

    puzzle = models.ForeignKey(
        Puzzle, related_name="other_credits", on_delete=models.CASCADE
    )

    users = models.ManyToManyField(User, related_name="other_credits", blank=True)
    text = models.TextField(blank=True)
    credit_type = models.CharField(
        max_length=3, choices=[ART, TECH, OTHER], default=ART[0]
    )
    get_credit_type_display: DisplayFn

    class Meta:
        unique_together = ("puzzle", "credit_type")

    def __str__(self):
        return f"{self.get_credit_type_display()}: %s" % (
            re.sub(
                r"([^,]+?), ([^,]+?)$",
                r"\1 and \2",
                ", ".join([u.credits_name for u in self.users.all()]),
            )
            or "--"
        )


@receiver(pre_save, sender=Puzzle)
def set_status_mtime(sender, instance, **_):
    try:
        obj = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        pass  # Object is new
    else:
        if obj.status != instance.status:  # Field has changed
            instance.status_mtime = timezone.now()


class SupportRequest(models.Model):
    """A request for support from one of our departments."""

    class Team(models.TextChoices):
        ART = ("ART", "🎨 Art")
        ACC = ("ACC", "🔎 Accessibility")
        TECH = ("TECH", "👩🏽‍💻 Tech")

    GROUP_TO_TEAM = {
        "Art Lead": Team.ART,
        "Accessibility Lead": Team.ACC,
        "Tech Lead": Team.TECH,
    }

    class Status(models.TextChoices):
        NONE = ("NO", "No need")
        REQUESTED = ("REQ", "Requested")
        APPROVED = ("APP", "Approved")
        BLOCK = ("BLOK", "Blocking")
        COMPLETE = ("COMP", "Completed")
        CANCELLED = ("X", "Cancelled")

    id: int
    team = models.CharField(max_length=4, choices=Team.choices)
    get_team_display: DisplayFn
    puzzle = models.ForeignKey(
        Puzzle, on_delete=models.CASCADE, related_name="support_requests"
    )
    status = models.CharField(
        max_length=4, choices=Status.choices, default=Status.REQUESTED
    )
    get_status_display: DisplayFn
    team_notes = models.TextField(blank=True)
    team_notes_mtime = models.DateTimeField(auto_now=False, null=True)
    team_notes_updater = models.ForeignKey(
        User, null=True, on_delete=models.PROTECT, related_name="support_team_requests"
    )
    assignees = models.ManyToManyField(
        User,
        blank=True,
        related_name="assigned_support_requests",
    )
    author_notes = models.TextField(blank=True)
    author_notes_mtime = models.DateTimeField(auto_now=False, null=True)
    author_notes_updater = models.ForeignKey(
        User,
        null=True,
        on_delete=models.PROTECT,
        related_name="support_author_requests",
    )
    outdated = models.BooleanField(default=False)

    class Meta:
        unique_together = ("puzzle", "team")

    def __str__(self):
        return f"Support request #{self.id} for {self.team} on {self.puzzle}"

    def get_emails(self):
        emails = [
            u.email
            for u in User.objects.filter(
                groups__name="".join(
                    c for c in self.get_team_display() if c.isascii()
                ).strip()
            )
        ]
        if self.team_notes_updater:
            emails.append(self.team_notes_updater.email)

        return list(set(emails))


def sizeof_fmt(num, suffix="B"):
    for unit in ["", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return "{:.1f}{}{}".format(num, "Yi", suffix)


class PuzzlePostprod(models.Model):
    id: int
    puzzle = models.OneToOneField(
        Puzzle, on_delete=models.CASCADE, related_name="postprod"
    )
    slug = models.CharField(
        max_length=200,  # keep in sync with spoilr
        null=False,
        blank=False,
        validators=[RegexValidator(regex=r'[^<>#%"\'|{})(\[\]\/\\\^?=`;@&, ]{1,200}')],
        help_text=(
            "The part of the URL on the hunt site referrring to this puzzle. E.g. for"
            " https://puzzle.hunt/puzzle/fifty-fifty, this would be 'fifty-fifty'."
        ),
    )
    mtime = models.DateTimeField(auto_now=True)
    host_url = models.CharField(
        max_length=255,
        blank=True,
        help_text="The base URL where this puzzle is postprodded. Defaults to staging",
    )

    def __str__(self):
        return f"Postprod #{self.id} for {self.puzzle}"

    def branch_name(self):
        return "postprod/" + self.slug

    def get_url(self, is_solution=False):
        host = self.host_url if self.host_url else settings.POSTPROD_URL
        subpath = "solutions" if is_solution else "puzzles"
        return f"{host}/{subpath}/{self.slug}"


class StatusSubscription(models.Model):
    """An indication to email a user when any puzzle enters this status."""

    status = models.CharField(
        max_length=status.MAX_LENGTH,
        choices=status.DESCRIPTIONS.items(),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    def __str__(self):
        return "{} subscription to {}".format(
            self.user, status.get_display(self.status)
        )


class PuzzleVisited(models.Model):
    """A model keeping track of when a user last visited a puzzle page."""

    id: int
    puzzle = models.ForeignKey(Puzzle, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Puzzle visit #{self.id} ({self.user}, {self.puzzle})"


class TestsolveSession(models.Model):
    """An attempt by a group of people to testsolve a puzzle.

    Participants in the session will be able to make comments and see other
    comments in the session. People spoiled on the puzzle can also comment and
    view the participants' comments.
    """

    id: int
    puzzle = models.ForeignKey(
        Puzzle, on_delete=models.CASCADE, related_name="testsolve_sessions"
    )
    puzzle_id: int
    started = models.DateTimeField(auto_now_add=True)
    joinable = models.BooleanField(
        default=True,
        help_text=(
            "Whether this puzzle is advertised to other users as a session they can"
            " join."
        ),
    )
    notes = models.TextField(blank=True)

    admin = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="testsolve_admin_sessions"
    )
    discord_thread_id = models.CharField(max_length=19, blank=True)
    discord_vc_id = models.CharField(max_length=19, blank=True)
    google_sheets_id = models.CharField(max_length=64, blank=True)

    # More-or-less `joinable`, but with different semantics
    is_open = models.BooleanField(default=True)

    comments: Manager[PuzzleComment]
    guesses: Manager[TestsolveGuess]
    participations: Manager[TestsolveParticipation]

    def __str__(self):
        return f"Testsolve session #{self.id} on puzzle #{self.puzzle_id}"

    @property
    def time_since_started(self):
        td = datetime.datetime.now(tz=datetime.timezone.utc) - self.started
        minutes = td.total_seconds() / 60
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        return " ".join(
            [
                time
                for time in [
                    f"{int(days)}d" if days > 0 else None,
                    f"{int(hours):02}h" if hours > 0 else None,
                    f"{int(minutes):02}m" if minutes > 0 else None,
                ]
                if time
            ]
        )

    def participants(self):
        users = []
        for p in self.participations.all():
            p.user.current = p.ended is None
            users.append(p.user)
        return users

    def active_participants(self):
        return [p.user for p in self.participations.all() if p.ended is None]

    def get_done_participants_display(self):
        participations = list(self.participations.all())
        done_participations = [p for p in participations if p.ended is not None]
        return f"{len(done_participations)} / {len(participations)}"

    def has_correct_guess(self):
        return any(g.correct for g in self.guesses.all())

    def get_average_fun(self):
        try:
            return statistics.mean(
                p.fun_rating
                for p in self.participations.all()
                if p.fun_rating is not None
            )
        except statistics.StatisticsError:
            return None

    def get_average_diff(self):
        try:
            return statistics.mean(
                p.difficulty_rating
                for p in self.participations.all()
                if p.difficulty_rating is not None
            )
        except statistics.StatisticsError:
            return None

    def get_average_hours(self):
        try:
            return statistics.mean(
                p.hours_spent
                for p in self.participations.all()
                if p.hours_spent is not None
            )
        except statistics.StatisticsError:
            return None

    def get_emails(self, exclude_emails=()):
        emails = set(self.puzzle.get_emails())
        emails |= {p.email for p in self.participants() if p.email is not None}

        emails -= set(exclude_emails)
        emails -= {""}

        return list(emails)

    def create_testsolve_threads(self: TestsolveSession):
        if not discord.enabled():
            return

        puzzle = self.puzzle
        c = discord.get_client()

        puzzle.ensure_testsolving_admin_thread_exists()
        thread = discord.build_testsolve_thread(self, c.guild_id)
        thread = c.save_thread(thread)
        sheet_id = google.GoogleManager.instance().create_testsolving_sheet(self)

        self.discord_thread_id = thread.id
        self.google_sheets_id = sheet_id
        self.save()

        admin_tag = self.admin.discord_tag
        puzzle_url = f"{settings.PUZZUP_URL}/puzzle/{puzzle.id}"
        testsolve_url = f"{settings.PUZZUP_URL}/testsolve/{self.id}"
        sheets_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        ", ".join(discord.get_tags(self.participants()))

        admin_message = c.post_message(
            puzzle.testsolving_admin_thread_id,
            {
                "content": (
                    f"New testsolve session {self.id} created for"
                    f" {puzzle.puzzle_type} {puzzle.id}.\nPuzzle page:"
                    f" {puzzle_url}\nTestsolve page: {testsolve_url}\nTestsolve admin:"
                    f" {admin_tag}\nTestsolve thread: <#{thread.id}>"
                ),
            },
        )
        c.pin_message(puzzle.testsolving_admin_thread_id, admin_message["id"])

        message = "\n".join(
            [
                f"The **testsolving admin** is {admin_tag}.",
                (
                    f"Here's the **testsolving instance in Puzzup** ({testsolve_url})."
                    " You will use it for: accessing the puzzle, checking the answer,"
                    " and submitting feedback."
                ),
                f"Here's a **solver sheet** so you can collaborate: {sheets_url}",
            ]
        )

        if puzzle.is_meta:
            message = "\n\n".join(
                [
                    "**__Meta Instructions__**",
                    message,
                    "This is a meta solving instance.",
                    (
                        "Do what you can and then stop after an hour (or two).  If"
                        " you're still having fun, and/or are on to something, feel"
                        " free to keep going.  If you're too stuck or miserable, just"
                        " stop, the puzzle might be broken and it's ok."
                    ),
                    (
                        "If you're done, or stuck, please provide puzzle feedback"
                        " individually. Don't spoil yourself on answers you didn't use."
                        "  The author(s) / editor(s) will read this feedback.  If"
                        " you're stuck, they might come back with different flavor or"
                        " something to see if that unsticks you.  If everyone's around"
                        " during your session this turnaround time might be quick, else"
                        " it might be a next day thing."
                    ),
                    (
                        "If you need help, @ your testsolving admin by name, otherwise"
                        " we might not realize you need help, cause this channel will"
                        ' be muted.  "Is this a typo?" is a very valid question.'
                    ),
                    (
                        "If there are colors in this puzzle and you are color blind,"
                        " let your admin know so you can get a partner in case this is"
                        " meaningful."
                    ),
                    (
                        "Feel free to call each other, use this thread, and the shared"
                        " sheet to collaborate.  I recommend logging some milestones in"
                        " progress in this thread too so typing up the feedback form"
                        " later can be easier."
                    ),
                    (
                        "Next up - I'll post feeders puzzle names and solutions like"
                        " this: (Name (||Solution||)) so you can start spoiling"
                        " yourselves gradually. Your team will never be able to solve"
                        " some puzzles in this round. A meta should be"
                        " solvable with at least 1-2 broken puzzles in the round. "
                        " These are marked UNSOLVED.  As for the rest, you don't HAVE"
                        " to use all the answers given, please note which/how many"
                        " answers you used."
                    ),
                    "Go!",
                ]
            )
        else:
            message = "\n\n".join(
                [
                    "**__Feeder Instructions__**",
                    message,
                    (
                        "Do what you can and then stop after an hour (or two).  If"
                        " you're still having fun, and/or are on to something, feel"
                        " free to keep going.  If you're too stuck or miserable, just"
                        " stop, the puzzle might be broken and it's ok."
                    ),
                    (
                        "If you're done, or stuck, please provide puzzle feedback"
                        " individually.  The author(s) / editor(s) will read this"
                        " feedback.  If you're stuck, they might come back with"
                        " different flavor or something to see if that unsticks you. "
                        " If everyone's around during your session this turnaround time"
                        " might be quick, else it might be a next day thing."
                    ),
                    (
                        "If you need help, @ your testsolving admin by name, otherwise"
                        " we might not realize you need help, cause this channel will"
                        ' be muted.  "Is this a typo?" is a very valid question.'
                    ),
                    (
                        "If there are colors in this puzzle and you are color blind,"
                        " let your admin know so you can get a partner in case this is"
                        " meaningful."
                    ),
                    (
                        "Feel free to call each other, use this thread, and the shared"
                        " sheet to collaborate.  I recommend logging some milestones in"
                        " progress in this thread too so typing up the feedback form"
                        " later can be easier."
                    ),
                    "Go!",
                ]
            )

        thread_message = c.post_message(thread.id, message)
        c.pin_message(thread.id, thread_message["id"])

    def create_testsolve_vc(self):
        c = discord.get_client()
        vc = discord.VoiceChannel(
            id="",
            name=f"{self.puzzle.puzzle_type} {self.puzzle.id} - Session {self.id}",
            guild_id=c.guild_id,
            parent_id=settings.DISCORD_TESTSOLVE_VC_CAT_ID,
        )

        vc.add_visibility([settings.DISCORD_CLIENT_ID])
        vc.add_visibility(
            [
                p.user.discord_user_id
                for p in self.participations.all()
                if p.user.discord_user_id
            ]
        )
        vc.add_visibility_roles([settings.DISCORD_TESTSOLVE_ADMIN_ROLE])
        vc.make_private()

        vc = c.save_channel(vc)
        self.discord_vc_id = vc.id
        self.save()

        self.post_message(f"Voice channel created: {discord.channel_tag(vc.id)}")

    def add_solvers(self, new_solvers: list[User]):
        new_testers = []
        for new_tester in new_solvers:
            if not TestsolveParticipation.objects.filter(
                session=self, user=new_tester
            ).exists():
                TestsolveParticipation(session=self, user=new_tester).save()
                new_testers.append(new_tester)

        c = discord.get_client()
        if not new_testers:
            return

        new_tester_tags = discord.get_tags(new_testers, skip_missing=False)
        self.post_message(f"Added solvers: {', '.join(new_tester_tags)}")

        # Avoid adding an admin testsolver to admin thread
        non_tcs = [u for u in new_testers if not u.is_testsolve_coordinator]
        tcs = [u for u in new_testers if u.is_testsolve_coordinator]
        tags = discord.get_tags(non_tcs, skip_missing=False) + [
            u.discord_nickname for u in tcs
        ]
        self.puzzle.post_message_to_testsolving_admin_thread(
            {
                "content": f"Added solvers to Session {self.id}: {', '.join(tags)}",
                "parse": [],
            }
        )

        if self.discord_vc_id:
            vc = c.get_voice_channel(self.discord_vc_id)
            vc.add_visibility(
                [u.discord_user_id for u in new_testers if u.discord_user_id]
            )
            c.save_channel(vc)

    def set_admin(self, new_admin: User):
        if new_admin != self.admin:
            self.admin = new_admin
            admin_tag = self.admin.discord_tag
            self.post_message(f"Set testsolving admin to {admin_tag}")
            self.puzzle.post_message_to_testsolving_admin_thread(
                f"Set testsolving admin for Session {self.id} to {admin_tag}"
            )

            if self.discord_vc_id:
                c = discord.get_client()
                vc = c.get_voice_channel(self.discord_vc_id)
                if self.admin.discord_user_id:
                    vc.add_visibility([self.admin.discord_user_id])
                c.save_channel(vc)

            self.save()

    def close_session(self):
        if not self.is_open:
            return
        if self.discord_vc_id:
            c = discord.get_client()

            # Don't care -- testsolving admins can manage manually, so be robust
            with contextlib.suppress(Exception):
                c.delete_channel(self.discord_vc_id)

            self.discord_vc_id = ""

        self.is_open = False
        self.save()

        message = f"Session {self.id} closed!"
        self.puzzle.post_message_to_testsolving_admin_thread(message)
        self.post_message(message)

    def open_session(self):
        if self.is_open:
            return
        self.is_open = True
        self.save()

        message = f"Session {self.id} reopened!"
        self.puzzle.post_message_to_testsolving_admin_thread(message)
        self.post_message(message)

        self.create_testsolve_vc()

    def add_comment(
        self,
        author: User,
        is_system: bool,
        content: str,
        request=None,
        send_email: bool = True,
    ):
        self.puzzle.add_comment(
            author=author,
            is_system=is_system,
            content=content,
            request=request,
            send_email=send_email,
            testsolve_session=self,
        )

    def post_message(self, payload: str | dict[str, Any]):
        if (c := discord.get_client()) and (th := discord.get_thread(c, self)):
            c.post_message(th.id, payload)


class PuzzleComment(models.Model):
    """A comment on a puzzle.

    All comments on a puzzle are visible to people spoiled on the puzzle.
    Comments may or may not be associated with a testsolve session; if they
    are, they will also be visible to people participating in or viewing the
    session."""

    id: int
    puzzle = models.ForeignKey(
        Puzzle, on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="comments")
    date = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)
    is_system = models.BooleanField()
    testsolve_session = models.ForeignKey(
        TestsolveSession,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="comments",
    )
    content = models.TextField(
        blank=True,
        help_text=(
            "The content of the comment. Should probably only be blank if the"
            " status_change is set."
        ),
    )
    status_change = models.CharField(
        max_length=status.MAX_LENGTH,
        choices=status.DESCRIPTIONS.items(),
        blank=True,
        help_text=(
            "Any status change caused by this comment. Only used for recording history"
            " and computing statistics; not a source of truth (i.e. the puzzle will"
            " still store its current status, and this field's value on any comment"
            " doesn't directly imply anything about that in any technically enforced"
            " way)."
        ),
    )

    def __str__(self):
        return f"Comment #{self.id}"


class CommentReaction(models.Model):
    # Since these are frivolous and display-only, I'm not going to bother
    # restricting them on the database model layer.
    EMOJI_OPTIONS = ["👍", "👎", "🎉", "❤️", "😄", "🤔", "😕", "❓", "👀", "⏳"]
    emoji = models.CharField(max_length=8)
    comment = models.ForeignKey(
        PuzzleComment, on_delete=models.CASCADE, related_name="reactions"
    )
    reactor = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="reactions"
    )

    class Meta:
        unique_together = ("emoji", "comment", "reactor")

    def __str__(self):
        return "{} reacted {} on {}".format(
            self.reactor.username, self.emoji, self.comment
        )

    @classmethod
    def toggle(cls, emoji, comment, reactor):
        # This just lets you react with any string to a comment, but it's
        # not the end of the world.
        my_reactions = cls.objects.filter(comment=comment, emoji=emoji, reactor=reactor)
        # Force the queryset instead of checking if it's empty because, if
        # it's not empty, we care about its contents.
        if len(my_reactions) > 0:
            my_reactions.delete()
        else:
            cls(emoji=emoji, comment=comment, reactor=reactor).save()


class TestsolveParticipationManager(Manager):
    def get_queryset(self, *args, **kwargs):
        # Prefetches the feedback text and sorts it by date
        return (
            super()
            .get_queryset(*args, **kwargs)
            .prefetch_related(
                Prefetch(
                    "feedbacks", queryset=TestsolveFeedback.objects.order_by("date")
                )
            )
        )


class TestsolveParticipation(models.Model):
    """Represents one user's participation in a testsolve session.

    Used to record the user's start and end time, as well as ratings on the
    testsolve."""

    id: int
    session = models.ForeignKey(
        TestsolveSession, on_delete=models.CASCADE, related_name="participations"
    )
    session_id: int
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="testsolve_participations"
    )
    started = models.DateTimeField(auto_now_add=True)
    ended = models.DateTimeField(null=True, blank=True)
    fun_rating = models.IntegerField(null=True, blank=True)
    difficulty_rating = models.IntegerField(null=True, blank=True)
    hours_spent = models.FloatField(
        null=True,
        help_text=(
            "**Hours spent**. Your best estimate of how many hours you spent on this"
            " puzzle. Decimal numbers are allowed."
        ),
    )

    objects = TestsolveParticipationManager()

    def __str__(self):
        return f"Testsolve participation #{self.id} in session #{self.session_id}"


@receiver(post_save, sender=TestsolveParticipation)
def add_testsolver_to_thread(
    sender, instance: TestsolveParticipation, created: bool, **kwargs
):
    if not created:
        return
    if discord.enabled():
        session = instance.session
        c = discord.get_client()
        thread = discord.get_thread(c, session)
        if not thread:
            return
        for did in discord.get_dids([instance.user]):
            if did:
                c.add_member_to_thread(thread.id, did)


class TestsolveFeedback(models.Model):
    """
    Contains textual feedback for a testsolve session.

    Numeric feedback is stored in the TestsolveParticipation.
    """

    id: int
    date = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)
    participation = models.ForeignKey(
        TestsolveParticipation, on_delete=models.CASCADE, related_name="feedbacks"
    )
    comment = models.OneToOneField(
        PuzzleComment,
        on_delete=models.CASCADE,
        related_name="testsolve_feedback",
        null=True,
        blank=True,
    )

    solve_path = models.TextField(
        blank=True,
        help_text="**Solve path**. What was the path you used to solve?",
    )

    meta_info = models.TextField(
        blank=True,
        help_text=(
            "**Meta info**. Did you use any information that wouldn't normally be"
            " available to solvers? For example, knowing the answer from the meta,"
            " knowing the meta mechanism or constraints, knowing the author, being"
            " spoiled on something, receiving nudges, new drafts, etc."
        ),
    )

    general_feedback = models.TextField(
        blank=True,
        help_text=(
            "**General feedback**. What did you like & dislike about this puzzle? Is"
            " there anything you think should be changed (e.g. amount of flavor/cluing,"
            " errata, tech issues, mechanics, theming, etc.)?"
        ),
    )

    aspects_accessibility = models.TextField(
        blank=True,
        help_text=(
            "Any accessibility concerns you'd like to raise? (alt text, color,"
            " transcripts, subtitles, tooltips, keyboard navigation, printing, etc.)"
        ),
    )

    def __str__(self):
        return f"Testsolve feedback #{self.id}"

    def make_comment(self, request: AuthenticatedHttpRequest, finish_method: str):
        participation = self.participation
        user = participation.user
        puzzle = participation.session.puzzle

        spoil_message = ""
        if is_spoiled_on(user, puzzle):
            spoil_message = "(solver was already spoiled)"
        elif finish_method == "SPOIL":
            spoil_message = "👀 solver is now spoiled"
        elif finish_method == "INCOMPLETE":
            spoil_message = "⏳ solver is not spoiled and plans to do more"
        elif finish_method == "NO_SPOIL":
            spoil_message = "🏁 solver is not spoiled but is done with this puzzle"

        ratings_text = " / ".join(
            [
                f"Fun: {participation.fun_rating or 'n/a'}",
                f"Difficulty: {participation.difficulty_rating or 'n/a'}",
                f"Hours spent: {participation.hours_spent or 'n/a'}",
            ]
        )

        if spoil_message:
            ratings_text += f" / {spoil_message}"

        comment_content = "\n".join(
            [
                "**Solve path**:",
                self.solve_path or "N/A",
                "",
                "**Meta info**:",
                self.meta_info or "N/A",
                "",
                "**General feedback**:",
                self.general_feedback or "N/A",
                "",
                "**Accessibility feedback**:",
                self.aspects_accessibility or "N/A",
                "",
                ratings_text,
                "",
                f"Testsolving session #{participation.session_id}",
                (
                    "Link to Spreadsheet:"
                    f" https://docs.google.com/spreadsheets/d/{participation.session.google_sheets_id}"
                ),
            ]
        )

        if not self.comment:
            self.comment = puzzle.add_comment(
                request=request,
                author=user,
                is_system=False,
                send_email=False,
                content=comment_content,
            )
            self.save()
        else:
            self.comment.content = comment_content
            self.comment.save()


class TestsolveGuess(models.Model):
    """A guess made by a user in a testsolve session."""

    session = models.ForeignKey(
        TestsolveSession, on_delete=models.CASCADE, related_name="guesses"
    )
    session_id: int
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="guesses")
    guess = models.TextField(max_length=500, blank=True)
    correct = models.BooleanField()
    partially_correct = models.BooleanField(default=False)
    partial_response = models.TextField(blank=True)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "testsolve guesses"

    def __str__(self):
        correct_text = (
            "Correct"
            if self.correct
            else "Partial" if self.partially_correct else "Incorrect"
        )
        return f"{self.guess}: {correct_text} guess in Session #{self.session_id}"


def is_spoiled_on(user: User, puzzle: Puzzle):
    return puzzle.spoiled.filter(id=user.id).exists()  # is this really the best way??


def is_author_on(user: User, puzzle: Puzzle):
    return puzzle.authors.filter(id=user.id).exists()


def is_editor_on(user: User, puzzle: Puzzle):
    return puzzle.editors.filter(id=user.id).exists()


def is_discussing_on(user: User, puzzle: Puzzle):
    return puzzle.discussion_editors.filter(id=user.id).exists()


def is_factchecker_on(user: User, puzzle: Puzzle):
    return (
        puzzle.factcheckers.filter(id=user.id).exists()
        or puzzle.quickcheckers.filter(id=user.id).exists()
    )


def is_postprodder_on(user: User, puzzle: Puzzle):
    return puzzle.postprodders.filter(id=user.id).exists()


def is_art_lead_on(user: User, puzzle: Puzzle):
    return (
        user.is_art_lead
        and puzzle.support_requests.filter(team=SupportRequest.Team.ART).exists()
    )


def is_supporting(user: User, puzzle: Puzzle):
    return user.assigned_support_requests.filter(puzzle=puzzle).exists()


def get_user_role(user, puzzle):
    if is_author_on(user, puzzle):
        return "author"
    elif is_editor_on(user, puzzle):
        return "editor"
    elif is_postprodder_on(user, puzzle):
        return "postprodder"
    elif is_factchecker_on(user, puzzle):
        return "factchecker"
    elif is_discussing_on(user, puzzle):
        return "discussion editor"
    elif is_art_lead_on(user, puzzle):
        return "art lead"
    elif is_supporting(user, puzzle):
        return "supporter"
    else:
        return None


class Hint(models.Model):
    id: int
    puzzle = models.ForeignKey(Puzzle, on_delete=models.PROTECT, related_name="hints")
    puzzle_id: int
    order = models.FloatField(
        blank=False,
        null=False,
        help_text=(
            "Order in the puzzle - use 0 for a hint at the very beginning of the"
            " puzzle, or 100 for a hint on extraction, and then do your best to"
            " extrapolate in between. Decimals are okay. For multiple subpuzzles,"
            " assign a whole number to each subpuzzle and use decimals off of that"
            " whole number for multiple hints in the subpuzzle."
        ),
    )
    description = models.CharField(
        max_length=1000,
        blank=False,
        null=False,
        help_text=(
            'A description of when this hint should apply; e.g. "The solvers have not'
            ' yet figured out that the mirrors represent word transformations"'
        ),
    )
    keywords = models.CharField(
        max_length=100,
        blank=True,
        null=False,
        help_text=(
            "Comma-separated keywords to look for in hunters' hint requests before"
            " displaying this hint suggestion"
        ),
    )
    content = models.CharField(
        max_length=1000,
        blank=False,
        null=False,
        help_text="Canned hint to give a team (can be edited by us before giving it)",
    )

    class Meta:
        unique_together = ("puzzle", "description")
        ordering = ["order"]

    def __str__(self):
        return f"Hint #{self.order} for {self.puzzle}"

    def get_keywords(self):
        return self.keywords.split(",")

    def get_yaml_data(self):
        return {
            "model": "spoilr_hints.cannedhint",
            "pk": self.id,
            "fields": {
                "puzzle": self.puzzle_id,
                "description": self.description,
                "order": self.order,
                "keywords": self.keywords,
                "content": self.content,
            },
        }


class SiteSetting(models.Model):
    """Arbitrary settings we don't want to customize from code."""

    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()

    def __str__(self):
        return f"{self.key} = {self.value}"

    @classmethod
    def get_setting(cls, key):
        try:
            return cls.objects.get(key=key).value
        except cls.DoesNotExist:
            return None

    @classmethod
    def get_int_setting(cls, key):
        try:
            return int(cls.objects.get(key=key).value)
        except cls.DoesNotExist:
            return None
        except ValueError:
            return None
