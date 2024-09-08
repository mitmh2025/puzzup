import datetime
import logging
import random
import re
import statistics
import urllib.parse
from collections.abc import Iterable
from types import MappingProxyType

import yaml
from dirtyfields import DirtyFieldsMixin  # type: ignore
from django import urls
from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.validators import (
    MaxValueValidator,
    RegexValidator,
)
from django.db import models
from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from requests import HTTPError

import puzzle_editing.discord_integration as discord
import puzzle_editing.google_integration as google
from puzzle_editing import messaging, status

logger = logging.getLogger(__name__)


class PuzzupUserManager(UserManager):
    def get_queryset(self, *args, **kwargs):
        return super().get_queryset(*args, **kwargs).prefetch_related("groups")

    def get(self, *args, **kwargs):
        # Prefetches the permission groups
        return super().prefetch_related("groups").get(*args, **kwargs)


class CustomUsernameValidator(UnicodeUsernameValidator):
    """Allows # (from discord)."""

    regex = r"^[\w.@#+-\\ ]+$"
    message = _(
        "Enter a valid username. This value may contain only letters, "
        "numbers, spaces, and \\/@/#/./+/-/_ characters."
    )


class User(AbstractUser):
    class Meta:
        # make Django always use the objects manager (so that we prefetch)
        base_manager_name = "objects"
        indexes = (
            models.Index(fields=["email"]),
            models.Index(fields=["discord_user_id"]),
        )

    objects = PuzzupUserManager()  # type: ignore

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
    discord_user_id = models.CharField(
        max_length=500, null=True, blank=True, unique=True
    )

    display_name = models.CharField(
        max_length=500,
        blank=True,
        help_text="How you want your name to appear to other puzzup users.",
    )

    credits_name = models.CharField(
        max_length=80,
        help_text=(
            "How you want your name to appear in puzzle credits, e.g. " "Ben Bitdiddle"
        ),
    )
    bio = models.TextField(
        blank=True,
        help_text=(
            "Tell us about yourself. What kinds of puzzle genres or "
            "subject matter do you like?"
        ),
    )

    timezone = models.CharField(
        max_length=100,
        blank=True,
        help_text="Your timezone, e.g. US/Eastern",
    )

    def save(self, *args, **kwargs):
        # Empty string is not unique, but uniqueness isn't enforced on null
        if self.discord_user_id == "":
            self.discord_user_id = None
        super().save(*args, **kwargs)

    @property
    def is_eic(self):
        return any(g.name == "EIC" for g in self.groups.all())

    @property
    def is_editor(self):
        return any(g.name == "Editor" for g in self.groups.all())

    @property
    def is_art_lead(self):
        return any(g.name == "Art Lead" for g in self.groups.all())

    @property
    def is_testsolve_coordinator(self):
        return any(g.name == "Testsolve Coordinators" for g in self.groups.all())

    @property
    def full_display_name(self):
        return "".join(
            [
                str(self),
                f" (@{self.discord_username})" if self.discord_username else "",
            ]
        ).strip()

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
        if display_name:
            ret = format_html(
                '<span data-tippy-content="{}">{}</span>', username, display_name
            )
        else:
            ret = username

        if linkify:
            return format_html(
                '<a href="{}">{}</a>', urls.reverse("user", args=[username]), ret
            )
        else:
            return ret

    @staticmethod
    def html_user_display_of(user, linkify):
        if not user:
            return mark_safe('<span class="empty">--</span>')
        return User.html_user_display_of_flat(user.username, str(user), linkify)

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
            ((user.username, str(user)) for user in users),
            linkify,
        )

    @staticmethod
    def get_testsolve_coordinators():
        return User.objects.filter(groups__name="Testsolve Coordinators")

    @staticmethod
    def get_eics():
        return User.objects.filter(groups__name="EIC")

    def __str__(self):
        return (
            self.display_name
            or self.credits_name
            or self.discord_username
            or self.username
        )

    def get_absolute_url(self):
        return urls.reverse("user", kwargs={"username": self.username})


class Round(models.Model):
    """A round of answers feeding into the same metapuzzle or set of metapuzzles."""

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

    def __str__(self):  # pylint: disable=invalid-str-returned
        return self.name

    def get_absolute_url(self):
        return urls.reverse("round", kwargs={"id": self.id})


class PuzzleAnswer(models.Model):
    """An answer. Can be assigned to zero, one, or more puzzles."""

    answer = models.TextField(blank=True)
    round = models.ForeignKey(Round, on_delete=models.PROTECT, related_name="answers")
    notes = models.TextField(blank=True)
    flexible = models.BooleanField(
        default=False,
        help_text="Whether or not this answer is easy to change and satisfy meta constraints.",
    )
    case_sensitive = models.BooleanField(
        default=False,
        help_text="Whether or not this answer needs to be submitted with the correct casing.",
    )
    whitespace_sensitive = models.BooleanField(
        default=False,
        help_text="Whether or not this answer shouldn't ignore whitespaces.",
    )

    def __str__(self):  # pylint: disable=invalid-str-returned
        return self.answer

    def get_absolute_url(self):
        return urls.reverse("edit_answer", kwargs={"id": self.id})

    def to_json(self):
        return {
            "answer": self.answer,
            "id": self.id,
            "notes": self.notes,
            "flexible": self.flexible,
            "puzzles": self.puzzles.all(),
            "whitespace_sensitive": self.whitespace_sensitive,
        }

    def normalize_answer(self, answer, ignore_case=True, ignore_whitespace=True):
        normalized = answer
        if ignore_whitespace:
            normalized = "".join(c for c in normalized if not c.isspace())
        if ignore_case:
            normalized = normalized.upper()

        return normalized

    def is_correct(self, guess):
        normalized_guess = self.normalize_answer(
            guess,
            ignore_case=not self.case_sensitive,
            ignore_whitespace=not self.whitespace_sensitive,
        )
        normalized_answer = self.normalize_answer(
            self.answer,
            ignore_case=not self.case_sensitive,
            ignore_whitespace=not self.whitespace_sensitive,
        )
        return normalized_answer == normalized_guess


