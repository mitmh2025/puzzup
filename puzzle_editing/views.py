import contextlib
import csv
import datetime
import json
import operator
import os
import random
import re
import traceback
import typing as t
from functools import reduce
from pathlib import Path

import dateutil.parser
import pydantic
import requests
from django import urls
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.signing import BadSignature, Signer
from django.db.models import Avg, Count, Exists, F, Max, OuterRef, Q, Subquery
from django.db.models.functions import Lower
from django.forms import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils.http import urlencode
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.views.static import serve  # noqa: F401
from github import Auth, Github

from . import discord_integration as discord
from . import google_integration as google
from . import status
from .celery import export_all_task, export_puzzle_task
from .discord import Permission as DiscordPermission
from .discord import TextChannel
from .forms import (
    AccountForm,
    AnswerForm,
    EditPostprodForm,
    GDocHtmlPreviewForm,
    GuessForm,
    PuzzleAnswersForm,
    PuzzleCommentForm,
    PuzzleContentForm,
    PuzzleHintForm,
    PuzzleInfoForm,
    PuzzleOtherCreditsForm,
    PuzzlePeopleForm,
    PuzzlePostprodForm,
    PuzzlePriorityForm,
    PuzzlePseudoAnswerForm,
    PuzzleSolutionForm,
    PuzzleTagForm,
    PuzzleTaggingForm,
    RegisterForm,
    RoundForm,
    SupportRequestAuthorNotesForm,
    SupportRequestStatusForm,
    SupportRequestTeamNotesForm,
    TestsolveFeedbackForm,
    TestsolveFinderForm,
    TestsolveParticipantPicker,
    TestsolveParticipationForm,
    TestsolveSessionInfoForm,
    TestsolveSessionNotesForm,
    UserMultipleChoiceField,
)
from .graph import aggregated_feeder_graph_b64
from .messaging import send_mail_wrapper
from .models import (
    CommentReaction,
    Hint,
    PseudoAnswer,
    Puzzle,
    PuzzleAnswer,
    PuzzleComment,
    PuzzleCredit,
    PuzzleTag,
    PuzzleVisited,
    Round,
    SiteSetting,
    SupportRequest,
    TestsolveFeedback,
    TestsolveGuess,
    TestsolveParticipation,
    TestsolveSession,
    User,
    get_user_role,
    is_author_on,
    is_discussing_on,
    is_editor_on,
    is_factchecker_on,
    is_postprodder_on,
    is_spoiled_on,
)
from .utils.postprod import export_all, export_puzzle, guess_google_doc_id
from .view_helpers import (
    AuthenticatedHttpRequest,
    external_puzzle_url,
    group_required,
)

# This file is so full of redefined-outer-name issues it swamps real problems.
# It has them because e.g. there's a fn called user() and also lots of fns with
# vars called 'user'.

# pylint: disable=redefined-outer-name
# pylint: disable=redefined-builtin


def index(request):
    announcement = SiteSetting.get_setting("ANNOUNCEMENT")

    if not request.user.is_authenticated:
        return render(request, "index_not_logged_in.html")

    request: AuthenticatedHttpRequest
    user = request.user

    blocked_on_author_puzzles = Puzzle.objects.filter(
        authors=user,
        status__in=status.STATUSES_BLOCKED_ON_AUTHORS,
    )
    blocked_on_editor_puzzles = Puzzle.objects.filter(
        editors=user,
        status__in=status.STATUSES_BLOCKED_ON_EDITORS,
    )
    current_sessions = TestsolveSession.objects.filter(
        participations__in=TestsolveParticipation.objects.filter(
            user=request.user,
            ended__isnull=True,
            session__is_open=True,
        ).all()
    )

    currently_in_factchecking = Puzzle.objects.filter(
        Q(status=status.NEEDS_TESTSOLVE_FACTCHECK)
        | Q(status=status.NEEDS_FACTCHECK)
        | Q(status=status.NEEDS_COPY_EDITS)
    )

    factchecking = currently_in_factchecking.filter(
        Q(factcheckers=request.user) | Q(quickcheckers=request.user)
    )

    discussing = Puzzle.objects.filter(
        Q(discussion_editors=request.user)
        & (
            Q(status=status.INITIAL_IDEA)
            | Q(status=status.AWAITING_APPROVAL)
            | Q(status=status.NEEDS_DISCUSSION)
            | Q(status=status.AWAITING_ANSWER)
            | Q(status=status.IDEA_IN_DEVELOPMENT)
            | Q(status=status.WRITING)
            | Q(status=status.AWAITING_TESTSOLVE_REVIEW)
        )
    )
    supporting = Puzzle.objects.filter(
        Q(support_requests__assignees=request.user)
        & Q(
            support_requests__status__in=[
                SupportRequest.Status.APPROVED,
                SupportRequest.Status.BLOCK,
            ]
        )
    )

    postprodding = Puzzle.objects.filter(
        status=status.NEEDS_POSTPROD, postprodders=user
    )
    inbox_puzzles = (
        user.spoiled_puzzles.exclude(status=status.DEAD)
        .annotate(
            last_comment_date=Max("comments__date"),
            last_visited_date=Subquery(
                PuzzleVisited.objects.filter(puzzle=OuterRef("pk"), user=user).values(
                    "date"
                )
            ),
        )
        .filter(
            Q(last_visited_date__isnull=True)
            | Q(last_comment_date__gt=F("last_visited_date"))
        )
    )

    return render(
        request,
        "index.html",
        {
            "announcement": announcement,
            "blocked_on_author_puzzles": blocked_on_author_puzzles,
            "blocked_on_editor_puzzles": blocked_on_editor_puzzles,
            "current_sessions": current_sessions,
            "discussing": discussing,
            "factchecking": factchecking,
            "inbox_puzzles": inbox_puzzles,
            "supporting": supporting,
            "postprodding": postprodding,
        },
    )


@login_required
def docs(request: AuthenticatedHttpRequest):
    return render(request, "docs.html", {})


@login_required
def process(request: AuthenticatedHttpRequest):
    return render(request, "process.html", {})


def register(request: AuthenticatedHttpRequest):
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect(urls.reverse("index"))
        else:
            return render(request, "register.html", {"form": form})
    else:
        form = RegisterForm()
        return render(request, "register.html", {"form": form})


@login_required
def account(request: AuthenticatedHttpRequest):
    user = request.user
    if request.method == "POST":
        form = AccountForm(request.POST)
        if form.is_valid():
            user.email = form.cleaned_data["email"]
            user.bio = form.cleaned_data["bio"]
            user.credits_name = form.cleaned_data["credits_name"]
            user.github_username = form.cleaned_data["github_username"]
            user.enable_keyboard_shortcuts = form.cleaned_data["keyboard_shortcuts"]
            user.save()
            return render(request, "account.html", {"form": form, "success": True})
        else:
            return render(request, "account.html", {"form": form, "success": None})
    else:
        form = AccountForm(
            initial={
                "email": user.email,
                "credits_name": user.credits_name or user.username,
                "bio": user.bio,
                "github_username": user.github_username,
                "keyboard_shortcuts": user.enable_keyboard_shortcuts,
            }
        )
        return render(request, "account.html", {"form": form, "success": None})


# List of timezones that should cover all common use cases
common_timezones = [
    ("Midway Atoll (UTC-11)", "Pacific/Midway"),
    ("US/Hawaii (UTC-10)", "US/Hawaii"),
    ("US/Alaska (UTC-9)", "US/Alaska"),
    ("US/Pacific (UTC-8)", "US/Pacific"),
    ("US/Mountain (UTC-7)", "US/Mountain"),
    ("US/Central (UTC-6)", "US/Central"),
    ("US/Eastern (UTC-5)", "US/Eastern"),
    ("America/Puerto_Rico (UTC-4)", "America/Puerto_Rico"),
    ("America/Argentina/Buenos_Aires (UTC-3)", "America/Argentina/Buenos_Aires"),
    ("Atlantic/Cape_Verde (UTC-1)", "Atlantic/Cape_Verde"),
    ("Europe/London (UTC+0)", "Europe/London"),
    ("Europe/Paris (UTC+1)", "Europe/Paris"),
    ("Asia/Jerusalem (UTC+2)", "Asia/Jerusalem"),
    ("Africa/Addis_Ababa (UTC+3)", "Africa/Addis_Ababa"),
    ("Asia/Dubai (UTC+4)", "Asia/Dubai"),
    ("Asia/Karachi (UTC+5)", "Asia/Karachi"),
    ("India Standard Time (UTC+5.5)", "Asia/Kolkata"),
    ("Asia/Dhaka (UTC+6)", "Asia/Dhaka"),
    ("Asia/Bangkok (UTC+7)", "Asia/Bangkok"),
    ("Asia/Singapore (UTC+8)", "Asia/Singapore"),
    ("Asia/Tokyo (UTC+9)", "Asia/Tokyo"),
    ("Australia/Adelaide (UTC+9.5)", "Australia/Adelaide"),
    ("Australia/Sydney (UTC+10)", "Australia/Sydney"),
    ("Vanuatu (UTC+11)", "Pacific/Efate"),
    ("New Zealand (UTC+12)", "Pacific/Auckland"),
]


@login_required
def set_timezone(request: AuthenticatedHttpRequest):
    if request.method == "POST":
        request.session["django_timezone"] = request.POST["timezone"]
        return redirect("/account")
    else:
        return render(
            request,
            "timezone.html",
            {
                "timezones": common_timezones,
                "current": request.session.get("django_timezone", "UTC"),
            },
        )


def oauth2_link(request: HttpRequest):
    user = t.cast(User, request.user)
    if request.method == "POST":
        if user.is_anonymous:
            return redirect("/login")

        if "unlink-discord" in request.POST:
            if user.discord_user_id or user.discord_username:
                if user.avatar_url.startswith("https://cdn.discordapp.com/"):
                    user.avatar_url = ""
                user.discord_user_id = ""
                user.discord_username = ""
                user.save()
        elif "refresh-discord" in request.POST:
            member = discord.get_client().get_member_by_id(user.discord_user_id)
            if member:
                user.discord_username = member["user"]["username"]
                if member["user"].get("discriminator"):
                    user.discord_username += "#" + member["user"]["discriminator"]
                user.discord_nickname = member["nick"] or ""
                user.save()
                user.avatar_url = user.get_avatar_url_via_discord(
                    member["user"]["avatar"]
                )
                user.save()

        return redirect("/account")

    if "code" in request.GET or "error" in request.GET:
        # Verify that state parameter has been signed by the server.
        verified = False
        if "state" in request.GET and "discord_state" in request.session:
            signer = Signer()
            try:
                state = signer.unsign_object(request.GET["state"])  # type: ignore
                verified = True
            except BadSignature:
                return redirect("/login")

        if not verified:
            return redirect("/login")

        del request.session["discord_state"]

        if "error" in request.GET:
            return render(request, "account.html")
        elif "code" in request.GET:
            post_payload = {
                "client_id": settings.DISCORD_CLIENT_ID,
                "client_secret": settings.DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": request.GET["code"],
                "redirect_uri": request.build_absolute_uri(urls.reverse("oauth2_link")),
                "scope": settings.DISCORD_OAUTH_SCOPES,
            }

            heads = {"Content-Type": "application/x-www-form-urlencoded"}

            r = requests.post(
                "https://discord.com/api/oauth2/token", data=post_payload, headers=heads
            )

            response = r.json()
            user_headers = {
                "Authorization": "Bearer {}".format(response["access_token"])
            }
            user_info = requests.get(
                "https://discord.com/api/v8/users/@me", headers=user_headers
            )

            user_data = user_info.json()

            # If user is not logged in, find matching user or create.
            if user.is_anonymous and "id" in user_data:
                try:
                    linked_user = User.objects.filter(
                        discord_user_id=user_data["id"]
                    ).first()
                    if linked_user is None:
                        # ewwwwwwwwwwwww
                        User.objects.get(discord_user_id=user_data["id"])
                    login(request, linked_user)
                    user = linked_user
                except User.DoesNotExist:
                    # Check that user belongs to Discord guild in settings.
                    user_check = requests.get(
                        f"https://discord.com/api/v8/guilds/{settings.DISCORD_GUILD_ID}/members/{user_data['id']}",
                        headers={"Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}"},
                    )

                    if user_check.status_code != 200:
                        return redirect("/login")

                    discord_username = "{}#{}".format(
                        user_data["username"], user_data["discriminator"]
                    )
                    linked_user = User.objects.create(
                        username=discord_username,
                        email=user_data["email"],
                    )
                    login(request, linked_user)
                    user = t.cast(User, request.user)

            user.discord_user_id = user_data["id"]

            user.discord_username = "{}#{}".format(
                user_data["username"], user_data["discriminator"]
            )

            if discord.enabled():
                c = discord.get_client()
                discord.init_perms(c, user)
                member = c.get_member_by_id(user.discord_user_id)
                if member:
                    user.discord_nickname = member["nick"] or member["user"]["username"]
                    if not user.credits_name:
                        user.credits_name = user.discord_nickname
                    user.avatar_url = (
                        user.get_avatar_url_via_discord(member["user"]["avatar"] or "")
                        or ""
                    )

            user.save()

        return redirect("/account")

    if not user.is_anonymous and user.discord_user_id:
        return redirect("/account")
    else:
        signer = Signer()
        state = signer.sign_object(
            {"dt": datetime.datetime.utcnow().isoformat()}
        )  # type: ignore
        request.session["discord_state"] = state

        params = {
            "response_type": "code",
            "client_id": settings.DISCORD_CLIENT_ID,
            "state": state,
            "scope": settings.DISCORD_OAUTH_SCOPES,
            "prompt": "none",
            "redirect_uri": request.build_absolute_uri(urls.reverse("oauth2_link")),
        }

        oauth_url = "https://discord.com/api/oauth2/authorize?" + urlencode(params)
        return redirect(oauth_url)