class PuzzleTag(models.Model):
    """A tag to classify puzzles."""

    name = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    important = models.BooleanField(
        default=False,
        help_text="Important tags are displayed prominently with the puzzle title.",
    )

    def __str__(self):
        return f"Tag: {self.name}"

    def get_absolute_url(self):
        return urls.reverse("single_tag", kwargs={"id": self.id})


def generate_codename():
    with (settings.BASE_DIR / "puzzle_editing/data/nouns-eng.txt").open() as f:
        nouns = [line.strip() for line in f.readlines()]
    random.shuffle(nouns)

    with (settings.BASE_DIR / "puzzle_editing/data/adj-eng.txt").open() as g:
        adjs = [line.strip() for line in g.readlines()]
    random.shuffle(adjs)

    try:
        name = adjs.pop() + "-" + nouns.pop()
        while Puzzle.objects.filter(codename=name).exists():
            name = adjs.pop() + " " + nouns.pop()
    except IndexError:
        return "Make up your own name!"

    return name


class Puzzle(DirtyFieldsMixin, models.Model):
    """A puzzle, that which Puzzup keeps track of the writing process of."""

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
    discord_info_message_id = models.CharField(
        max_length=19,
        blank=True,
    )

    authors = models.ManyToManyField(User, related_name="authored_puzzles", blank=True)
    lead_author = models.ForeignKey(
        User,
        related_name="led_puzzles",
        null=True,
        on_delete=models.PROTECT,
        help_text="The author responsible for driving the puzzle forward and getting it over the finish line.",
    )
    authors_addl = models.CharField(
        max_length=200,
        help_text="The second line of author credits. Only use in cases where a standard author credit isn't accurate.",
        blank=True,
    )

    editors = models.ManyToManyField(User, related_name="editing_puzzles", blank=True)
    needed_editors = models.IntegerField(default=2)
    spoiled = models.ManyToManyField(
        User,
        related_name="spoiled_puzzles",
        blank=True,
        help_text="Users spoiled on the puzzle.",
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
    status_mtime = models.DateTimeField(editable=False)

    last_updated = models.DateTimeField(auto_now=True)

    summary = models.TextField(
        blank=True,
        help_text="Describe what people will see when they open the puzzle page with **NO SPOILERS**.  _Examples: 'A long list of crossword clues with several rebus puzzles below' or 'Puzzle looks like sheet music, but with emoji scattered throughout.'_",
    )
    description = models.TextField(
        help_text='**Describe your puzzle idea** fully here, including "ahas" that solvers will have to solve the puzzle and the mechanics of extracting an answer.  What will solvers actually be doing in this puzzle, and what is fun/hard/interesting about it?  If you have constructed any examples as "proof of concept," or know of a similar puzzle to yours, describe that here.',
    )
    editor_notes = models.TextField(
        blank=True,
        verbose_name="Mechanics",
        help_text="A **succinct list** of mechanics and themes used (can contain spoilers). _Examples: Geoguessr, Sudoku, Taylor Swift music videos_",
    )
    notes = models.TextField(
        blank=True,
        help_text="State what answer constraints your puzzle has and what round you'd prefer it to go in (if applicable). Editors will do their best to honor these constraints and reach out to you if they can't be satisfied.",
    )
    private_notes = models.TextField(
        blank=True,
        verbose_name="Private notes",
        help_text="Private notes about this puzzle, visible only to the EICs",
    )
    flavor = models.TextField(
        blank=True,
        help_text="Puzzle flavor used by creative team to motivate round art, such as 'puzzle consists of performers swallowing swords' or 'puzzle is themed as a ride through a tunnel of love'.",
    )
    flavor_approved_time = models.DateTimeField(auto_now=False, blank=True, null=True)
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
    content_google_doc_id = models.CharField(
        max_length=64,
        blank=True,
    )
    solution_google_doc_id = models.CharField(
        max_length=64,
        blank=True,
    )
    resource_google_folder_id = models.CharField(
        max_length=64,
        blank=True,
    )
    is_meta = models.BooleanField(
        verbose_name="Is this a meta?", help_text="Check the box if yes.", default=False
    )

    # How many clean testsolves has this puzzle had
    logistics_clean_testsolve_count = models.PositiveSmallIntegerField(
        default=0, blank=False, null=False
    )
    logistics_closed_testsolving = models.BooleanField(
        default=False,
        help_text="Check this box if this puzzle should only allow testsolve sessions created by a testsolve coordinator",
    )
    # From 0-2, what is the expected difficulty of this puzzle across various fields?
    logistics_difficulty_testsolve = models.PositiveSmallIntegerField(
        validators=[MaxValueValidator(2)], blank=True, null=True
    )
    logistics_difficulty_postprod = models.PositiveSmallIntegerField(
        validators=[MaxValueValidator(2)], blank=True, null=True
    )
    logistics_difficulty_factcheck = models.PositiveSmallIntegerField(
        validators=[MaxValueValidator(2)], blank=True, null=True
    )
    logistics_needs_final_day_factcheck = models.BooleanField(
        verbose_name="Needs final day factcheck",
        help_text="Are any facts or statuses of things outside the puzzle used in the puzzle that could possibly change before hunt, e.g. the ice cream flavors available at a certain store, or content on an external webpage? If so, factchecking will do a final day check.",
        default=False,
    )
    # Additional logistics information
    logistics_number_testsolvers = models.CharField(max_length=512, blank=True)
    logistics_testsolve_length = models.CharField(max_length=512, blank=True)
    logistics_testsolve_skills = models.CharField(max_length=512, blank=True)

    SPECIALIZED_TYPES = (
        ("PHY", "Physical Puzzle"),
        ("EVE", "Event"),
        ("", "None of the Above"),
    )

    logistics_specialized_type = models.CharField(
        max_length=3, choices=SPECIALIZED_TYPES, blank=True
    )

    class Meta:
        permissions = (
            ("list_puzzle", "Can see all puzzles"),
            ("unspoil_puzzle", "Can unspoil people"),
            ("change_status_puzzle", "Can change puzzle status"),
        )

    def __str__(self):
        return self.spoiler_free_title()

    def save(self, *args, **kwargs) -> None:
        status_changed = "status" in self.get_dirty_fields()
        # Make sure lead author is always spoiled and is always an author (see update_spoiled below for the m2m version)
        super().save(*args, **kwargs)
        if self.lead_author:
            self.authors.add(self.lead_author)
            self.spoiled.add(self.lead_author)
        super().save(*args, **kwargs)
        if status_changed:
            send_status_notifications(self)

    def get_absolute_url(self):
        return urls.reverse("puzzle_w_slug", kwargs={"id": self.id, "slug": self.slug})

    def spoiler_free_name(self):
        if self.codename:
            return f"({self.codename})"
        return self.name

    def spoiler_free_title(self):
        return self.spoiler_free_name()

    @property
    def spoilery_title(self):
        name = self.name
        if self.codename:
            name += f" ({self.codename})"
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
            "{}: {} {}",
            self.id,
            format_html_join(
                " ",
                "<sup>[{}]</sup>",
                ((name,) for name in self.important_tag_names()),
            ),
            self.spoiler_free_name(),
        )

    def puzzle_url(self):
        return urls.reverse("puzzle", args=[self.id])

    def html_link(self):
        return format_html(
            """<a href="{}" class="puzzle-link">{}</a>""",
            self.puzzle_url(),
            self.html_display(),
        )

    def html_link_no_tags(self):
        return format_html(
            """<a href="{}" class="puzzle-link">{}</a>""",
            self.puzzle_url(),
            self.spoiler_free_name(),
        )

    def get_status_rank(self):
        return status.get_status_rank(self.status)

    def get_status_emoji(self):
        return status.get_emoji(self.status)

    def get_blocker(self):
        # just text describing what the category of blocker is, not a list of
        # Users or anything like that
        return status.get_blocker(self.status)

    def get_transitions(self):
        return [
            {
                "status": s,
                "status_display": status.get_display(s),
                "description": description,
            }
            for s, description in status.get_transitions(self.status)
        ]

    def get_emails(self, exclude_emails=()):
        # tcs = User.objects.filter(groups__name__in=['Testsolve Coordinators']).exclude(email="").values_list("email", flat=True)

        emails = set(self.authors.values_list("email", flat=True))
        emails |= set(self.editors.values_list("email", flat=True))
        emails |= set(self.factcheckers.values_list("email", flat=True))
        emails |= set(self.postprodders.values_list("email", flat=True))

        emails -= set(exclude_emails)
        emails -= {""}

        return list(emails)

    def get_content_url(self, user: User | None = None) -> str | None:
        if not self.content_google_doc_id:
            return None

        url = f"https://docs.google.com/document/u/0/d/{urllib.parse.quote(self.content_google_doc_id)}/edit"
        if user and user.is_authenticated:
            url += f"?{urllib.parse.urlencode({'authuser': user.email})}"
        return url

    def get_solution_url(self, user: User | None = None) -> str | None:
        if not self.solution_google_doc_id:
            return None

        url = f"https://docs.google.com/document/u/0/d/{urllib.parse.quote(self.solution_google_doc_id)}/edit"
        if user and user.is_authenticated:
            url += f"?{urllib.parse.urlencode({'authuser': user.email})}"
        return url

    def get_resource_url(self, user: User | None = None) -> str | None:
        if not self.resource_google_folder_id:
            return None

        url = f"https://drive.google.com/drive/u/0/folders/{urllib.parse.quote(self.resource_google_folder_id)}"
        if user and user.is_authenticated:
            url += f"?{urllib.parse.urlencode({'authuser': user.email})}"
        return url

    def has_postprod(self):
        try:
            return self.postprod is not None
        except PuzzlePostprod.DoesNotExist:
            return False

    def has_factcheck(self):
        try:
            return self.factcheck is not None
        except PuzzleFactcheck.DoesNotExist:
            return False

    def has_hints(self):
        return self.hints.count() > 0

    def ordered_hints(self):
        return self.hints.order_by("order")

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
    def author_byline(self):
        credits = [u.credits_name for u in self.authors.all()]
        credits.sort(key=lambda u: u.upper())
        if len(credits) == 2:
            return " and ".join(credits)
        else:
            return re.sub(r"([^,]+?), ([^,]+?)$", r"\1, and \2", ", ".join(credits))

    @property
    def answer(self):
        try:
            self._prefetched_objects_cache[self.answers.prefetch_cache_name]
            return ", ".join(a.answer for a in self.answers.all())
        except (AttributeError, KeyError):
            return ", ".join(self.answers.values_list("answer", flat=True)) or None

    @property
    def round(self):
        return next(iter(a.round for a in self.answers.all()), None)

    @property
    def round_name(self):
        return next(iter(a.round.name for a in self.answers.all()), None)

    @property
    def metadata(self):
        editors = [u.credits_name for u in self.editors.all()]
        editors.sort(key=lambda u: u.upper())
        postprodders = [u.credits_name for u in self.postprodders.all()]
        postprodders.sort(key=lambda u: u.upper())
        return {
            "puzzle_title": self.name,
            "credits": f"by {self.author_byline}",
            "answer": self.answer or "???",
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
            "puzzle_slug": self.postprod.slug
            if self.has_postprod()
            else re.sub(
                r'[<>#%\'"|{}\[\])(\\\^?=`;@&,]',
                "",
                re.sub(r"[ \/]+", "-", self.name),
            ).lower(),
        }

    @property
    def slug(self):
        return slugify(self.codename.lower())

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
                # TODO: don't hardcode remaining fields
                "icon_x": 0,
                "icon_y": 0,
                "icon_size": 0,
                "text_x": 0,
                "text_y": 0,
                "testsolve_url": None,
                "unsolved_icon": "",
                "solved_icon": "",
                "points": 1,
            },
        }

        spoilr_puzzle_data = {
            "model": "spoilr_core.puzzle",
            "pk": self.id,
            "fields": {
                "external_id": self.id,
                "round": metadata["round"],
                "answer": metadata["answer"],
                "name": self.name,
                "credits": metadata["credits"],
                "order": self.id,
                "is_meta": self.is_meta,
                "slug": metadata["puzzle_slug"],
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


DISCORD_NOTICE_STATUS_GROUPS = [
    {
        status.AWAITING_ANSWER,
        status.WRITING_FLEXIBLE,
    },
    {
        status.TESTSOLVING,
    },
    {
        # Any of these indicate that the puzzle passed testsolving:
        status.NEEDS_SOLUTION,
        status.AWAITING_ANSWER_FLEXIBLE,
        status.NEEDS_POSTPROD,
    },
]

DISCORD_NOTICE_CELEBRATION_SENTENCE = (
    "Stuff is happening 🚨! ",
    "There's been a development 📰! ",
    "Puzzles are moving 🚚!",
    "Can you believe it 😮? ",
    "The pieces are falling into place 🧩! ",
    "The plot thickens 📚! ",
    "Progress marches on 🏃! ",
    "The wheels are turning 🔄! ",
)

DISCORD_NOTICE_CELEBRATION_EMOJI = (
    "🎉",
    "🎊",
    "🎈",
    "🥳",
    "🎆",
    "🎇",
    "🧨",
    "🔥",
    "🌟",
    "✨",
    "🎂",
    "🍰",
    "🥂",
    "🍾",
    "🍻",
    "😍",
)


def send_status_notifications(puzzle: Puzzle) -> None:
    if puzzle.is_meta:
        meta_filter = (
            StatusSubscription.MetaFilter.ALL,
            StatusSubscription.MetaFilter.META_ONLY,
        )
    else:
        meta_filter = (
            StatusSubscription.MetaFilter.ALL,
            StatusSubscription.MetaFilter.NON_META_ONLY,
        )
    subscriptions = (
        StatusSubscription.objects.filter(
            status=puzzle.status, meta_filter__in=meta_filter
        )
        .exclude(user__email="")
        .values_list("user__email", flat=True)
    )
    status_display = status.get_display(puzzle.status)
    status_emoji = status.get_emoji(puzzle.status)
    if subscriptions:
        messaging.send_mail_wrapper(
            f"{puzzle.spoiler_free_title()} ➡ {status_display}",
            "emails/status_update_email",
            {
                "settings": settings,
                "puzzle": puzzle,
                "status": status_display,
            },
            subscriptions,
        )

    should_hype = False
    # Hype a puzzle if (a) it's going into open testsolving (b) this is the first
    # time it's entered a status group
    if (
        puzzle.status == status.TESTSOLVING and not puzzle.logistics_closed_testsolving
    ) or any(
        puzzle.status in group
        and puzzle.comments.filter(status_change__in=group).count() <= 1
        for group in DISCORD_NOTICE_STATUS_GROUPS
    ):
        should_hype = True

    re_testing = (
        puzzle.status == status.TESTSOLVING
        and puzzle.comments.filter(status_change__in=status.TESTSOLVING).count() > 1
    )

    # Check if this is the first time the puzzle has entered this group of statuses
    if (c := discord.get_client()) and should_hype:
        message = random.choice(DISCORD_NOTICE_CELEBRATION_SENTENCE)
        message += f" Congrats to author(s) {", ".join(discord.mention_users(puzzle.authors.all()))}"
        if puzzle.editors.exists():
            message += f" and editor(s) {', '.join(discord.mention_users(puzzle.editors.all()))}"
        message += f" on moving{" (metapuzzle)" if puzzle.is_meta else ""} {puzzle.codename}{" **back**" if re_testing else ""} to {status_display}{f" {status_emoji}" if status_emoji else ""}!"

        if puzzle.status == status.TESTSOLVING:
            if puzzle.logistics_closed_testsolving:
                message += " (Testsolvers, don't get too excited — our testsolve coordinators are going to do some manual coordinating for this particular puzzle 🤫. But hold tight; more puzzles are coming your way soon!)"
            else:
                message += f" Testsolvers, get your pencils ✏️ ready, find a group, and [get to testsolving]({settings.PUZZUP_URL}{urls.reverse("testsolve_main")})!"
        if puzzle.status in (
            status.NEEDS_SOLUTION,
            status.AWAITING_ANSWER_FLEXIBLE,
            status.NEEDS_POSTPROD,
        ):
            message += " That means this puzzle has graduated from testsolving!"

        if (
            puzzle.status == status.TESTSOLVING
            and not puzzle.logistics_closed_testsolving
            and settings.DISCORD_TESTSOLVE_HYPE_CHANNEL_ID
        ):
            try:
                c.post_message(settings.DISCORD_TESTSOLVE_HYPE_CHANNEL_ID, message)
            except HTTPError as e:
                # swallow rate limiting errors
                if e.response.status_code != 429:
                    raise

        if settings.DISCORD_HYPE_CHANNEL_ID:
            try:
                message_id = c.post_message(settings.DISCORD_HYPE_CHANNEL_ID, message)[
                    "id"
                ]
                emoji = random.choices(DISCORD_NOTICE_CELEBRATION_EMOJI, k=2)
                for em in emoji:
                    c.add_reaction(settings.DISCORD_HYPE_CHANNEL_ID, message_id, em)
            except HTTPError as e:
                # swallow rate limiting errors
                if e.response.status_code != 429:
                    raise


@receiver(pre_save, sender=Puzzle)
def pre_save_puzzle(sender, instance, **kwargs):
    if "status" in instance.get_dirty_fields():
        instance.status_mtime = timezone.now()


@receiver(post_save, sender=Puzzle)
def post_save_puzzle(sender, instance, created, **kwargs):
    changed = False

    discord.sync_puzzle_channel(discord.get_client(), instance)

    if google.enabled():
        gm = google.GoogleManager.instance()
        if not instance.content_google_doc_id:
            instance.content_google_doc_id = gm.create_puzzle_content_doc(instance)
            changed = True
        if not instance.solution_google_doc_id:
            instance.solution_google_doc_id = gm.create_puzzle_solution_doc(instance)
            changed = True
        if not instance.resource_google_folder_id:
            instance.resource_google_folder_id = gm.create_puzzle_resources_folder(
                instance
            )
            changed = True

    if instance.status == status.NEEDS_FACTCHECK and not getattr(
        instance, "factcheck", None
    ):
        # Create a factcheck object the first time state changes to NEEDS_FACTCHECK
        PuzzleFactcheck(puzzle=instance).save()

    if changed:
        instance.save()


@receiver(m2m_changed, sender=Puzzle.authors.through)
@receiver(m2m_changed, sender=Puzzle.editors.through)
@receiver(m2m_changed, sender=Puzzle.spoiled.through)
def update_spoiled(sender, instance, action, **kwargs):
    should_update = False
    if sender == Puzzle.spoiled.through:
        should_update = action in ("post_remove", "post_clear")
    else:
        should_update = action == "post_add"
    if should_update:
        instance.spoiled.add(*instance.authors.all())
        instance.spoiled.add(*instance.editors.all())


class PseudoAnswer(models.Model):
    """
    Possible answers a solver might input that don't mark the puzzle as correct,
    but need handling.
    For example, they might provide a nudge for teams that are on the right
    track, or special instructions for how to obtain the correct answer.
    """

    puzzle = models.ForeignKey(
        Puzzle, on_delete=models.CASCADE, related_name="pseudo_answers"
    )
    answer = models.TextField(max_length=100)
    response = models.TextField()

    class Meta:
        unique_together = ("puzzle", "answer")
        ordering = ("puzzle", "answer")

    def __str__(self):
        return f'"{self.puzzle.name}" ({self.answer})'

    def get_absolute_url(self):
        return urls.reverse("edit_pseudo_answer", kwargs={"id": self.id})

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

    def normalize(self, text):
        normalized = text
        normalized = "".join(c for c in normalized if not c.isspace())
        normalized = normalized.upper()
        return normalized

    def is_correct(self, guess):
        normalized_guess = self.normalize(guess)
        normalized_answer = self.normalize(self.answer)
        return normalized_answer == normalized_guess


class PuzzleCredit(models.Model):
    """A miscellaneous puzzle credit, such as Art"""

    class CreditType(models.TextChoices):
        ART = ("ART", "Art")
        TECH = ("TCH", "Tech")
        OTHER = ("OTH", "Other")

    puzzle = models.ForeignKey(
        Puzzle, related_name="other_credits", on_delete=models.CASCADE
    )

    users = models.ManyToManyField(User, related_name="other_credits", blank=True)

    text = models.TextField(blank=True)

    credit_type = models.CharField(
        max_length=3, choices=CreditType.choices, default=CreditType.ART
    )

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

    def get_absolute_url(self):
        return urls.reverse(
            "puzzle_other_credit_update",
            kwargs={"puzzle_id": self.puzzle_id, "id": self.id},
        )


class SupportRequest(models.Model):
    """A request for support from one of our departments."""

    class Team(models.TextChoices):
        ART = ("ART", "🎨 Art")
        ACC = ("ACC", "🔎 Accessibility")
        FAB = ("FAB", "🔨 Fabrication")
        OPS = ("OPS", "🚧 Operations")
        TECH = ("TECH", "👩🏽‍💻 Tech")

    TEAM_TO_GROUP = MappingProxyType(
        {
            Team.ART: "Art Lead",
            Team.ACC: "Accessibility Lead",
            Team.FAB: "Fabrication Lead",
            Team.OPS: "Ops Lead",
            Team.TECH: "Tech Lead",
        }
    )

    GROUP_TO_TEAM = MappingProxyType(
        {
            "Art Lead": Team.ART,
            "Accessibility Lead": Team.ACC,
            "Fabrication Lead": Team.FAB,
            "Ops Lead": Team.OPS,
            "Tech Lead": Team.TECH,
        }
    )

    class Status(models.TextChoices):
        NONE = ("NO", "No need")
        REQUESTED = ("REQ", "Requested")
        APPROVED = ("APP", "Triaged, waiting on team")
        BLOCK = ("BLOK", "Triaged, waiting on puzzle")
        COMPLETE = ("COMP", "Completed")
        CANCELLED = ("X", "Cancelled")

    team = models.CharField(max_length=4, choices=Team.choices)
    puzzle = models.ForeignKey(Puzzle, on_delete=models.CASCADE)
    status = models.CharField(
        max_length=4, choices=Status.choices, default=Status.REQUESTED
    )
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
        return f'{self.get_team_display()} request for "{self.puzzle.name}"'

    def get_absolute_url(self):
        return urls.reverse(
            "support_by_puzzle_id", kwargs={"id": self.puzzle_id, "team": self.team}
        )

    def get_emails(self) -> list[str]:
        emails = {
            u.email
            for u in User.objects.filter(
                groups__name=self.TEAM_TO_GROUP[self.Team[self.team]]
            )
            if u.email
        }
        if self.team_notes_updater and self.team_notes_updater.email:
            emails.add(self.team_notes_updater.email)

        return list(emails)


class PuzzlePostprod(models.Model):
    puzzle = models.OneToOneField(
        Puzzle, on_delete=models.CASCADE, related_name="postprod"
    )
    slug = models.CharField(
        max_length=100,
        null=False,
        blank=False,
        validators=[RegexValidator(regex=r'[^<>#%"\'|{})(\[\]\/\\\^?=`;@&, ]{1,100}')],
        help_text="The part of the URL on the hunt site referrring to this puzzle. E.g. for https://puzzle.hunt/puzzle/fifty-fifty, this would be 'fifty-fifty'.",
    )
    mtime = models.DateTimeField(auto_now=True)
    host_url = models.CharField(
        max_length=255,
        blank=True,
        help_text="The base URL where this puzzle is postprodded. Defaults to staging",
    )

    def __str__(self):
        return f"<Postprod {self.slug}>"

    def get_url(self, is_solution=False):
        host = self.host_url if self.host_url else settings.POSTPROD_URL
        subpath = "solutions" if is_solution else "puzzles"
        return f"{host}/{subpath}/{self.slug}"


class PuzzleFactcheck(models.Model):
    """Tracks factchecking for a puzzle."""

    puzzle = models.OneToOneField(
        Puzzle, on_delete=models.CASCADE, related_name="factcheck"
    )
    google_sheet_id = models.CharField(max_length=100)
    output = models.TextField(blank=True)

    def __str__(self):
        return f"<Factcheck {self.puzzle_id} {self.puzzle.spoiler_free_name()}>"


@receiver(post_save, sender=PuzzleFactcheck)
def post_save_factcheck(sender, instance, created, **kwargs):
    if not created:
        return

    changed = False
    if google.enabled():
        instance.google_sheet_id = (
            google.GoogleManager.instance().create_factchecking_sheet(instance.puzzle)
        )
        changed = True

    if changed:
        instance.save()


class StatusSubscription(models.Model):
    """An indication to email a user when any puzzle enters this status."""

    status = models.CharField(
        max_length=status.MAX_LENGTH,
        choices=status.DESCRIPTIONS.items(),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    class MetaFilter(models.TextChoices):
        ALL = "A", "All"
        META_ONLY = "M", "Meta Only"
        NON_META_ONLY = "N", "Non-Meta Only"

    meta_filter = models.CharField(
        max_length=1,
        choices=MetaFilter.choices,
        default=MetaFilter.ALL,
    )

    def __str__(self):
        return f"{self.user} subscription ({self.get_meta_filter_display()}) to {status.get_display(self.status)}"


class PuzzleVisited(models.Model):
    """A model keeping track of when a user last visited a puzzle page."""

    puzzle = models.ForeignKey(Puzzle, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} visited {self.puzzle}"


class TestsolveSession(models.Model):
    """An attempt by a group of people to testsolve a puzzle.

    Participants in the session will be able to make comments and see other
    comments in the session. People spoiled on the puzzle can also comment and
    view the participants' comments.
    """

    puzzle = models.ForeignKey(
        Puzzle, on_delete=models.CASCADE, related_name="testsolve_sessions"
    )
    started = models.DateTimeField(auto_now_add=True)
    joinable = models.BooleanField(
        default=True,
        help_text="Whether this puzzle is advertised to other users as a session they can join.",
    )
    notes = models.TextField(blank=True)
    late_testsolve = models.BooleanField(
        default=False,
        help_text="Whether this testsolve occurred after the puzzle had passed testsolving",
    )

    discord_thread_id = models.CharField(
        max_length=19,
        blank=True,
    )
    puzzle_copy_google_doc_id = models.CharField(
        max_length=64,
        blank=True,
    )
    google_sheets_id = models.CharField(
        max_length=64,
        blank=True,
    )

    class Meta:
        permissions = (("close_session", "Can close a session at any point"),)

    def __str__(self):
        return f"Testsolve session #{self.id} on {self.puzzle}"

    def get_absolute_url(self):
        return urls.reverse("testsolve_one", kwargs={"id": self.id})

    def get_time_since_started(self):
        td = datetime.datetime.now(tz=datetime.UTC) - self.started
        minutes = td.total_seconds() / 60.0
        hours, minutes = divmod(minutes, 60.0)
        days, hours = divmod(hours, 24.0)
        return days, hours, minutes

    @property
    def is_expired(self):
        days = self.get_time_since_started()[0]
        return days >= 2

    @property
    def time_since_started(self):
        days, hours, minutes = self.get_time_since_started()

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

    @property
    def ended(self):
        return len(self.active_participants()) == 0

    def participants(self) -> Iterable[User]:
        for p in self.participations.select_related("user").all():
            yield p.user

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

    def get_puzzle_copy_url(self, user: User | None = None) -> str | None:
        if not self.puzzle_copy_google_doc_id:
            return None
        url = f"https://docs.google.com/document/u/0/d/{urllib.parse.quote(self.puzzle_copy_google_doc_id)}/edit"
        if user and user.is_authenticated:
            url += f"?{urllib.parse.urlencode({'authuser': user.email})}"
        return url

    def get_sheet_url(self, user: User | None = None) -> str | None:
        if not self.google_sheets_id:
            return None
        url = f"https://docs.google.com/spreadsheets/u/0/d/{urllib.parse.quote(self.google_sheets_id)}/edit"
        if user and user.is_authenticated:
            url += f"?{urllib.parse.urlencode({'authuser': user.email})}"
        return url


@receiver(post_save, sender=TestsolveSession)
def post_save_testsolve_session(
    sender, instance: TestsolveSession, created: bool, **kwargs
):
    if not created:
        return

    try:
        c = discord.get_client()
        if c:
            discord.make_testsolve_thread(c, instance)
    except Exception as e:
        logger.exception("Failed to create Discord thread", exc_info=e)

    content_id, sheet_id = "", ""
    try:
        (
            content_id,
            sheet_id,
        ) = google.GoogleManager.instance().create_testsolving_folder(instance)
    except Exception as e:
        logger.exception("Failed to create Google sheet", exc_info=e)

    changed = False
    if sheet_id:
        instance.puzzle_copy_google_doc_id = content_id
        instance.google_sheets_id = sheet_id
        changed = True
    if changed:
        instance.save()


class PuzzleComment(models.Model):
    """A comment on a puzzle.

    All comments on a puzzle are visible to people spoiled on the puzzle.
    Comments may or may not be associated with a testsolve session; if they
    are, they will also be visible to people participating in or viewing the
    session."""

    puzzle = models.ForeignKey(
        Puzzle, on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="comments")
    date = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)
    is_system = models.BooleanField()
    is_feedback = models.BooleanField(
        help_text="Whether or not this comment is created as feedback from a testsolve session"
    )
    testsolve_session = models.ForeignKey(
        TestsolveSession,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="comments",
    )
    content = models.TextField(
        blank=True,
        help_text="The content of the comment. Should probably only be blank if the status_change is set.",
    )
    status_change = models.CharField(
        max_length=status.MAX_LENGTH,
        choices=status.DESCRIPTIONS.items(),
        blank=True,
        help_text="Any status change caused by this comment. Only used for recording history and computing statistics; not a source of truth (i.e. the puzzle will still store its current status, and this field's value on any comment doesn't directly imply anything about that in any technically enforced way).",
    )

    def __str__(self):
        return f"Comment #{self.id} on {self.puzzle}"

    def get_absolute_url(self):
        return f"{self.puzzle.get_absolute_url()}#comment-{self.id}"


class CommentReaction(models.Model):
    # Since these are frivolous and display-only, I'm not going to bother
    # restricting them on the database model layer.
    EMOJI_OPTIONS = ("👍", "👎", "🎉", "❤️", "😄", "🤔", "😕", "❓", "👀", "🍖")
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
        return f"{self.reactor.username} reacted {self.emoji} on {self.comment}"

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


class TestsolveParticipation(models.Model):
    """Represents one user's participation in a testsolve session.

    Used to record the user's start and end time, as well as ratings on the
    testsolve."""

    session = models.ForeignKey(
        TestsolveSession, on_delete=models.CASCADE, related_name="participations"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="testsolve_participations"
    )
    in_discord_thread = models.BooleanField(default=False)
    started = models.DateTimeField(auto_now_add=True)
    ended = models.DateTimeField(null=True, blank=True)
    fun_rating = models.IntegerField(null=True, blank=True)
    difficulty_rating = models.IntegerField(null=True, blank=True)
    hours_spent = models.FloatField(
        null=True,
        help_text="**Hours spent**. Your best estimate of how many hours you spent on this puzzle. Decimal numbers are allowed.",
    )

    general_feedback = models.TextField(
        blank=True,
        help_text="What did you like & dislike about this puzzle? Is there anything you think should be changed (e.g. amount of flavor/cluing, errata, tech issues, mechanics, theming, etc.)?",
    )

    misc_feedback = models.TextField(
        blank=True,
        help_text="Anything else you want to add? If you were spoiled, mention it here. (This can include: things you tried, any shortcuts you found, break-ins, stuck points, accessibility)",
    )

    clues_needed = models.TextField(
        blank=True,
        help_text="Did you solve the complete puzzle before getting the answer, or did you shortcut, and if so, how much remained unsolved?",
    )

    aspects_enjoyable = models.TextField(
        blank=True,
        help_text="What parts of the puzzle were particularly enjoyable, if any?",
    )
    aspects_unenjoyable = models.TextField(
        blank=True,
        help_text="What parts of the puzzle were not enjoyable, if any?",
    )
    aspects_accessibility = models.TextField(
        blank=True,
        help_text="If you have physical issues such as a hearing impairment, vestibular disorder, etc., what problems did you encounter with this puzzle, if any?",
    )

    technical_issues = models.BooleanField(
        default=False,
        null=False,
        help_text="Did you encounter any technical problems with any aspect of the puzzle, including problems with your browser, any assistive device, etc. as well as any puzzle-specific tech?",
    )
    technical_issues_device = models.TextField(
        blank=True,
        help_text="**If Yes:** What type of device was the issue associated with? Please be as specific as possible (PC vs Mac, what browser, etc",
    )
    technical_issues_description = models.TextField(
        blank=True, help_text="**If Yes:** Please describe the issue"
    )

    instructions_overall = models.BooleanField(
        default=True, help_text="Were the instructions clear?"
    )
    instructions_feedback = models.TextField(
        blank=True,
        help_text="**If No:** What was confusing about the instructions?",
    )

    FLAVORTEXT_CHOICES = (
        ("helpful", "It was helpful and appropriate"),
        ("too_leading", "It was too leading"),
        ("not_helpful", "It was not helpful"),
        ("confused", "It confused us, or led us in a wrong direction"),
        ("none_but_ok", "There was no flavor text, and that was fine"),
        ("none_not_ok", "There was no flavor text, and I would have liked some"),
    )

    flavortext_overall = models.CharField(
        max_length=20,
        help_text="Which best describes the flavor text?",
        choices=FLAVORTEXT_CHOICES,
    )
    flavortext_feedback = models.TextField(
        blank=True, help_text="**If Helpful:** How did the flavor text help?"
    )

    stuck_overall = models.BooleanField(
        default=False,
        null=False,
        help_text="**Were you stuck at any point?** E.g. not sure how to start, not sure which data to gather, etc.",
    )
    stuck_points = models.TextField(
        blank=True,
        help_text="**If Yes:** Where did you get stuck? List as many places as relevant.",
    )
    stuck_time = models.FloatField(
        null=True,
        blank=True,
        help_text="**If Yes:** For about how long were you stuck?",
    )
    stuck_unstuck = models.TextField(
        blank=True,
        help_text="**If Yes:** What helped you get unstuck? Was it a satisfying aha?",
    )

    errors_found = models.TextField(
        blank=True,
        help_text="What errors, if any, did you notice in the puzzle?",
    )

    suggestions_change = models.TextField(
        blank=True,
        help_text="Do you have suggestions to change the puzzle? Please explain why your suggestion(s) will help.",
    )

    suggestions_keep = models.TextField(
        blank=True,
        help_text="Do you have suggestions for things that should definitely stay in the puzzle? Please explain what you like about them.",
    )

    def __str__(self):
        return f"Testsolve participation: {self.user.username} in Session #{self.session.id}"


@receiver(post_save, sender=TestsolveParticipation)
def add_testsolver_to_thread(
    sender, instance: TestsolveParticipation, created: bool, **kwargs
):
    if instance.in_discord_thread:
        return
    c = discord.get_client()
    if c:
        session = instance.session
        if instance.user.discord_user_id:
            c.add_member_to_thread(
                session.discord_thread_id, instance.user.discord_user_id
            )
            instance.in_discord_thread = True
            instance.save()


class TestsolveGuess(models.Model):
    """A guess made by a user in a testsolve session."""

    session = models.ForeignKey(
        TestsolveSession, on_delete=models.CASCADE, related_name="guesses"
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="guesses")
    guess = models.TextField(max_length=500, blank=True)
    correct = models.BooleanField()
    partially_correct = models.BooleanField(default=False)
    partial_response = models.TextField(blank=True)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "testsolve guesses"

    def __str__(self):
        correct_text = "Correct" if self.correct else "Incorrect"
        return f"{self.guess}: {correct_text} guess by {self.user.username} in Session #{self.session.id}"


def is_spoiled_on(user: User, puzzle: Puzzle) -> bool:
    # should use prefetch_related("spoiled") when using this
    return user.is_eic or user in puzzle.spoiled.all()


def is_author_on(user: User, puzzle: Puzzle) -> bool:
    return user in puzzle.authors.all()


def is_editor_on(user: User, puzzle: Puzzle) -> bool:
    return user in puzzle.editors.all()


def is_factchecker_on(user: User, puzzle: Puzzle) -> bool:
    return user in puzzle.factcheckers.all()


def is_postprodder_on(user: User, puzzle: Puzzle) -> bool:
    return user in puzzle.postprodders.all()


def get_user_role(user: User, puzzle: Puzzle) -> str | None:
    if is_author_on(user, puzzle):
        return "author"
    elif is_editor_on(user, puzzle):
        return "editor"
    elif is_postprodder_on(user, puzzle):
        return "postprodder"
    elif is_factchecker_on(user, puzzle):
        return "factchecker"
    else:
        return None


class Hint(models.Model):
    puzzle = models.ForeignKey(Puzzle, on_delete=models.PROTECT, related_name="hints")
    order = models.FloatField(
        blank=False,
        null=False,
        help_text="Order in the puzzle - use 0 for a hint at the very beginning of the puzzle, or 100 for a hint on extraction, and then do your best to extrapolate in between. Decimals are okay. For multiple subpuzzles, assign a whole number to each subpuzzle and use decimals off of that whole number for multiple hints in the subpuzzle.",
    )
    description = models.CharField(
        max_length=1000,
        blank=False,
        null=False,
        help_text='A description of when this hint should apply; e.g. "Solvers have done X and currently have..."',
    )
    keywords = models.CharField(
        max_length=100,
        blank=True,
        null=False,
        help_text="Comma-separated keywords to look for in hunters' hint requests before displaying this hint suggestion",
    )
    content = models.CharField(
        max_length=1000,
        blank=False,
        null=False,
        help_text="Canned hint to give a team (can be edited by us before giving it)",
    )

    class Meta:
        unique_together = ("puzzle", "description")
        ordering = ("order",)

    def __str__(self):
        return f"Hint #{self.order} for {self.puzzle}"

    def get_absolute_url(self):
        return urls.reverse("edit_hint", kwargs={"id": self.id})

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

    @classmethod
    def get_bool_setting(cls, key):
        try:
            return cls.objects.get(key=key).value.lower() == "true"
        except cls.DoesNotExist:
            return False
        except ValueError:
            return False


class DiscordCategoryCache(models.Model):
    """Cache of Discord categories, maintained by the discord_daemon task"""

    _CATEGORY_STATUS_RE = "|".join(
        [re.escape(status.get_display(s)) for s in status.STATUSES]
    )
    _CATEGORY_RE = re.compile(
        rf"^{settings.DISCORD_CATEGORY_PREFIX or ""}(?P<description>{_CATEGORY_STATUS_RE})(-(?P<num>\d+))?$"
    )

    id = models.CharField(primary_key=True, max_length=20)  # Discord snowflake ID
    name = models.CharField(max_length=100)
    position = models.IntegerField()
    puzzle_status = models.CharField(max_length=status.MAX_LENGTH, blank=True)
    puzzle_status_index = models.IntegerField(blank=True, null=True)

    def __str__(self):
        return f"{self.id} ({self.name})"

    def save(self, *args, **kwargs):
        if match := DiscordCategoryCache._CATEGORY_RE.match(self.name):
            puzzle_status = None
            for s, d in status.DESCRIPTIONS.items():
                if d == match.group("description"):
                    puzzle_status = s
                    break
            if puzzle_status:
                self.puzzle_status = puzzle_status
                num = match.group("num")
                self.puzzle_status_index = int(num or 0)
        super().save(*args, **kwargs)


class DiscordTextChannelCache(models.Model):
    """Cache of Discord channels, maintained by the discord_daemon task"""

    id = models.CharField(primary_key=True, max_length=20)  # Discord snowflake ID
    name = models.CharField(max_length=100)
    topic = models.CharField(max_length=1024)
    position = models.IntegerField()
    category = models.ForeignKey(
        DiscordCategoryCache,
        on_delete=models.SET_NULL,
        null=True,
        related_name="text_channels",
        db_constraint=False,
    )
    permission_overwrites = models.JSONField()

    def __str__(self):
        return f"{self.id} ({self.name})"

    @property
    def url(self):
        return f"https://discord.com/channels/{settings.DISCORD_GUILD_ID}/{self.id}"


class FileUpload(models.Model):
    """Uploads of ZIP files"""

    bucket = models.CharField(max_length=100)
    prefix = models.CharField(max_length=100)
    filename = models.CharField(max_length=100)
    uploader = models.ForeignKey(User, on_delete=models.PROTECT)
    uploaded = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.bucket}/{self.prefix} by {self.uploader.username}"