@login_required
def puzzle_new(request: AuthenticatedHttpRequest):
    user: User = request.user

    if request.method == "POST":
        form = PuzzleInfoForm(user, request.POST)
        if form.is_valid():
            puzzle: Puzzle = form.save(commit=False)
            puzzle.status_mtime = datetime.datetime.now()
            puzzle.save()
            form.save_m2m()
            puzzle.spoiled.add(*puzzle.authors.all())
            if c := discord.get_client():
                url = external_puzzle_url(request, puzzle)
                tc = None
                if puzzle.discord_channel_id:
                    # if you put in an invalid discord ID, we just ignore it
                    # and create a new channel for you.
                    tc = discord.get_channel(c, puzzle)
                tc = (
                    discord.build_puzzle_channel(url, puzzle, c.guild_id)
                    if tc is None
                    else discord.sync_puzzle_channel(puzzle, tc, url=url)
                )
                tc.make_private()
                author_tags = discord.get_tags(puzzle.authors.all(), False)
                cat = status.get_display(puzzle.status)
                tc = c.save_channel_to_cat(tc, cat)
                puzzle.discord_channel_id = tc.id
                puzzle.save()

                puzzle.post_message_to_channel(
                    f"This puzzle has been created in status **{cat}**!\n"
                    f"Access it at {url}\n"
                    f"Author(s): {', '.join(author_tags)}"
                )
            puzzle.add_comment(
                request=request,
                author=user,
                is_system=True,
                send_email=False,
                content="Created puzzle",
                status_change="II",
            )

            return redirect(urls.reverse("authored"))
        else:
            return render(request, "new.html", {"form": form})
    else:
        form = PuzzleInfoForm(request.user)
        return render(request, "new.html", {"form": form})


@login_required
def all_answers(request: AuthenticatedHttpRequest):
    user = request.user
    if request.method == "POST":
        if "spoil_on" in request.POST:
            get_object_or_404(Round, id=request.POST["spoil_on"]).spoiled.add(user)
        return redirect(urls.reverse("all_answers"))
    rounds = [
        {
            "id": round.id,
            "name": round.name,
            "description": round.description,
            "spoiled": round.spoiled.filter(id=user.id).exists(),
            "answers": [
                {
                    "answer": answer.answer,
                    "id": answer.id,
                    "notes": answer.notes,
                    "puzzles": answer.puzzles.all(),
                }
                for answer in round.answers.all().order_by(Lower("answer"))
            ],
            "form": AnswerForm(round),
            "editors": round.editors.all().order_by(Lower("credits_name")),
        }
        for round in Round.objects.all()
        .prefetch_related("editors", "answers")
        .order_by(Lower("name"))
    ]

    return render(
        request,
        "all_answers.html",
        {"rounds": rounds},
    )


@login_required
def random_answers(request: AuthenticatedHttpRequest):
    answers = list(PuzzleAnswer.objects.filter(puzzles__isnull=True))
    available = random.sample(answers, min(3, len(answers)))
    return render(request, "random_answers.html", {"answers": available})


# TODO: "authored" is now a misnomer
@login_required
def authored(request: AuthenticatedHttpRequest):
    puzzles = Puzzle.objects.filter(authors=request.user)
    editing_puzzles = Puzzle.objects.filter(editors=request.user)
    return render(
        request,
        "authored.html",
        {
            "puzzles": puzzles,
            "editing_puzzles": editing_puzzles,
        },
    )


@login_required
def all_puzzles(request: AuthenticatedHttpRequest):
    puzzles = (
        Puzzle.objects.all().prefetch_related("authors", "answers").order_by("name")
    )
    return render(request, "all.html", {"puzzles": puzzles})


@login_required
def bystatus(request: AuthenticatedHttpRequest):
    all_puzzles = Puzzle.objects.exclude(
        status__in=[
            status.INITIAL_IDEA,
            status.DEFERRED,
            status.DEAD,
        ]
    ).prefetch_related("authors", "tags")

    puzzles = []
    for puzzle in all_puzzles:
        puzzle_obj = {
            "puzzle": puzzle,
            # "authors": [a for a in puzzle.authors.all()],
            "status": (
                f"{status.get_emoji(puzzle.status)} {status.get_display(puzzle.status)}"
            ),
        }
        puzzles.append(puzzle_obj)

    # sorted_puzzles = sorted(needs_postprod, key=lambda a: (status.STATUSES.index(a.status), a.name))
    puzzles = sorted(puzzles, key=lambda x: status.get_status_rank(x["puzzle"].status))

    return render(request, "bystatus.html", {"puzzles": puzzles})
    # return render(request, "postprod_all.html", context)


class DiscordData(pydantic.BaseModel):
    """Data about a puzzle's discord channel, for display on a page."""

    # Whether discord is enabled, disabled, or supposedly enabled but we
    # couldn't fetch data
    status: t.Literal["enabled", "disabled", "broken"]
    guild_id: str = ""  # For URL generation
    channel_id: str = ""
    name: str = ""
    public: bool = False
    nvis: int = 0  # Number of people with explicit view permission
    i_can_see: bool = False  # Whether the current user has view permission
    error: str = ""

    @property
    def exists(self):
        """True iff discord is working and we have a channel_id.

        The assumption is that whoever creates this object will have checked
        whether the channel_id actually exists already, and won't set one here
        if the one we have is invalid.
        """
        return self.status == "enabled" and self.guild_id and self.channel_id

    @property
    def url(self):
        """URL for the discord channel, or None if there isn't one."""
        if not self.guild_id or not self.channel_id:
            return None
        return f"discord://discord.com/channels/{self.guild_id}/{self.channel_id}"

    @classmethod
    def from_channel(cls, tc: discord.TextChannel, me: User) -> "DiscordData":
        """Parse a TextChannel+User into a DiscordData"""
        myid = me.discord_user_id
        vis = False
        nvis = 0
        for uid, overwrite in tc.perms.users.items():
            if DiscordPermission.VIEW_CHANNEL in overwrite.allow:
                nvis += 1
                if uid == myid:
                    vis = True
        return cls(
            status="enabled",
            name=tc.name,
            guild_id=tc.guild_id,
            public=tc.is_public(),
            channel_id=tc.id,
            nvis=nvis,
            i_can_see=vis,
        )


@login_required
def all_hints(request: AuthenticatedHttpRequest):
    return render(request, "all_hints.html", {"puzzles": Puzzle.objects.all()})


@login_required
def puzzle_hints(request: AuthenticatedHttpRequest, id):
    puzzle: Puzzle = get_object_or_404(Puzzle, id=id)
    if request.method == "POST" and "add_hint" in request.POST:
        form = PuzzleHintForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect(urls.reverse("puzzle_hints", kwargs={"id": id}))

    return render(
        request,
        "puzzle_hints.html",
        {"hint_form": PuzzleHintForm(initial={"puzzle": puzzle}), "puzzle": puzzle},
    )


@login_required
def puzzle_other_credits(request: AuthenticatedHttpRequest, id):
    puzzle: Puzzle = get_object_or_404(Puzzle, id=id)
    if request.method == "POST":
        form = PuzzleOtherCreditsForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect(
                urls.reverse("puzzle_other_credits", kwargs={"id": puzzle.id})
            )
    else:
        form = PuzzleOtherCreditsForm(initial={"puzzle": puzzle})

    return render(
        request,
        "puzzle_other_credits.html",
        {"puzzle": puzzle, "other_credit_form": form},
    )


@login_required
def puzzle_other_credit_update(request: AuthenticatedHttpRequest, id, puzzle_id):
    other_credit = get_object_or_404(PuzzleCredit, id=id)
    if request.method == "POST":
        if "delete_oc" in request.POST:
            other_credit.delete()
        else:
            form = PuzzleOtherCreditsForm(request.POST, instance=other_credit)
            if form.is_valid():
                form.save()

        return redirect(urls.reverse("puzzle_other_credits", kwargs={"id": puzzle_id}))

    return render(
        request,
        "puzzle_other_credit_edit.html",
        {
            "other_credit": other_credit,
            "puzzle": other_credit.puzzle,
            "other_credit_form": PuzzleOtherCreditsForm(instance=other_credit),
        },
    )


@login_required
def puzzle(request: AuthenticatedHttpRequest, id, slug=None):
    puzzle: Puzzle = get_object_or_404(Puzzle, id=id)
    if slug is None:
        new_slug = puzzle.slug
        if new_slug:
            return redirect(
                urls.reverse("puzzle_w_slug", kwargs={"id": id, "slug": new_slug})
            )

    user: User = request.user

    vis, vis_created = PuzzleVisited.objects.get_or_create(puzzle=puzzle, user=user)
    if not vis_created:
        # update the auto_now=True DateTimeField anyway
        vis.save()

    def add_system_comment_here(message, status_change=""):
        puzzle.add_comment(
            request=request,
            author=user,
            is_system=True,
            send_email=False,
            content=message,
            status_change=status_change,
        )

    if request.method == "POST":
        c: discord.Client | None = None
        ch: TextChannel | None = None
        our_d_id: str = user.discord_user_id
        disc_ops = {
            "subscribe-me",
            "unsubscribe-me",
            "discord-public",
            "discord-private",
            "resync-discord",
            "delete-channel",
        }
        if discord.enabled():
            # Preload the discord client and current channel data.
            c, ch = discord.get_client_and_channel(puzzle)
            if c and puzzle.discord_channel_id and not ch:
                # If the puzzle has a channel_id but it doesn't exist, clear it
                # here to save time in the future.
                puzzle.discord_channel_id = ""
                puzzle.save()
        if "do_spoil" in request.POST:
            puzzle.spoiled.add(user)
        elif set(request.POST) & disc_ops:
            if c and ch:
                newcat = None
                if "subscribe-me" in request.POST:
                    ch.add_visibility([our_d_id] if our_d_id else ())
                elif "unsubscribe-me" in request.POST:
                    ch.rm_visibility([our_d_id] if our_d_id else ())
                elif "discord-public" in request.POST:
                    ch.make_public()
                elif "discord-private" in request.POST:
                    ch.make_private()
                elif "resync-discord" in request.POST:
                    # full resync of all attributes
                    url = external_puzzle_url(request, puzzle)
                    discord.sync_puzzle_channel(puzzle, ch, url)
                    newcat = status.get_display(puzzle.status)
                if newcat is not None:
                    c.save_channel_to_cat(ch, newcat)
                else:
                    c.save_channel(ch)

                if "delete-channel" in request.POST:
                    c.delete_channel(ch.id)
                    puzzle.discord_channel_id = ""
                    puzzle.save()
            else:
                return HttpResponseBadRequest("<b>Discord is not enabled.</b>")
        elif "link-discord" in request.POST:
            if not c:
                return HttpResponseBadRequest("<b>Discord is not enabled.</b>")
            if ch is None:
                url = external_puzzle_url(request, puzzle)
                tc = discord.build_puzzle_channel(url, puzzle, c.guild_id)
                cat = status.get_display(puzzle.status)
                tc = c.save_channel_to_cat(tc, cat)
                puzzle.discord_channel_id = tc.id
                puzzle.save()
                author_tags = discord.get_tags(puzzle.authors.all(), False)
                editor_tags = discord.get_tags(puzzle.editors.all(), False)
                msg_chunks = [
                    f"This channel was just created for puzzle {puzzle.name}!",
                    f"Access it at {url}",
                ]
                if author_tags:
                    msg_chunks.append(f"Author(s): {', '.join(author_tags)}")
                if editor_tags:
                    msg_chunks.append(f"Editor(s): {', '.join(editor_tags)}")
                c.post_message(tc.id, "\n".join(msg_chunks))
        elif "change_status" in request.POST:
            puzzle.set_status(request, request.POST["change_status"])

        elif "change_priority" in request.POST:
            form = PuzzlePriorityForm(request.POST, instance=puzzle)
            if form.is_valid():
                form.save()
                add_system_comment_here(
                    "Priority changed to " + puzzle.get_priority_display()
                )
        elif "add_author" in request.POST:
            puzzle.authors.add(user)
            puzzle.spoiled.add(user)
            if c and ch:
                discord.sync_puzzle_channel(puzzle, ch)
                c.save_channel(ch)
                # discord.announce_ppl(c, ch, spoiled=[user])
            add_system_comment_here("Added author " + str(user))
        elif "remove_author" in request.POST:
            puzzle.authors.remove(user)
            add_system_comment_here("Removed author " + str(user))
        elif "add_editor" in request.POST:
            puzzle.editors.add(user)
            puzzle.spoiled.add(user)
            if c and ch:
                discord.sync_puzzle_channel(puzzle, ch)
                c.save_channel(ch)
                discord.announce_ppl(c, ch, editors=[user])
            add_system_comment_here("Added editor " + str(user))
        elif "remove_editor" in request.POST:
            puzzle.editors.remove(user)
            add_system_comment_here("Removed editor " + str(user))
        elif "add_quickchecker" in request.POST:
            puzzle.quickcheckers.add(user)
            add_system_comment_here("Added quickchecker " + str(user))
        elif "remove_quickchecker" in request.POST:
            puzzle.quickcheckers.remove(user)
            add_system_comment_here("Removed quickchecker " + str(user))
        elif "add_factchecker" in request.POST:
            puzzle.factcheckers.add(user)
            add_system_comment_here("Added factchecker " + str(user))
        elif "remove_factchecker" in request.POST:
            puzzle.factcheckers.remove(user)
            add_system_comment_here("Removed factchecker " + str(user))
        elif "add_postprodder" in request.POST:
            puzzle.postprodders.add(user)
            add_system_comment_here("Added postprodder " + str(user))
        elif "remove_postprodder" in request.POST:
            puzzle.postprodders.remove(user)
            add_system_comment_here("Removed postprodder " + str(user))
        elif "edit_content" in request.POST:
            content_form = PuzzleContentForm(request.POST, instance=puzzle)
            if content_form.is_valid():
                content_form.save()
                add_system_comment_here("Edited puzzle content")
        elif "edit_solution" in request.POST:
            soln_form = PuzzleSolutionForm(request.POST, instance=puzzle)
            if soln_form.is_valid():
                soln_form.save()
                add_system_comment_here("Edited puzzle solution")
        elif "edit_postprod" in request.POST:
            form = EditPostprodForm(request.POST, instance=puzzle.postprod)
            if form.is_valid():
                form.save()
                add_system_comment_here("Edited puzzle postprod host url")
        elif "add_pseudo_answer" in request.POST:
            pseudo_ans_form = PuzzlePseudoAnswerForm(request.POST)
            if pseudo_ans_form.is_valid():
                pseudo_ans_form.save()
                add_system_comment_here("Added partial answer")
        elif "add_hint" in request.POST:
            hint_form = PuzzleHintForm(request.POST)
            if hint_form.is_valid():
                hint_form.save()
                add_system_comment_here("Added hint")
                return redirect(urls.reverse("puzzle_hints", args=[puzzle.id]))
        elif (
            "add_comment" in request.POST or "add_comment_change_status" in request.POST
        ):
            comment_form = PuzzleCommentForm(request.POST)
            # Not worth crashing over. Just do our best.
            status_change_dirty = request.POST.get("add_comment_change_status")
            status_change = ""
            if (
                status_change_dirty
                and status_change_dirty in status.BLOCKERS_AND_TRANSITIONS
            ):
                status_change = status_change_dirty

            if status_change and puzzle.status != status_change:
                puzzle.set_status(request, status_change, make_comment=False)

            if comment_form.is_valid():
                puzzle.add_comment(
                    request=request,
                    author=user,
                    is_system=False,
                    send_email=True,
                    content=comment_form.cleaned_data["content"],
                    status_change=status_change,
                )

        elif "react_comment" in request.POST:
            emoji = request.POST.get("emoji")
            comment = PuzzleComment.objects.get(id=request.POST["react_comment"])
            # This just lets you react with any string to a comment, but it's
            # not the end of the world.
            if emoji and comment:
                CommentReaction.toggle(emoji, comment, user)
        # refresh
        return redirect(urls.reverse("puzzle", args=[id]))

    # TODO: participants is still hitting the database once per session;
    # might be possible to craft a Prefetch to get the list of
    # participants; or maybe we can abstract out the handrolled user list
    # logic and combine with the other views that do this

    # I inspected the query and Count with filter does become a SUM of CASE
    # expressions so it's using the same left join as everything else,
    # correctly for what we want
    testsolve_sessions = TestsolveSession.objects.filter(puzzle=puzzle).annotate(
        has_correct=Exists(
            TestsolveGuess.objects.filter(session=OuterRef("pk"), correct=True)
        ),
        participation_count=Count("participations"),
        participation_done_count=Count(
            "participations", filter=Q(participations__ended__isnull=False)
        ),
        avg_diff=Avg("participations__difficulty_rating"),
        avg_fun=Avg("participations__fun_rating"),
        avg_hours=Avg("participations__hours_spent"),
    )

    if is_spoiled_on(user, puzzle):
        discdata = DiscordData(status="disabled")
        if discord.enabled():
            discdata.status = "enabled"
            c = discord.get_client()
            try:
                ch = discord.get_channel(c, puzzle)
                if ch:
                    discdata = DiscordData.from_channel(ch, user)
            except Exception:
                discdata.status = "broken"
                discdata.guild_id = c.guild_id
                discdata.channel_id = puzzle.discord_channel_id
                discdata.error = traceback.format_exc()

        comments = PuzzleComment.objects.filter(puzzle=puzzle)
        unread_puzzles = user.spoiled_puzzles.annotate(
            last_comment_date=Max("comments__date"),
            last_visited_date=Subquery(
                PuzzleVisited.objects.filter(puzzle=OuterRef("pk"), user=user).values(
                    "date"
                )
            ),
        ).filter(
            Q(last_visited_date__isnull=True)
            | Q(last_comment_date__gt=F("last_visited_date"))
        )
        requests = (
            SupportRequest.objects.filter(puzzle=puzzle)
            .filter(Q(status="REQ") | Q(status="APP"))
            .all()
        )

        is_author = is_author_on(user, puzzle)
        is_editor = is_editor_on(user, puzzle)
        is_discussing = is_discussing_on(user, puzzle)
        can_manage_discord = is_author or is_editor or user.is_eic

        unspoiled_users = (
            User.objects.exclude(pk__in=puzzle.spoiled.all())
            .filter(is_active=True)
            .annotate(testsolve_count=Count("testsolve_participations"))
            .order_by("testsolve_count")
        )
        unspoiled = [u.credits_name or u.username for u in unspoiled_users]
        unspoiled_emails = "; ".join(
            [
                f'"{u.credits_name or u.username}" <{u.email}>'
                for u in unspoiled_users
                if u.email
            ]
        )
        unspoiled.reverse()

        start_testsolve = user.is_superuser or user.is_testsolve_coordinator

        transitions = []
        if puzzle.is_unblockable_by(user):
            transitions = puzzle.get_transitions(user)

        return render(
            request,
            "puzzle.html",
            {
                "puzzle": puzzle,
                "discord": discdata,
                "support_requests": requests,
                "comments": comments,
                "comment_form": PuzzleCommentForm(),
                "testsolve_sessions": testsolve_sessions,
                "all_statuses": status.ALL_STATUSES,
                "is_author": is_author,
                "is_editor": is_editor,
                "is_discussing": is_discussing,
                "can_manage_discord": can_manage_discord,
                "is_factchecker": is_factchecker_on(user, puzzle),
                "is_in_testsolving": puzzle.status == status.TESTSOLVING,
                "user_can_start_testsolve": start_testsolve,
                "is_postprodder": is_postprodder_on(user, puzzle),
                "postprod_form": (
                    EditPostprodForm(instance=puzzle.postprod)
                    if puzzle.has_postprod()
                    else None
                ),
                "content_form": PuzzleContentForm(instance=puzzle),
                "solution_form": PuzzleSolutionForm(instance=puzzle),
                "pseudo_answer_form": PuzzlePseudoAnswerForm(
                    initial={"puzzle": puzzle}
                ),
                "priority_form": PuzzlePriorityForm(instance=puzzle),
                "hint_form": PuzzleHintForm(initial={"puzzle": puzzle}),
                "enable_keyboard_shortcuts": user.enable_keyboard_shortcuts,
                "next_unread_puzzle_id": (
                    unread_puzzles[0].id if unread_puzzles.count() else None
                ),
                "disable_postprod": SiteSetting.get_setting("DISABLE_POSTPROD"),
                "unspoiled": unspoiled,
                "unspoiled_emails": unspoiled_emails,
                "transitions": transitions,
            },
        )
    else:
        return render(
            request,
            "puzzle_unspoiled.html",
            {
                "puzzle": puzzle,
                "role": get_user_role(user, puzzle),
                "testsolve_sessions": testsolve_sessions,
                "is_in_testsolving": puzzle.status == status.TESTSOLVING,
                "status": status.get_display(puzzle.status),
                "maybe_postproddable": (
                    status.get_status_rank(puzzle.status)
                    >= status.get_status_rank(status.NEEDS_POSTPROD)
                ) and status not in {status.DEFERRED, status.DEAD},
                "postprod_url": settings.POSTPROD_URL,
            },
        )


@login_required
def puzzle_archive_messages(request: AuthenticatedHttpRequest, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    c, ch = discord.get_client_and_channel(puzzle)

    if not c or not ch:
        warning = f"Puzzle {id} does not have a discord channel."
        messages.warning(request, warning)
        return redirect(urls.reverse("puzzle", args=[id]))

    msgs = c.get_channel_messages(ch.id, max_total=None)
    nicks = {
        u["user"]["id"]: u["nick"] or u["user"]["global_name"] or u["user"]["username"]
        for u in c.get_members_in_guild()
    }

    comment_lines = []
    for msg in msgs:
        timestamp = dateutil.parser.parse(msg["timestamp"]).strftime(
            "%a, %b%e, %Y%l:%M %p"
        )

        line = f"{nicks[msg['author']['id']]} ({timestamp}): {msg['content']}"
        comment_lines.append(line)

    puzzle.add_comment(
        author=request.user,
        is_system=True,
        content="\n".join(comment_lines),
        send_email=False,
    )

    return redirect(urls.reverse("puzzle", args=[id]))


@login_required
def puzzle_answers(request: AuthenticatedHttpRequest, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    user = request.user
    spoiled = is_spoiled_on(user, puzzle)

    if request.method == "POST":
        form = PuzzleAnswersForm(user, request.POST, instance=puzzle)
        if form.is_valid():
            form.save()

            answers = form.cleaned_data["answers"]
            comment = (
                (
                    "Assigned answer " + answers[0].answer
                    if len(answers) == 1
                    else "Assigned answers "
                    + ", ".join(answer.answer for answer in answers)
                )
                if answers
                else "Unassigned answer"
            )

            if answers and puzzle.is_meta:
                puzzle_round_ids = (
                    answers.values("round")
                    .distinct()
                    .values_list("round_id", flat=True)
                )
                user_spoiled_ids = puzzle.spoiled.values_list("id", flat=True)
                Round.spoiled.through.objects.bulk_create(
                    [
                        Round.spoiled.through(round_id=r_id, user_id=u_id)
                        for r_id in puzzle_round_ids
                        for u_id in user_spoiled_ids
                    ],
                    ignore_conflicts=True,
                )

            puzzle.add_comment(
                request=request,
                author=user,
                is_system=True,
                send_email=False,
                content=comment,
            )

            return redirect(urls.reverse("puzzle", args=[id]))

    unspoiled_rounds = Round.objects.exclude(spoiled=user).count()
    unspoiled_answers = PuzzleAnswer.objects.exclude(round__spoiled=user).count()

    return render(
        request,
        "puzzle_answers.html",
        {
            "puzzle": puzzle,
            "form": PuzzleAnswersForm(user, instance=puzzle),
            "spoiled": spoiled,
            "unspoiled_rounds": unspoiled_rounds,
            "unspoiled_answers": unspoiled_answers,
        },
    )


@login_required
def puzzle_tags(request: AuthenticatedHttpRequest, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    user = request.user
    spoiled = is_spoiled_on(user, puzzle)

    if request.method == "POST":
        form = PuzzleTaggingForm(request.POST, instance=puzzle)
        if form.is_valid():
            form.save()

            tags = form.cleaned_data["tags"]
            comment = "Changed tags: " + (
                ", ".join(tag.name for tag in tags) or "(none)"
            )

            puzzle.add_comment(
                request=request,
                author=user,
                is_system=True,
                send_email=False,
                content=comment,
            )

            return redirect(urls.reverse("puzzle", args=[id]))

    return render(
        request,
        "puzzle_tags.html",
        {
            "puzzle": puzzle,
            "form": PuzzleTaggingForm(instance=puzzle),
            "spoiled": spoiled,
        },
    )


@login_required
def puzzle_postprod(request, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    user = request.user
    spoiled = is_spoiled_on(user, puzzle)

    if request.method == "POST":
        instance = puzzle.postprod if puzzle.has_postprod() else None
        form = PuzzlePostprodForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            puzzle_html = form.cleaned_data["puzzle_html"]
            solution_html = form.cleaned_data["solution_html"]
            max_image_width = form.cleaned_data["max_image_width"]
            puzzle_directory = form.cleaned_data["puzzle_directory"]
            pp = form.save(commit=False)
            if settings.POSTPROD_BRANCH_URL:
                branch_slug = re.sub(r"\W", "-", pp.branch_name())
                pp.host_url = settings.POSTPROD_BRANCH_URL.format(slug=branch_slug)
            pp.save()
            if SiteSetting.get_int_setting("ASYNC_POSTPROD"):
                export_puzzle_task.delay(
                    user.id,
                    pp.id,
                    puzzle_directory=puzzle_directory,
                    puzzle_html=puzzle_html,
                    solution_html=solution_html,
                    max_image_width=max_image_width,
                )

            else:
                branch = export_puzzle(
                    pp,
                    puzzle_directory=puzzle_directory,
                    puzzle_html=puzzle_html,
                    solution_html=solution_html,
                    max_image_width=max_image_width,
                )
                if branch:
                    messages.success(
                        request,
                        (
                            "Successfully pushed commit to"
                            f" {settings.HUNT_REPO_URL} ({branch})"
                        ),
                    )
                else:
                    messages.error(
                        request,
                        (
                            "Failed to commit new changes. Please contact @tech if this"
                            " is not expected."
                        ),
                    )
                    return redirect(urls.reverse("puzzle_postprod", args=[id]))

            puzzle.add_comment(
                request=request,
                author=user,
                is_system=True,
                send_email=False,
                content="Postprod updated.",
            )

            return redirect(urls.reverse("puzzle", args=[id]))
        else:
            return render(
                request,
                "puzzle_postprod.html",
                {
                    "puzzle": puzzle,
                    "postprod_url": puzzle.postprod_url,
                    "form": form,
                    "spoiled": spoiled,
                },
            )

    elif puzzle.has_postprod():
        form = PuzzlePostprodForm(instance=puzzle.postprod)
    else:
        default_slug = slugify(puzzle.name.lower())
        authors = [user.credits_name for user in puzzle.authors.all()]
        authors.sort(key=lambda a: a.upper())
        form = PuzzlePostprodForm(
            initial={
                "puzzle": puzzle,
                "slug": default_slug,
                "authors": ", ".join(authors),
            }
        )

    return render(
        request,
        "puzzle_postprod.html",
        {
            "puzzle": puzzle,
            "postprod_url": puzzle.postprod_url,
            "form": form,
            "spoiled": spoiled,
        },
    )


@login_required
def puzzle_postprod_metadata(request: AuthenticatedHttpRequest, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    authors = [u.safe_credits_name for u in puzzle.authors.all()]
    authors.sort(key=lambda a: a.upper())

    metadata = JsonResponse(puzzle.metadata)

    metadata["Content-Disposition"] = 'attachment; filename="metadata.json"'

    return metadata


@login_required
def puzzle_yaml(request: AuthenticatedHttpRequest, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    return HttpResponse(puzzle.get_yaml_fixture(), content_type="text/plain")


@login_required
@group_required("EIC")
def export(request):
    output = ""
    if request.method == "POST" and "export" in request.POST:
        if SiteSetting.get_int_setting("ASYNC_POSTPROD"):
            export_all_task.delay(request.user.id)
            output = "Exporting all metadata in background task."
        else:
            branch_name = export_all()
            output = (
                "Successfully exported all metadata to"
                f" {settings.HUNT_REPO_URL} ({branch_name})"
                if branch_name
                else "Failed to export. Please report this issue to tech."
            )

    return render(request, "export.html", {"output": output})


@login_required
def check_metadata(request: AuthenticatedHttpRequest):
    puzzleFolder = Path(settings.HUNT_REPO_PATH, "hunt/data/puzzle")
    mismatches = []
    credits_mismatches = []
    notfound = []
    exceptions = []
    for puzzledir in os.listdir(puzzleFolder):
        datafile = Path(puzzleFolder, puzzledir, "metadata.json")
        try:
            with datafile.open() as data:
                metadata = json.load(data)
                pu_id = metadata["puzzle_idea_id"]
                slug_in_file = metadata["puzzle_slug"]
                credits_in_file = metadata["credits"]
                puzzle = Puzzle.objects.get(id=pu_id)
                metadata_credits = puzzle.metadata["credits"]

                if puzzle.postprod.slug != slug_in_file:
                    puzzle.slug_in_file = slug_in_file
                    mismatches.append(puzzle)
                if metadata_credits != credits_in_file:
                    puzzle.metadata_credits = metadata_credits
                    puzzle.credits_in_file = credits_in_file
                    credits_mismatches.append(puzzle)
        except FileNotFoundError:
            notfound.append(puzzledir)
            pass
        except Exception as e:
            exceptions.append(f"{puzzledir} - {e}")
            print(datafile, e)
            # sys.exit(1)

    return render(
        request,
        "check_metadata.html",
        {
            "mismatches": mismatches,
            "credits_mismatches": credits_mismatches,
            "notfound": notfound,
            "exceptions": exceptions,
        },
    )


@login_required
def puzzle_edit(request: AuthenticatedHttpRequest, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    user = request.user

    if request.method == "POST":
        form = PuzzleInfoForm(user, request.POST, instance=puzzle)
        if form.is_valid():
            if "authors" in form.changed_data:
                old_authors = set(puzzle.authors.all())
                new_authors = set(form.cleaned_data["authors"]) - old_authors
            else:
                new_authors = set()
            form.save()

            if form.changed_data:
                puzzle.add_comment(
                    request=request,
                    author=user,
                    is_system=True,
                    send_email=False,
                    content=get_changed_data_message(form),
                )
                if new_authors:
                    puzzle.spoiled.add(*new_authors)
                c, ch = discord.get_client_and_channel(puzzle)
                if c and ch:
                    url = external_puzzle_url(request, puzzle)
                    discord.sync_puzzle_channel(puzzle, ch, url=url)
                    c.save_channel(ch)
                    # if new_authors:
                    #     discord.announce_ppl(c, ch, spoiled=new_authors)

            return redirect(urls.reverse("puzzle", args=[id]))
    else:
        form = PuzzleInfoForm(user, instance=puzzle)

    return render(
        request,
        "puzzle_edit.html",
        {"puzzle": puzzle, "form": form, "spoiled": is_spoiled_on(user, puzzle)},
    )


def get_changed_data_message(form):
    """Given a filled-out valid form, describe what changed.

    Somewhat automagically produce a system comment message that includes all
    the updated fields and particularly lists all new users for
    `UserMultipleChoiceField`s with an "Assigned" sentence."""

    normal_fields = []
    lines = []

    for field in form.changed_data:
        if isinstance(form.fields[field], UserMultipleChoiceField):
            users = form.cleaned_data[field]
            field_name = field.replace("_", " ")
            if users:
                user_display = ", ".join(str(u) for u in users)
                # XXX haxx
                if len(users) == 1 and field_name.endswith("s"):
                    field_name = field_name[:-1]
                lines.append(f"Assigned {user_display} as {field_name}")
            else:
                lines.append("Unassigned all {}".format(field.replace("_", " ")))

        else:
            normal_fields.append(field)

    if normal_fields:
        lines.insert(0, "Updated {}".format(", ".join(normal_fields)))

    return "<br/>".join(lines)


@login_required
def puzzle_people(request: AuthenticatedHttpRequest, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    user = request.user

    if request.method == "POST":
        form = PuzzlePeopleForm(request.POST, instance=puzzle)
        if form.is_valid():
            changed = set()
            old = {}
            added = {}
            if form.changed_data:
                for key in ["authors", "spoiled", "editors"]:
                    old[key] = set(getattr(puzzle, key).all())
                    new = set(form.cleaned_data[key])
                    added[key] = new - old[key]
                    if new != old[key]:
                        changed.add(key)
            form.save()
            if changed and discord.enabled():
                c, ch = discord.get_client_and_channel(puzzle)
                if c and ch:
                    discord.sync_puzzle_channel(puzzle, ch)
                    c.save_channel(ch)
                    discord.announce_ppl(
                        c,
                        ch,
                        editors=added.get("editors", set()),
                        # spoiled=added.get('spoiled', set())
                    )

            if form.changed_data:
                puzzle.add_comment(
                    request=request,
                    author=user,
                    is_system=True,
                    send_email=False,
                    content=get_changed_data_message(form),
                )

            return redirect(urls.reverse("puzzle", args=[id]))
        else:
            context = {
                "puzzle": puzzle,
                "form": form,
            }
    else:
        context = {
            "puzzle": puzzle,
            "form": PuzzlePeopleForm(instance=puzzle),
        }

    return render(request, "puzzle_people.html", context)


@login_required
def puzzle_escape(request: AuthenticatedHttpRequest, id):
    puzzle: Puzzle = get_object_or_404(Puzzle, id=id)
    user: User = request.user

    if request.method == "POST":
        if "unspoil" in request.POST:
            puzzle.spoiled.remove(user)
            if user.discord_user_id and discord.enabled():
                c, ch = discord.get_client_and_channel(puzzle)
                if c and ch:
                    ch.rm_visibility([user.discord_user_id])
                    c.save_channel(ch)
            puzzle.add_comment(
                request=request,
                author=user,
                is_system=True,
                send_email=False,
                content="Unspoiled " + str(user),
            )

    return render(
        request,
        "puzzle_escape.html",
        {
            "puzzle": puzzle,
            "spoiled": is_spoiled_on(user, puzzle),
            "status": status.get_display(puzzle.status),
            "is_in_testsolving": puzzle.status == status.TESTSOLVING,
        },
    )


@login_required
def edit_comment(request: AuthenticatedHttpRequest, id):
    comment = get_object_or_404(PuzzleComment, id=id)

    if request.user != comment.author:
        return render(
            request,
            "edit_comment.html",
            {
                "comment": comment,
                "not_author": True,
            },
        )
    elif comment.is_system:
        return render(
            request,
            "edit_comment.html",
            {
                "comment": comment,
                "is_system": True,
            },
        )

    if request.method == "POST":
        form = PuzzleCommentForm(request.POST)
        if form.is_valid():
            comment.content = form.cleaned_data["content"]
            comment.save()

            return redirect(urls.reverse("puzzle", args=[comment.puzzle.id]))
        else:
            return render(
                request, "edit_comment.html", {"comment": comment, "form": form}
            )

    return render(
        request,
        "edit_comment.html",
        {
            "comment": comment,
            "form": PuzzleCommentForm({"content": comment.content}),
        },
    )


@login_required
def edit_hint(request: AuthenticatedHttpRequest, id):
    hint = get_object_or_404(Hint, id=id)

    if request.method == "POST":
        if "delete" in request.POST:
            hint.delete()
            return redirect(urls.reverse("puzzle_hints", args=[hint.puzzle.id]))
        else:
            form = PuzzleHintForm(request.POST, instance=hint)
            if form.is_valid():
                form.save()
                return redirect(urls.reverse("puzzle_hints", args=[hint.puzzle.id]))
            else:
                return render(request, "edit_hint.html", {"hint": hint, "form": form})

    return render(
        request,
        "edit_hint.html",
        {
            "hint": hint,
            "form": PuzzleHintForm(instance=hint),
        },
    )


def warn_about_testsolving(is_spoiled, in_session, has_session):
    reasons = []
    if is_spoiled:
        reasons.append("you are spoiled")
    if in_session:
        reasons.append("you are already testsolving it")
    if has_session:
        reasons.append("there is an existing session you can join")

    if not reasons:
        return None
    if len(reasons) == 1:
        return reasons[0]
    return ", ".join(reasons[:-1]) + " and " + reasons[-1]


@login_required
def testsolve_main(request: AuthenticatedHttpRequest):
    user = request.user

    current_sessions = TestsolveSession.objects.filter(
        is_open=True, participations__user=user
    ).distinct()
    past_sessions = TestsolveSession.objects.filter(
        is_open=False, participations__user=user
    ).distinct()

    context = {
        "past_sessions": past_sessions,
        "current_sessions": current_sessions,
        "testsolve_admin_view": user.is_testsolve_coordinator,
    }

    return render(request, "testsolve_main.html", context)


@login_required
@group_required("Testsolve Coordinators", "EIC")
def testsolve_admin(request: AuthenticatedHttpRequest):
    user = request.user

    if request.method == "POST":
        session = get_object_or_404(TestsolveSession, id=request.POST["session_id"])
        if "change_is_open" in request.POST:
            new_open = request.POST["change_is_open"] == "1"
            if new_open:
                session.open_session()
            else:
                session.close_session()

    puzzles_in_factcheck = Puzzle.objects.filter(
        Q(status=status.NEEDS_TESTSOLVE_FACTCHECK)
        | Q(status=status.TESTSOLVE_FACTCHECK_REVISION)
    )

    ready_for_testing = (
        Puzzle.objects.filter(
            Q(status=status.TESTSOLVING) | Q(status=status.ACTIVELY_TESTSOLVING)
        )
        .annotate(
            num_active_testsolves=Count(
                "testsolve_sessions",
                filter=Q(testsolve_sessions__is_open=True),
            )
        )
        .order_by("status_mtime")
    )

    all_sessions = (
        TestsolveSession.objects.prefetch_related("participations")
        .select_related("admin", "puzzle")
        .annotate(
            participations_count=Count("participations"),
            pending_feedback_count=Count(
                "participations",
                filter=Q(participations__ended__isnull=True),
            ),
        )
    )
    all_active_sessions = all_sessions.filter(is_open=True)
    admin_active_sessions = all_active_sessions.filter(admin=user)
    all_past_sessions = all_sessions.filter(is_open=False)

    context = {
        "puzzles_in_factcheck": puzzles_in_factcheck,
        "puzzles_in_testsolving": ready_for_testing,
        "admin_active_sessions": admin_active_sessions,
        "all_active_sessions": all_active_sessions,
        "all_past_sessions": all_past_sessions,
    }

    return render(request, "testsolve_admin.html", context)


@login_required
def my_spoiled(request: AuthenticatedHttpRequest):
    spoiled = request.user.spoiled_puzzles.prefetch_related("answers").all()

    context = {"spoiled": spoiled}
    return render(request, "my_spoiled.html", context)


@login_required
@group_required("Testsolve Coordinators", "EIC")
def testsolve_finder(request: AuthenticatedHttpRequest):
    solvers = request.GET.getlist("solvers")
    users = User.objects.filter(pk__in=solvers) if solvers else None
    if users:
        puzzles = list(
            Puzzle.objects.filter(status=status.TESTSOLVING).order_by("priority")
        )
        for puzzle in puzzles:
            puzzle.user_data = []
            puzzle.unspoiled_count = 0
            puzzle.new_testsolve_url = (
                urls.reverse("testsolve_new", args=[puzzle.id])
                + "?"
                + "&".join(f"solvers={user.id}" for user in users)
            )
        for user in users:
            authored_ids = set(user.authored_puzzles.values_list("id", flat=True))
            editor_ids = set(user.editing_puzzles.values_list("id", flat=True))
            spoiled_ids = set(user.spoiled_puzzles.values_list("id", flat=True))
            spoiled_round_ids = set(user.spoiled_rounds.values_list("id", flat=True))
            for puzzle in puzzles:
                if puzzle.id in authored_ids:
                    puzzle.user_data.append("📝 Author")
                elif puzzle.id in editor_ids:
                    puzzle.user_data.append("💬 Editor")
                elif puzzle.id in spoiled_ids:
                    puzzle.user_data.append("👀 Spoiled")
                elif (
                    set(
                        puzzle.answers.values("round")
                        .distinct()
                        .values_list("round_id", flat=True)
                    )
                    & spoiled_round_ids
                ):
                    puzzle.user_data.append("🔮 Round-Spoiled")
                else:
                    puzzle.user_data.append("❓ Unspoiled")
                    puzzle.unspoiled_count += 1

        puzzles.sort(key=lambda puzzle: -puzzle.unspoiled_count)
    else:
        puzzles = None

    form = TestsolveFinderForm(solvers or request.user)

    return render(
        request,
        "testsolve_finder.html",
        {"puzzles": puzzles, "solvers": solvers, "form": form, "users": users},
    )


def normalize_answer(answer):
    return "".join(c for c in answer if c.isalnum()).upper()


def testsolve_queryset_to_csv(qs):
    opts = qs.model._meta
    csvResponse = HttpResponse(content_type="text/csv")
    csvResponse["Content-Disposition"] = "attachment;filename=export.csv"
    writer = csv.writer(csvResponse)

    field_names = [field.name for field in opts.fields]
    headers = list(field_names)
    headers.insert(0, "puzzle_id")
    headers.insert(1, "puzzle_name")
    writer.writerow(headers)
    for obj in qs:
        data = [getattr(obj, field) for field in field_names]
        data.insert(0, obj.session.puzzle.id)
        data.insert(1, obj.session.puzzle.spoilery_title)
        writer.writerow(data)

    return csvResponse


@login_required
def testsolve_csv(request: AuthenticatedHttpRequest, id):
    session = get_object_or_404(TestsolveSession, id=id)
    queryset = TestsolveParticipation.objects.filter(session=session)
    # opts = queryset.model._meta  # pylint: disable=protected-access
    # response = HttpResponse(content_type="text/csv")
    # response['Content-Disposition'] = 'attachment;filename=export.csv'
    # writer = csv.writer(response)

    # field_names = [field.name for field in opts.fields]
    # writer.writerow(field_names)
    # for obj in queryset:
    #     writer.writerow([getattr(obj, field) for field in field_names])

    return HttpResponse(testsolve_queryset_to_csv(queryset), content_type="text/csv")


@login_required
def testsolve_participants(request: AuthenticatedHttpRequest, id):
    session = get_object_or_404(TestsolveSession, id=id)
    puzzle = session.puzzle
    user = request.user
    if request.method == "POST":
        new_testers = User.objects.filter(
            pk__in=request.POST.getlist("add_testsolvers")
        )
        for new_tester in new_testers:
            if not TestsolveParticipation.objects.filter(
                session=session, user=new_tester
            ).exists():
                TestsolveParticipation(session=session, user=new_tester).save()

    current_testers = User.objects.exclude(
        pk__in=[user.id for user in session.participants()]
    )
    form = TestsolveParticipantPicker(None, current_testers)
    context = {"session": session, "puzzle": puzzle, "user": user, "form": form}
    return render(request, "testsolve_participants.html", context)


@login_required
@group_required("Testsolve Coordinators", "EIC")
def testsolve_new(request: AuthenticatedHttpRequest, id: int):
    puzzle = get_object_or_404(Puzzle, id=id)
    user = request.user

    if request.method == "POST":
        form = TestsolveSessionInfoForm(puzzle, data=request.POST)
        if not form.is_valid():
            return render(
                request, "testsolve_new.html", {"form": form, "puzzle": puzzle}
            )

        session: TestsolveSession = form.save(commit=False)
        session.puzzle = puzzle
        session.save()

        new_testers = form.cleaned_data["solvers"]

        session.create_testsolve_threads()
        session.create_testsolve_vc()
        session.add_solvers(new_testers)

        puzzle.add_comment(
            request=request,
            author=user,
            is_system=True,
            send_email=False,
            content=f"Created testsolve session #{session.id}",
        )

        if puzzle.status == status.TESTSOLVING:
            puzzle.set_status(request, status.ACTIVELY_TESTSOLVING)

        return redirect(urls.reverse("testsolve_one", args=[session.id]))
    else:
        solvers: list = request.GET.getlist("solvers")
        form = TestsolveSessionInfoForm(
            puzzle,
            admin=user,
            initial_solvers=(
                list(User.objects.filter(pk__in=solvers).all()) if solvers else []
            ),
        )
        return render(request, "testsolve_new.html", {"form": form, "puzzle": puzzle})


@login_required
def testsolve_one(request: AuthenticatedHttpRequest, id):
    session: TestsolveSession = get_object_or_404(
        (
            TestsolveSession.objects.select_related()
            .prefetch_related("participations__user")
            .prefetch_related("puzzle__spoiled")
            .prefetch_related("puzzle__authors")
            .prefetch_related("puzzle__editors")
            .prefetch_related("puzzle__postprodders")
            .prefetch_related("puzzle__factcheckers")
        ),
        id=id,
    )
    puzzle = session.puzzle
    user = request.user
    current_testers = User.objects.exclude(
        pk__in=[user.id for user in session.participants()]
    )
    testsolve_edit_form = TestsolveSessionInfoForm(
        puzzle,
        exclude=current_testers,
        instance=session,
    )
    c, ch = discord.get_client_and_channel(puzzle)

    if request.method == "POST":
        if "edit_notes" in request.POST:
            notes_form = TestsolveSessionNotesForm(request.POST, instance=session)
            if notes_form.is_valid():
                notes_form.save()

        elif "do_guess" in request.POST:
            guess_form = GuessForm(request.POST)
            if not guess_form.is_valid():
                return redirect(urls.reverse("testsolve_one", args=[id]))

            guess = guess_form.cleaned_data["guess"]
            correct = any(
                answer.is_correct(guess) for answer in session.puzzle.answers.all()
            )
            # Check these later, only if guess is not correct
            partially_correct = False
            partial_response = ""

            if correct:
                comment = f"Correct answer: {guess}."

                # Send a congratulatory message to the thread.
                session.post_message(
                    "\n".join(
                        [
                            ":tada: Congratulations on solving this puzzle! :tada:",
                            f"Answer: `{guess}`",
                            (
                                "Time since testsolve started:"
                                f" {session.time_since_started}"
                            ),
                        ]
                    )
                )
                session.puzzle.post_message_to_testsolving_admin_thread(
                    f":tada: Session {session.id} solved the puzzle! :tada:"
                )
                session.puzzle.post_message_to_channel(
                    f":tada: Session {session.id} solved the puzzle! :tada:"
                )

            else:
                # Guess might still be partially correct
                for answer in session.puzzle.pseudo_answers.all():
                    if answer.is_correct(guess):
                        partially_correct = True
                        partial_response = answer.response
                        comment = f"Guessed: {guess}. Response: {partial_response}"
                        break

                else:
                    comment = f"Guessed: {guess}. Incorrect."
                session.post_message(
                    {
                        "content": f"<@{user.discord_user_id}> g{comment[1:]}",
                        "allowed_mentions": {"parse": []},
                    }
                )

            guess_model = TestsolveGuess(
                session=session,
                user=user,
                guess=guess,
                correct=correct,
                partially_correct=partially_correct,
                partial_response=partial_response,
            )
            guess_model.save()

            session.add_comment(
                request=request,
                author=user,
                is_system=True,
                send_email=False,
                content=comment,
            )

            # Auto-set puzzle status to Awaiting Testsolve Review.
            if correct and puzzle.status == status.ACTIVELY_TESTSOLVING:
                puzzle.set_status(request, status.AWAITING_TESTSOLVE_REVIEW)

        elif "change_joinable" in request.POST:
            session.joinable = request.POST["change_joinable"] == "1"
            session.save()

        elif "change_is_open" in request.POST:
            new_open = request.POST["change_is_open"] == "1"
            if new_open:
                session.open_session()
            else:
                session.close_session()

        elif "add_comment" in request.POST:
            comment_form = PuzzleCommentForm(request.POST)
            if comment_form.is_valid():
                session.add_comment(
                    request=request,
                    author=user,
                    is_system=False,
                    send_email=True,
                    content=comment_form.cleaned_data["content"],
                )
        elif "react_comment" in request.POST:
            emoji = request.POST.get("emoji")
            puzzle_comment = PuzzleComment.objects.get(id=request.POST["react_comment"])
            # This just lets you react with any string to a comment, but it's
            # not the end of the world.
            if emoji and puzzle_comment:
                CommentReaction.toggle(emoji, puzzle_comment, user)

        elif "escape_testsolve" in request.POST:
            to_del = get_object_or_404(
                TestsolveParticipation,
                session=session,
                user=user,
            )
            to_del.delete()
            return redirect(urls.reverse("testsolve_main"))
        elif "update_session" in request.POST:
            form = TestsolveSessionInfoForm(puzzle, data=request.POST)
            if not form.is_valid():
                return redirect(urls.reverse("testsolve_one", args=[id]))

            session.set_admin(form.cleaned_data["admin"])
            session.add_solvers(form.cleaned_data["solvers"])

        # refresh
        return redirect(urls.reverse("testsolve_one", args=[id]))

    participation: TestsolveParticipation | None = None
    with contextlib.suppress(TestsolveParticipation.DoesNotExist):
        participation = TestsolveParticipation.objects.get(session=session, user=user)

    spoiled = is_spoiled_on(user, puzzle)
    answers_exist = session.puzzle.answers.exists()
    comments = session.comments.filter(puzzle=puzzle)
    is_solved = session.has_correct_guess()

    true_participants = []

    user_is_participant = False

    for participant in session.participations.all():
        if get_user_role(participant.user, session.puzzle) not in ["author", "editor"]:
            true_participants.append(participant)
        elif participant.user.id == user.id:
            user_is_participant = True

    context = {
        "session": session,
        "participation": participation,
        "spoiled": spoiled,
        "comments": comments,
        "answers_exist": answers_exist,
        "guesses": TestsolveGuess.objects.filter(session=session).select_related(
            "user"
        ),
        "is_solved": is_solved,
        "notes_form": TestsolveSessionNotesForm(instance=session),
        "guess_form": GuessForm(),
        "comment_form": PuzzleCommentForm(),
        "testsolve_edit_form": testsolve_edit_form,
        "true_participants": true_participants,
        "user_is_hidden_from_list": user_is_participant,
        "postprod_url": settings.POSTPROD_URL,
        "is_coordinator": session.admin == user,
        "testsolve_admin_view": user.is_testsolve_coordinator,
    }

    if c:
        context.update(
            {
                "admin_thread_link": c.get_thread_link(
                    session.puzzle.testsolving_admin_thread_id
                ),
                "solve_thread_link": c.get_thread_link(session.discord_thread_id),
            }
        )

    return render(request, "testsolve_one.html", context)


@login_required
def testsolve_feedback(request: AuthenticatedHttpRequest, id: int):
    session = get_object_or_404(TestsolveSession, id=id)

    feedback = session.participations.filter(feedbacks__isnull=False).distinct()
    no_feedback = (
        session.participations.filter(feedbacks__isnull=True).distinct().count()
    )
    participants = no_feedback + len(feedback)

    context = {
        "session": session,
        "no_feedback": no_feedback,
        "feedback": feedback,
        "participants": participants,
        "title": f"Testsolving Feedback - {session.puzzle}",
        "bulk": False,
    }

    return render(request, "testsolve_feedback.html", context)


@login_required
def puzzle_feedback(request: AuthenticatedHttpRequest, id):
    puzzle = get_object_or_404(Puzzle, id=id)

    feedback = (
        TestsolveParticipation.objects.filter(
            Q(session__puzzle=puzzle) & Q(feedbacks__isnull=False)
        )
        .select_related("session")
        .order_by("session__id")
        .distinct()
    )

    context = {
        "puzzle": puzzle,
        "feedback": feedback,
        "title": f"Testsolve Feedback for {puzzle.spoilery_title}",
        "bulk": True,
    }

    return render(request, "testsolve_feedback.html", context)


@login_required
def puzzle_feedback_all(request: AuthenticatedHttpRequest):
    feedback = (
        TestsolveParticipation.objects.filter(feedbacks__isnull=False)
        .select_related("session")
        .order_by("session__puzzle__id", "session__id")
    )

    context = {"feedback": feedback, "title": "All Testsolve Feedback", "bulk": True}

    return render(request, "testsolve_feedback.html", context)


@login_required
def puzzle_feedback_csv(request: AuthenticatedHttpRequest, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    feedback = (
        TestsolveParticipation.objects.filter(session__puzzle=puzzle)
        .filter(feedbacks__isnull=False)
        .select_related("session")
        .order_by("session__id")
    )

    return HttpResponse(testsolve_queryset_to_csv(feedback), content_type="text/csv")


@login_required
def puzzle_feedback_all_csv(request: AuthenticatedHttpRequest):
    feedback = (
        TestsolveParticipation.objects.filter(feedbacks__isnull=False)
        .select_related("session")
        .order_by("session__puzzle__id", "session__id")
    )

    return HttpResponse(testsolve_queryset_to_csv(feedback), content_type="text/csv")


@login_required
def spoiled(request: AuthenticatedHttpRequest):
    puzzles = Puzzle.objects.filter(
        status__in=[status.TESTSOLVING, status.REVISING]
    ).annotate(
        is_spoiled=Exists(
            User.objects.filter(spoiled_puzzles=OuterRef("pk"), id=request.user.id)
        )
    )
    context = {"puzzles": puzzles}
    return render(request, "spoiled.html", context)


@login_required
def testsolve_finish(request: AuthenticatedHttpRequest, id):
    user = request.user

    session = get_object_or_404(TestsolveSession, id=id)
    puzzle = session.puzzle
    participation = get_object_or_404(
        TestsolveParticipation, session=session, user=user
    )

    if request.method == "POST":
        participation_form = TestsolveParticipationForm(
            request.POST,
            instance=participation,
        )
        feedback_form = TestsolveFeedbackForm(request.POST)

        if not participation_form.is_valid() or not feedback_form.is_valid():
            context = {
                "session": session,
                "participation": participation,
                "participation_form": participation_form,
                "feedback_form": feedback_form,
            }
            return render(request, "testsolve_finish.html", context)

        participation = participation_form.save()
        feedback: TestsolveFeedback = feedback_form.save(commit=False)
        feedback.participation = participation
        feedback.save()
        finish_method = participation_form.cleaned_data["finish_method"]
        feedback.make_comment(request, finish_method)

        if finish_method != "INCOMPLETE":
            participation.ended = datetime.datetime.now()
            participation.save()

        # Even if no-spoil, they saw answers and should be round-spoiled.
        if finish_method != "LEAVE" and puzzle.is_meta:
            puzzle_round_ids = (
                puzzle.answers.values("round")
                .distinct()
                .values_list("round_id", flat=True)
            )
            Round.spoiled.through.objects.bulk_create(
                [
                    Round.spoiled.through(round_id=r_id, user=user)
                    for r_id in puzzle_round_ids
                ],
                ignore_conflicts=True,
            )

        if finish_method == "SPOIL":
            if not is_spoiled_on(user, puzzle):
                puzzle.spoiled.add(user)
            return redirect(urls.reverse("puzzle", args=[puzzle.id]))
        else:
            return redirect(urls.reverse("testsolve_one", args=[id]))

    participation_form = TestsolveParticipationForm(instance=participation)
    feedback_form = TestsolveFeedbackForm()

    context = {
        "session": session,
        "participation": participation,
        "participation_form": participation_form,
        "feedback_form": feedback_form,
    }

    return render(request, "testsolve_finish.html", context)


@login_required
def postprod(request: AuthenticatedHttpRequest):
    if request.method == "POST":
        if "puzzle_id" not in request.POST:
            return redirect(urls.reverse("index"))

        puzzle = get_object_or_404(Puzzle, id=request.POST["puzzle_id"])
        user = request.user

        if puzzle.status in {
            status.NEEDS_POSTPROD,
            status.ACTIVELY_POSTPRODDING,
            status.POSTPROD_BLOCKED,
            status.POSTPROD_BLOCKED_ON_TECH,
        }:
            if not puzzle.postprodders.filter(id=user.id).exists():
                puzzle.postprodders.add(user)
                puzzle.save()
        else:
            msg = f"Puzzle {puzzle.id} is not currently in a postprod status"
            raise PermissionDenied(msg)

        # Stupid hack lol
        if puzzle.status == status.POSTPROD_BLOCKED:
            puzzle.set_status(request, status.NEEDS_POSTPROD)

        if puzzle.status == status.NEEDS_POSTPROD:
            puzzle.set_status(request, status.ACTIVELY_POSTPRODDING)

        if puzzle.postprod_issue_id and user.github_username:
            g = Github(auth=Auth.Token(settings.GITHUB_TOKEN))
            repo = g.get_repo(settings.HUNT_REPO_NAME)
            issue = repo.get_issue(puzzle.postprod_issue_id)
            assignees = issue.assignees
            # Private repos support only one assignee
            if assignees and assignees[0].login != user.github_username:
                issue.remove_from_assignees(*assignees)
                issue.add_to_assignees(user.github_username)
            elif not assignees:
                issue.add_to_assignees(user.github_username)

        c, ch = discord.get_client_and_channel(puzzle)
        if c:
            payload = {
                "content": f"{user.discord_tag} has been added as a postprodder.",
                "allowed_mentions": {"parse": []},
            }
            if ch:
                c.post_message(ch.id, payload)

        return redirect(urls.reverse("puzzle", args=[puzzle.id]))

    postprodding = Puzzle.objects.filter(
        status__in=[
            status.NEEDS_POSTPROD,
            status.ACTIVELY_POSTPRODDING,
            status.POSTPROD_BLOCKED,
            status.POSTPROD_BLOCKED_ON_TECH,
        ],
        postprodders=request.user,
    )
    needs_postprod = Puzzle.objects.annotate(
        has_postprodder=Exists(User.objects.filter(postprodding_puzzles=OuterRef("pk")))
    ).filter(status=status.NEEDS_POSTPROD, has_postprodder=False)
    postprod_blocked = Puzzle.objects.annotate(
        has_postprodder=Exists(User.objects.filter(postprodding_puzzles=OuterRef("pk")))
    ).filter(status=status.POSTPROD_BLOCKED, has_postprodder=False)

    context = {
        "postprodding": postprodding,
        "needs_postprod": needs_postprod,
        "postprod_blocked": postprod_blocked,
    }
    return render(request, "postprod.html", context)


@login_required
def postprod_all(request: AuthenticatedHttpRequest):
    needs_postprod = Puzzle.objects.filter(
        status__in=[
            status.NEEDS_POSTPROD,
            status.ACTIVELY_POSTPRODDING,
            status.POSTPROD_BLOCKED,
            status.POSTPROD_BLOCKED_ON_TECH,
            status.AWAITING_POSTPROD_APPROVAL,
            status.NEEDS_FACTCHECK,
            status.NEEDS_FINAL_REVISIONS,
            status.NEEDS_HINTS,
            status.AWAITING_HINTS_APPROVAL,
        ]
    )

    sorted_puzzles = sorted(
        needs_postprod, key=lambda a: (status.STATUSES.index(a.status), a.name)
    )

    context = {
        "puzzles": sorted_puzzles,
    }
    return render(request, "postprod_all.html", context)


@login_required
def factcheck(request: AuthenticatedHttpRequest):
    if request.method == "POST":
        if "puzzle_id" not in request.POST:
            return redirect(urls.reverse("index"))

        puzzle = get_object_or_404(Puzzle, id=request.POST["puzzle_id"])
        user = request.user
        kind = ""

        if puzzle.status == status.NEEDS_TESTSOLVE_FACTCHECK:
            kind = "quickcheck"
            if not puzzle.quickcheckers.filter(id=user.id).exists():
                puzzle.quickcheckers.add(user)
                puzzle.save()
        elif puzzle.status in {status.NEEDS_FACTCHECK, status.NEEDS_COPY_EDITS}:
            kind = "factcheck"
            if not puzzle.factcheckers.filter(id=user.id).exists():
                puzzle.factcheckers.add(user)
                puzzle.save()
        else:
            msg = f"Puzzle {puzzle.id} is not currently in a factchecking status"
            raise PermissionDenied(msg)

        sheet_id = google.GoogleManager.instance().create_factchecking_sheet(
            puzzle, kind=kind
        )
        sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"

        puzzle.add_comment(
            request=request,
            author=user,
            is_system=True,
            send_email=False,
            content=f"Began new {kind}: <{sheet_url}>",
        )

        c, ch = discord.get_client_and_channel(puzzle)
        if c:
            payload = {
                "content": (
                    f"{user.discord_tag} began new {kind} for {puzzle.id}:"
                    f" <{sheet_url}>"
                ),
                "allowed_mentions": {"parse": []},
            }
            if ch:
                c.post_message(ch.id, payload)
            c.post_message(settings.DISCORD_FACTCHECK_ADMIN_CHANNEL_ID, payload)

        return redirect(urls.reverse("puzzle", args=[puzzle.id]))

    currently_in_factchecking = Puzzle.objects.filter(
        Q(status=status.NEEDS_TESTSOLVE_FACTCHECK)
        | Q(status=status.NEEDS_FACTCHECK)
        | Q(status=status.NEEDS_COPY_EDITS)
    )

    active_user_factchecks = currently_in_factchecking.filter(
        Q(factcheckers=request.user) | Q(quickcheckers=request.user)
    )

    puzzles = Puzzle.objects.annotate(
        has_factchecker=Exists(
            User.objects.filter(factchecking_puzzles=OuterRef("pk"))
        ),
        has_quickchecker=Exists(
            User.objects.filter(quickchecking_puzzles=OuterRef("pk"))
        ),
    )

    needs_pretestsolve_factcheck = puzzles.filter(
        status=status.NEEDS_TESTSOLVE_FACTCHECK, has_quickchecker=False
    )
    needs_factcheck = puzzles.filter(
        status=status.NEEDS_FACTCHECK, has_factchecker=False
    )
    needs_copyedit = puzzles.filter(
        status=status.NEEDS_COPY_EDITS, has_factchecker=False
    )

    context = {
        "factchecking": active_user_factchecks,
        "needs_pretestsolve_factchecking": needs_pretestsolve_factcheck,
        "needs_factchecking": needs_factcheck,
        "needs_copyediting": needs_copyedit,
        "all_factchecking": currently_in_factchecking,
    }
    return render(request, "factcheck.html", context)


@login_required
@group_required("EIC")
def eic(request: AuthenticatedHttpRequest, template="awaiting_editor.html"):
    return render(
        request,
        template,
        {
            "awaiting_eic": Puzzle.objects.filter(
                status=status.AWAITING_APPROVAL,
            ).order_by("status_mtime"),
            "needs_discussion": Puzzle.objects.filter(
                status=status.NEEDS_DISCUSSION
            ).order_by("status_mtime"),
            "awaiting_answer": Puzzle.objects.filter(
                status=status.AWAITING_ANSWER
            ).order_by("status_mtime"),
            "awaiting_approval_for_testsolving": Puzzle.objects.filter(
                status=status.AWAITING_APPROVAL_FOR_TESTSOLVING
            ).order_by("status_mtime"),
            "awaiting_approval_post_testsolving": Puzzle.objects.filter(
                status=status.AWAITING_APPROVAL_POST_TESTSOLVING
            ).order_by("status_mtime"),
            "awaiting_hints_approval": Puzzle.objects.filter(
                status=status.AWAITING_HINTS_APPROVAL
            ).order_by("status_mtime"),
            "awaiting_approval_postprod": Puzzle.objects.filter(
                status=status.AWAITING_POSTPROD_APPROVAL
            ).order_by("status_mtime"),
        },
    )


@login_required
def editor_overview(request: AuthenticatedHttpRequest):
    active_statuses = [
        status.INITIAL_IDEA,
        status.AWAITING_APPROVAL,
        status.NEEDS_DISCUSSION,
        status.IDEA_IN_DEVELOPMENT,
        status.AWAITING_ANSWER,
        status.WRITING,
        status.AWAITING_APPROVAL_FOR_TESTSOLVING,
        status.NEEDS_TESTSOLVE_FACTCHECK,
        status.TESTSOLVE_FACTCHECK_REVISION,
        status.TESTSOLVING,
        status.AWAITING_TESTSOLVE_REVIEW,
        status.REVISING,
        status.AWAITING_APPROVAL_POST_TESTSOLVING,
        status.NEEDS_HINTS,
        status.AWAITING_HINTS_APPROVAL,
        status.NEEDS_POSTPROD,
        status.ACTIVELY_POSTPRODDING,
        status.POSTPROD_BLOCKED,
        status.POSTPROD_BLOCKED_ON_TECH,
        status.AWAITING_POSTPROD_APPROVAL,
        # status.NEEDS_FACTCHECK,
        # status.NEEDS_FINAL_REVISIONS,
        # status.NEEDS_COPY_EDITS,
        # status.DONE,
        # status.DEFERRED,
        # status.DEAD,
    ]

    puzzle_editors = (
        User.objects.exclude(editing_puzzles__isnull=True)
        .annotate(num_editing=Count("editing_puzzles"))
        .order_by("id")
    )

    actively_editing = [(p.id, 0) for p in puzzle_editors]

    edited_puzzles = Puzzle.objects.exclude(editors__isnull=True).order_by("status")
    active_puzzles = edited_puzzles.filter(status__in=active_statuses)

    all_editors = [e.id for e in puzzle_editors]
    editored_puzzles = []
    for p in edited_puzzles:
        this_puz_editors = [pe.id for pe in p.editors.all()]
        editored_puzzles.append(
            {
                "id": p.id,
                "codename": p.codename,
                "name": p.name,
                "status": status.get_display(p.status),
                "editors": [1 if e in this_puz_editors else 0 for e in all_editors],
            }
        )

    for p in active_puzzles:
        this_puz_editors = [pe.id for pe in p.editors.all()]
        actively_editing = [
            (ae[0], ae[1] + (1 if ae[0] in this_puz_editors else 0))
            for ae in actively_editing
        ]

    context = {
        "editors": puzzle_editors,
        "actively_editing": actively_editing,
        "editored_puzzles": editored_puzzles,
    }
    return render(request, "editor_overview.html", context)


@login_required
def needs_editor(request: AuthenticatedHttpRequest):
    needs_editors = Puzzle.objects.annotate(
        remaining_des=(F("needed_editors") - Count("editors"))
    ).filter(remaining_des__gt=0)

    context = {"needs_editors": needs_editors}
    return render(request, "needs_editor.html", context)


@login_required
@group_required("EIC")
def byround_eic(request: AuthenticatedHttpRequest, id=None):
    return byround(request, id, eic_view=True)


@login_required
@group_required("EIC")
def byround(request: AuthenticatedHttpRequest, id=None, eic_view=False):
    round_objs = Round.objects.all()
    if id:
        round_objs = round_objs.filter(pk=id)
    round_objs = round_objs.prefetch_related("answers", "editors").order_by(
        Lower("name")
    )

    rounds = [
        {
            "id": round.id,
            "name": round.name,
            "description": round.description,
            "answers": [
                {
                    "answer": answer.answer,
                    "id": answer.id,
                    "notes": answer.notes,
                    "puzzles": (
                        answer.puzzles.all()
                    ),  # .exclude(status=status.DEAD).exclude(status=status.DEFERRED),
                }
                for answer in round.answers.all()
                .prefetch_related("puzzles")
                .prefetch_related("puzzles__authors")
                .prefetch_related("puzzles__postprod")
                .order_by(Lower("answer"))
            ],
            "editor": round.editors.first(),
        }
        for round in round_objs
    ]

    eics = set()
    for round in round_objs:
        eic = round.editors.first()
        if eic is not None:
            eics.add(eic)

    return render(
        request,
        "allrounds.html",
        {
            "rounds": rounds,
            "eics": eics,
            "eic_view": eic_view,
            "single_round": rounds[0] if id else None,
        },
    )


@login_required
@group_required("EIC")
def rounds(request: AuthenticatedHttpRequest, id=None):
    user = request.user

    if request.method == "POST":
        if "spoil_on" in request.POST:
            get_object_or_404(Round, id=request.POST["spoil_on"]).spoiled.add(user)

        elif "new_round" in request.POST:
            new_round_form = RoundForm(request.POST)
            if new_round_form.is_valid():
                new_round = new_round_form.save()
                new_round.spoiled.add(user)

        elif "add_answer" in request.POST:
            answer_form = AnswerForm(None, request.POST)
            if answer_form.is_valid():
                answer_form.save()

        elif "delete_answer" in request.POST:
            get_object_or_404(PuzzleAnswer, id=request.POST["delete_answer"]).delete()

        return redirect(urls.reverse("rounds"))

    round_objs = Round.objects.all()
    if id:
        round_objs = round_objs.filter(pk=id)
    round_objs = round_objs.prefetch_related("answers", "editors").order_by(
        Lower("name")
    )

    rounds = [
        {
            "id": round.id,
            "name": round.name,
            "description": round.description,
            "spoiled": round.spoiled.filter(id=user.id).exists(),
            "answers": [
                {
                    "answer": answer.answer,
                    "id": answer.id,
                    "notes": answer.notes,
                    "puzzles": answer.puzzles.all(),
                }
                for answer in round.answers.all().order_by(Lower("answer"))
            ],
            "form": AnswerForm(round),
            "editors": round.editors.all().order_by(Lower("credits_name")),
        }
        for round in round_objs
    ]

    return render(
        request,
        "rounds.html",
        {
            "rounds": rounds,
            "single_round": rounds[0] if id else None,
            "new_round_form": RoundForm(),
        },
    )


@login_required
@group_required("EIC")
def edit_round(request: AuthenticatedHttpRequest, id):
    round = get_object_or_404(Round, id=id)
    if request.method == "POST":
        print(request.POST)
        if request.POST.get("delete") and request.POST.get("sure-delete") == "on":
            round.delete()
            return redirect(urls.reverse("rounds"))
        form = RoundForm(request.POST, instance=round)
        if form.is_valid():
            form.save()

            return redirect(urls.reverse("rounds"))
        else:
            return render(request, "edit_round.html", {"form": form})
    return render(
        request,
        "edit_round.html",
        {
            "form": RoundForm(instance=round),
            "round": round,
            "has_answers": round.answers.count(),
        },
    )


@login_required
def support_all(request: AuthenticatedHttpRequest):
    user = request.user
    team = request.GET.get("team", "ALL")

    # Condition 1: See requests for the teams that this user is supervising
    filters = []
    if user.is_eic:
        # EICs can see everything
        filters.append(Q(pk__isnull=False))
    else:
        # See only the team(s) that the user's groups allow
        group_filters = [
            Q(team=SupportRequest.GROUP_TO_TEAM[group_name].value)
            for group_name in user.groups.values_list("name", flat=True)
            if group_name in SupportRequest.GROUP_TO_TEAM
        ]
        if group_filters:
            filters.append(reduce(operator.or_, group_filters))

    all_open_requests = SupportRequest.objects.filter(
        status__in=["REQ", "APP"]
    ).order_by("team", "status")
    all_closed_requests = SupportRequest.objects.exclude(
        status__in=["REQ", "APP"]
    ).order_by("team", "status")
    if team != "ALL":
        all_open_requests = all_open_requests.filter(team=team)
        all_closed_requests = all_closed_requests.filter(team=team)

    # Condition 2: See requests for puzzles that you are spoiled on, or actively
    # working on.
    is_spoiled = User.objects.filter(
        Q(spoiled_puzzles=OuterRef("puzzle"))
        | Q(assigned_support_requests=OuterRef("pk")),
        pk=user.id,
    )
    # Either condition 1 or condition 2
    is_visible = Exists(is_spoiled) | Q(*filters)
    open_requests = all_open_requests.filter(is_visible)
    closed_requests = all_closed_requests.filter(is_visible)

    team_title = team.title()
    if team == "ACC":
        team_title = "Accessibility"

    return render(
        request,
        "support_all.html",
        {
            "title": f"{team_title} support requests",
            "open_requests": open_requests,
            "closed_requests": closed_requests,
            "hidden_count": (
                all_open_requests.count()
                + all_closed_requests.count()
                - open_requests.count()
                - closed_requests.count()
            ),
            "type": "all",
            "team": team,
        },
    )


@login_required
def support_by_puzzle(request: AuthenticatedHttpRequest, id):
    """Show all requests for a puzzle or create one"""
    puzzle = get_object_or_404(Puzzle, id=id)
    support = []
    for team, team_name in SupportRequest.Team.choices:
        support.append(
            {
                "obj": (
                    SupportRequest.objects.filter(team=team)
                    .filter(puzzle=puzzle)
                    .first()
                ),
                "short": team,
                "display": team_name,
            }
        )
    # if post = create support for puzzle then redirect to support_one
    # else show all for puzzle plus links to create
    return render(
        request,
        "support_for_puzzle.html",
        {
            "title": f"Support requests for {puzzle.name}",
            "type": "puzzle",
            "support": support,
            "puzzle": puzzle,
        },
    )


@login_required
def support_by_puzzle_id(request: AuthenticatedHttpRequest, id, team):
    """Show support by puzzle and type or else show form to create a new one"""
    id = int(id)
    puzzle = get_object_or_404(Puzzle, pk=id)
    if request.method == "POST" and "create" in request.POST:
        support = SupportRequest.objects.create(puzzle=puzzle, team=team)
    else:
        try:
            support = SupportRequest.objects.get(puzzle=puzzle, team=team)
        except SupportRequest.DoesNotExist:
            return render(
                request,
                "support_confirm.html",
                {
                    "puzzle": puzzle,
                },
            )

    if request.method == "POST":
        if "edit_team_notes" in request.POST:
            old_notes = support.team_notes
            team_notes_form = SupportRequestTeamNotesForm(
                request.POST, instance=support
            )
            if team_notes_form.is_valid():
                old_status = support.get_status_display
                team_notes_form.save()
                support.team_notes_updater = request.user
                support.team_notes_mtime = datetime.datetime.now(
                    datetime.timezone.utc
                ).astimezone()
                support.save()
                new_notes = support.team_notes
                send_mail_wrapper(
                    (
                        f"{support.get_team_display()} team support request update for"
                        f" {support.puzzle.name}"
                    ),
                    "emails/support_update",
                    {
                        "request": request,
                        "support": support,
                        "old_notes": old_notes,
                        "new_notes": new_notes,
                        "old_status": old_status,
                    },
                    support.puzzle.get_emails(),
                )
        elif "edit_author_notes" in request.POST:
            old_notes = support.author_notes
            author_notes_form = SupportRequestAuthorNotesForm(
                request.POST, instance=support
            )
            if author_notes_form.is_valid():
                old_status = support.get_status_display
                author_notes_form.save()
                support.author_notes_updater = request.user
                support.author_notes_mtime = datetime.datetime.now(
                    datetime.timezone.utc
                ).astimezone()
                if support.status in ["APP", "COMP"]:
                    support.outdated = True
                support.save()
                new_notes = support.author_notes
                send_mail_wrapper(
                    (
                        f"{support.get_team_display()} team support request update for"
                        f" {support.puzzle.name}"
                    ),
                    "emails/support_update",
                    {
                        "request": request,
                        "support": support,
                        "old_notes": old_notes,
                        "new_notes": new_notes,
                        "old_status": old_status,
                    },
                    support.get_emails(),
                )
                # add call to email team with update and new status
        elif "update_status" in request.POST:
            status_form = SupportRequestStatusForm(request.POST, instance=support)
            if status_form.is_valid():
                status_form.save()
                if support.outdated:
                    support.outdated = False
                    support.save()

    return render(
        request,
        "support_view.html",
        {
            "support": support,
            "author_notes_form": SupportRequestAuthorNotesForm(instance=support),
            "team_notes_form": SupportRequestTeamNotesForm(instance=support),
            "status_form": SupportRequestStatusForm(instance=support),
        },
    )


@login_required
@group_required("EIC")
def edit_answer(request: AuthenticatedHttpRequest, id):
    answer = get_object_or_404(PuzzleAnswer, id=id)

    if request.method == "POST":
        answer_form = AnswerForm(answer.round, request.POST, instance=answer)
        if answer_form.is_valid():
            answer_form.save()

            return redirect(urls.reverse("edit_answer", args=[id]))
    else:
        answer_form = AnswerForm(answer.round, instance=answer)

    return render(request, "edit_answer.html", {"answer": answer, "form": answer_form})


@login_required
@login_required
def edit_pseudo_answer(request, id):
    pseudo_answer = get_object_or_404(PseudoAnswer, id=id)

    if request.method == "POST":
        if "delete" in request.POST:
            pseudo_answer.delete()
            return redirect(urls.reverse("puzzle", args=[pseudo_answer.puzzle_id]))
        else:
            form = PuzzlePseudoAnswerForm(request.POST, instance=pseudo_answer)
            if form.is_valid():
                form.save()
                return redirect(urls.reverse("puzzle", args=[pseudo_answer.puzzle_id]))
            else:
                return render(
                    request,
                    "edit_pseudo_answer.html",
                    {"pseudo_answer": pseudo_answer, "form": form},
                )

    return render(
        request,
        "edit_pseudo_answer.html",
        {
            "pseudo_answer": pseudo_answer,
            "form": PuzzlePseudoAnswerForm(instance=pseudo_answer),
        },
    )


@login_required
@group_required("EIC")
def bulk_add_answers(request: AuthenticatedHttpRequest, id):
    round = get_object_or_404(Round, id=id)
    if request.method == "POST":
        lines = request.POST["bulk_add_answers"].split("\n")
        answers = [line.strip() for line in lines]

        PuzzleAnswer.objects.bulk_create(
            [PuzzleAnswer(answer=answer, round=round) for answer in answers if answer]
        )

        return redirect(urls.reverse("bulk_add_answers", args=[id]))

    return render(
        request,
        "bulk_add_answers.html",
        {
            "round": round,
        },
    )


@login_required
@group_required("EIC")
def tags(request: AuthenticatedHttpRequest):
    return render(
        request,
        "tags.html",
        {"tags": PuzzleTag.objects.all().annotate(count=Count("puzzles"))},
    )


@login_required
def statistics(request: AuthenticatedHttpRequest):
    past_writing = 0
    past_testsolving = 0

    counts = Puzzle.objects.values("status").annotate(
        meta_count=Count("status", filter=Q(is_meta=True)),
        feeder_count=Count("status", filter=Q(is_meta=False)),
    )

    statuses = []
    for p in sorted(counts, key=lambda x: status.get_status_rank(x["status"])):
        status_obj = {
            "status": status.get_display(p["status"]),
            "meta_count": p["meta_count"],
            "feeder_count": p["feeder_count"],
            "count": p["meta_count"] + p["feeder_count"],
        }
        if status.past_writing(p["status"]):
            past_writing += p["meta_count"] + p["feeder_count"]
        if status.past_testsolving(p["status"]):
            past_testsolving += p["meta_count"] + p["feeder_count"]

        statuses.append(status_obj)

    answers = {
        "assigned": PuzzleAnswer.objects.filter(puzzles__isnull=False).count(),
        "rest": PuzzleAnswer.objects.filter(puzzles__isnull=False).count(),
        "waiting": PuzzleAnswer.objects.filter(puzzles__isnull=True).count(),
    }

    target_count = SiteSetting.get_int_setting("TARGET_PUZZLE_COUNT")
    unreleased_count = SiteSetting.get_int_setting("UNRELEASED_PUZZLE_COUNT")
    image_base64 = aggregated_feeder_graph_b64(
        request.GET.get("time", "alltime"), target_count
    )

    return render(
        request,
        "statistics.html",
        {
            "status": statuses,
            "answers": answers,
            "image_base64": image_base64,
            "past_writing": past_writing,
            "past_testsolving": past_testsolving,
            "target_count": target_count,
            "unreleased_count": unreleased_count,
        },
    )


@login_required
@group_required("EIC")
def new_tag(request: AuthenticatedHttpRequest):
    if request.method == "POST":
        form = PuzzleTagForm(request.POST)
        if form.is_valid():
            form.save()

            return redirect(urls.reverse("tags"))
        else:
            return render(request, "new_tag.html", {"form": form})
    return render(request, "new_tag.html", {"form": PuzzleTagForm()})


@login_required
def single_tag(request: AuthenticatedHttpRequest, id):
    tag = get_object_or_404(PuzzleTag, id=id)

    count = tag.puzzles.count()
    label = "1 puzzle" if count == 1 else f"{count} puzzles"
    return render(
        request,
        "single_tag.html",
        {
            "tag": tag,
            "count_label": label,
        },
    )


@login_required
@group_required("EIC")
def edit_tag(request: AuthenticatedHttpRequest, id):
    tag = get_object_or_404(PuzzleTag, id=id)
    if request.method == "POST":
        form = PuzzleTagForm(request.POST, instance=tag)
        if form.is_valid():
            form.save()

            return redirect(urls.reverse("tags"))
        else:
            return render(request, "edit_tag.html", {"form": form, "tag": tag})
    return render(
        request,
        "edit_tag.html",
        {
            "form": PuzzleTagForm(instance=tag),
            "tag": tag,
        },
    )


# distinct=True because
# https://stackoverflow.com/questions/59071464/django-how-to-annotate-manytomany-field-with-count
# Doing separate aggregations across these fields and manually joining because
# the query resulting from doing them all at once seems to be very slow? Takes
# a list (not QuerySet) of all users and a dictionary of annotation names to
# Django annotations; mutates the users by adding the corresponding attributes
# to them.
def annotate_users_helper(user_list, annotation_kwargs):
    id_dict = {}
    for my_user in User.objects.all().annotate(**annotation_kwargs):
        id_dict[my_user.id] = my_user
    for user in user_list:
        my_user = id_dict[user.id]
        for k in annotation_kwargs:
            setattr(user, k, getattr(my_user, k))


@login_required
def users(request: AuthenticatedHttpRequest):
    users = list(User.objects.all().order_by(Lower("credits_name")))

    for key in ["authored", "editing", "factchecking"]:
        annotation_kwargs = {}
        annotation_kwargs[key + "_active"] = Count(
            key + "_puzzles",
            filter=~Q(
                **{
                    key
                    + "_puzzles__status__in": [
                        status.DEAD,
                        status.DEFERRED,
                        status.DONE,
                    ]
                }
            ),
            distinct=True,
        )
        annotation_kwargs[key + "_deferred"] = Count(
            key + "_puzzles",
            filter=Q(**{key + "_puzzles__status": status.DEFERRED}),
            distinct=True,
        )
        annotation_kwargs[key + "_dead"] = Count(
            key + "_puzzles",
            filter=Q(**{key + "_puzzles__status": status.DEAD}),
            distinct=True,
        )
        annotation_kwargs[key + "_done"] = Count(
            key + "_puzzles",
            filter=Q(**{key + "_puzzles__status": status.DONE}),
            distinct=True,
        )
        annotate_users_helper(users, annotation_kwargs)
    annotation_kwargs = {}
    annotation_kwargs["testsolving_done"] = Count(
        "testsolve_participations",
        filter=Q(testsolve_participations__ended__isnull=False),
        distinct=True,
    )
    annotation_kwargs["testsolving_in_progress"] = Count(
        "testsolve_participations",
        filter=Q(testsolve_participations__ended__isnull=True),
        distinct=True,
    )
    annotate_users_helper(users, annotation_kwargs)

    return render(
        request,
        "users.html",
        {
            "users": users,
        },
    )


@login_required
def users_statuses(request: AuthenticatedHttpRequest):
    # distinct=True because https://stackoverflow.com/questions/59071464/django-how-to-annotate-manytomany-field-with-count
    annotation_kwargs = {
        stat: Count(
            "authored_puzzles", filter=Q(authored_puzzles__status=stat), distinct=True
        )
        for stat in status.STATUSES
    }

    users = list(User.objects.all().annotate(**annotation_kwargs))
    for user in users:
        user.stats = [getattr(user, stat) for stat in status.STATUSES]

    return render(
        request,
        "users_statuses.html",
        {
            "users": users,
            "statuses": [status.DESCRIPTIONS[stat] for stat in status.STATUSES],
        },
    )


@login_required
def user(request: AuthenticatedHttpRequest, username: str):
    them = get_object_or_404(User, username=username)
    return render(
        request,
        "user.html",
        {
            "them": them,
            "testsolving_sessions": TestsolveSession.objects.filter(
                participations__user=them.id
            ),
        },
    )


@csrf_exempt
def preview_markdown(request):
    if request.method == "POST":
        output = render_to_string(
            "preview_markdown.html", {"input": request.body.decode("utf-8")}
        )
        return JsonResponse(
            {
                "success": True,
                "output": output,
            }
        )
    return JsonResponse(
        {
            "success": False,
            "error": "No markdown input received",
        }
    )


@csrf_exempt
def gdoc_html_preview(request):
    content = ""
    form = GDocHtmlPreviewForm()
    if request.method == "POST":
        form = GDocHtmlPreviewForm(request.POST)
        if form.is_valid():
            gdoc_id = guess_google_doc_id(form.cleaned_data["gdoc_url"])
            try:
                content = PuzzlePostprodForm.get_gdoc_html(gdoc_id)
            except ValidationError as e:
                content = e.message

    return render(
        request,
        "gdoc_export.html",
        {
            "form": form,
            "html_content": content,
        },
    )


@login_required
@group_required("EIC")
def discord_channels(request):
    return JsonResponse(data={"channels": discord.get_client().get_channels_in_guild()})
