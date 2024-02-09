import contextlib
import csv
import datetime
import json
import operator
import os
import secrets
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from functools import reduce
from typing import TypedDict

import requests
from django import forms, urls
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db.models import (
    Avg,
    BooleanField,
    Count,
    Exists,
    ExpressionWrapper,
    F,
    Max,
    OuterRef,
    Q,
    Subquery,
)
from django.db.models.functions import Lower
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils.http import urlencode
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from requests import HTTPError

import puzzle_editing.discord_integration as discord
from puzzle_editing import messaging, status, utils
from puzzle_editing import models as m
from puzzle_editing.discord.client import JsonDict
from puzzle_editing.forms import (
    AccountForm,
    AnswerForm,
    EditPostprodForm,
    GuessForm,
    LogisticsInfoForm,
    PuzzleAnswersForm,
    PuzzleCommentForm,
    PuzzleFactcheckForm,
    PuzzleHintForm,
    PuzzleInfoForm,
    PuzzleOtherCreditsForm,
    PuzzlePeopleForm,
    PuzzlePostprodForm,
    PuzzlePriorityForm,
    PuzzlePseudoAnswerForm,
    PuzzleTagForm,
    PuzzleTaggingForm,
    RegisterForm,
    RoundForm,
    SupportRequestAuthorNotesForm,
    SupportRequestStatusForm,
    SupportRequestTeamNotesForm,
    TestsolveFinderForm,
    TestsolveParticipantPicker,
    TestsolveParticipationForm,
    TestsolveSessionNotesForm,
    UserMultipleChoiceField,
    UserTimezoneForm,
)
from puzzle_editing.graph import curr_puzzle_graph_b64
from puzzle_editing.models import (
    CommentReaction,
    DiscordTextChannelCache,
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
    TestsolveGuess,
    TestsolveParticipation,
    TestsolveSession,
    User,
    get_user_role,
    is_author_on,
    is_editor_on,
    is_factchecker_on,
    is_postprodder_on,
    is_spoiled_on,
)
from settings.base import SITE_PASSWORD

from .view_helpers import (
    auto_postprodding_required,
    external_puzzle_url,
    group_required,
    require_testsolving_enabled,
)


class AuthenticatedHttpRequest(HttpRequest):
    user: User


def get_sessions_with_joined_and_current(user):
    return TestsolveSession.objects.annotate(
        joined=Exists(
            TestsolveParticipation.objects.filter(
                session=OuterRef("pk"),
                user=user,
            )
        ),
        current=Exists(
            TestsolveParticipation.objects.filter(
                session=OuterRef("pk"),
                user=user,
                ended=None,
            )
        ),
    )


def get_credits_name(user):
    return user.credits_name or user.display_name or user.username


def get_logistics_info(puzzle):
    def get_display_value(field: str, value) -> str | None:
        if value is None:
            return None

        widget = LogisticsInfoForm.Meta.widgets.get(field, None)
        choices = dict(getattr(widget, "choices", {}))
        if value in choices:
            return choices[value]

        if field == "logistics_specialized_type":
            # Special case to return display value based on this enum.
            return dict(Puzzle.SPECIALIZED_TYPES)[value or ""]

        return value

    return {
        field: get_display_value(field, getattr(puzzle, field, None))
        for field in LogisticsInfoForm.Meta.fields
    }


def index(request):
    user = request.user

    announcement = SiteSetting.get_setting("ANNOUNCEMENT")

    if not request.user.is_authenticated:
        return render(request, "index_not_logged_in.html")

    blocked_on_author_puzzles = Puzzle.objects.filter(
        authors=user,
        status__in=status.STATUSES_BY_BLOCKERS[status.AUTHORS_AND_EDITORS],
    )
    blocked_on_editor_puzzles = Puzzle.objects.filter(
        editors=user,
        status__in=status.STATUSES_BY_BLOCKERS[status.AUTHORS_AND_EDITORS],
    )
    current_user_sessions = TestsolveSession.objects.filter(
        participations__in=TestsolveParticipation.objects.filter(
            user=request.user, ended__isnull=True
        ).all()
    ).order_by("started")

    factchecking = Puzzle.objects.filter(
        status=status.STATUSES_BY_BLOCKERS[status.FACTCHECKERS], factcheckers=user
    )
    postprodding = Puzzle.objects.filter(
        status=status.STATUSES_BY_BLOCKERS[status.POSTPRODDERS], postprodders=user
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
            "current_user_sessions": current_user_sessions,
            "factchecking": factchecking,
            "inbox_puzzles": inbox_puzzles,
            "postprodding": postprodding,
        },
    )


@login_required
def docs(request):
    if not request.user.is_staff:
        raise PermissionDenied
    return render(request, "docs.html", {})


def process(request, doc="writing"):
    if not request.user.is_authenticated:
        return render(request, "process_not_logged_in.html")
    return render(request, "process.html", {"doc": doc})


def register(request):
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
def account(request):
    user = request.user
    if request.method == "POST":
        form = AccountForm(request.POST)
        if form.is_valid():
            user.email = form.cleaned_data["email"]
            user.display_name = form.cleaned_data["display_name"]
            user.bio = form.cleaned_data["bio"]
            user.credits_name = form.cleaned_data["credits_name"]
            user.save()
            return render(request, "account.html", {"form": form, "success": True})
        else:
            return render(request, "account.html", {"form": form, "success": None})
    else:
        form = AccountForm(
            initial={
                "email": user.email,
                "display_name": user.display_name,
                "credits_name": user.credits_name or user.display_name or user.username,
                "bio": user.bio,
            }
        )
        return render(request, "account.html", {"form": form, "success": None})


@login_required
def account_timezone(request):
    if request.method == "POST":
        form = UserTimezoneForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            return redirect("/account")

    form = UserTimezoneForm(instance=request.user)
    return render(
        request,
        "timezone.html",
        {
            "current_timezone": request.user.timezone
            if request.user.timezone
            else settings.TIME_ZONE,
            "form": form,
        },
    )


def format_discord_username(user: JsonDict) -> str:
    if user["discriminator"] != "0":
        return "{}#{}".format(user["username"], user["discriminator"])
    return user["username"]


# This endpoint handles both linking a Discord account to an existing user and
# logging in (or creating a new user) via Discord.
@require_GET
def oauth2_link_discord(request):
    if "error" in request.GET:
        messages.error(request, "Discord login failed: " + request.GET["error"])
        return redirect("/account")

    if "code" in request.GET:
        if (
            "state" not in request.GET
            or "discord_state" not in request.session
            or request.GET["state"] != request.session["discord_state"]
        ):
            messages.error(request, "Discord login failed: state mismatch")
            return redirect("/account")
        del request.session["discord_state"]

        payload = {
            "client_id": settings.DISCORD_CLIENT_ID,
            "client_secret": settings.DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": request.GET["code"],
            "redirect_uri": request.build_absolute_uri(
                urls.reverse("oauth2_link_discord")
            ),
            "scope": settings.DISCORD_OAUTH_SCOPES,
        }

        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        response = requests.post(
            "https://discord.com/api/oauth2/token", data=payload, headers=headers
        )
        response.raise_for_status()
        oauth_data = response.json()

        response = requests.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": "Bearer {}".format(oauth_data["access_token"])},
        )
        response.raise_for_status()
        user_data = response.json()

        if not user_data["verified"]:
            messages.error(
                request,
                "Discord login failed: you must have a verified email address to link your account.",
            )
            return redirect("/account")

        # Make sure the user is in the server
        response = requests.get(
            f"https://discord.com/api/v10/guilds/{settings.DISCORD_GUILD_ID}/members/{user_data["id"]}",
            headers={"Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}"},
        )
        if response.status_code != 200:
            messages.error(
                request,
                "Discord login failed: you're not in our Discord server. If you think you are, try going to https://discord.com/app and logging in, then try logging in here again.",
            )
            return redirect("/account")

        user = request.user
        if not user.is_authenticated:
            try:
                # first see if there's an existing user
                user = User.objects.get(discord_user_id=user_data["id"])
            except User.DoesNotExist:
                # if not create a new user
                user = User(
                    username=format_discord_username(user_data),
                    email=user_data["email"],
                )
                user.set_unusable_password()
                user.save()

            # Either way, log them in
            login(request, user)

        # Finally, capture Discord profile info on the user
        user.discord_user_id = user_data["id"]
        user.discord_username = format_discord_username(user_data)
        c = discord.get_client()
        if c:
            discord.init_perms(c, user)
            member = c.get_member_by_id(user.discord_user_id)
            if member:
                user.discord_nickname = member["nick"] or ""
        user.save()

        return redirect("/account")

    state = secrets.token_urlsafe()
    request.session["discord_state"] = state

    params = {
        "response_type": "code",
        "client_id": settings.DISCORD_CLIENT_ID,
        "state": state,
        "scope": settings.DISCORD_OAUTH_SCOPES,
        "redirect_uri": request.build_absolute_uri(urls.reverse("oauth2_link_discord")),
        "prompt": "none",
    }

    oauth_url = "https://discord.com/api/oauth2/authorize?" + urlencode(params)
    return redirect(oauth_url)


@login_required
@require_POST
def oauth2_unlink_discord(request):
    user = request.user
    if not SITE_PASSWORD:
        messages.error(
            request,
            "Discord link is required for all users.",
        )
        return redirect("/account")
    if user.discord_user_id or user.discord_username:
        user.discord_user_id = ""
        user.discord_username = ""
        user.save()

    return redirect("/account")


@login_required
def puzzle_new(request) -> HttpResponse:
    user = request.user

    if request.method == "POST":
        form = PuzzleInfoForm(user, request.POST)
        if form.is_valid():
            puzzle: Puzzle = form.save()

            if c := discord.get_client():
                discord.sync_puzzle_channel(c, puzzle)
                author_tags = discord.mention_users(puzzle.authors.all(), False)
                c.post_message(
                    puzzle.discord_channel_id,
                    f"This puzzle has been created in status **{status.get_display(puzzle.status)}**!\n"
                    f"Access it at {external_puzzle_url(request, puzzle)}\n"
                    f"Write your puzzle here: https://docs.google.com/document/d/{puzzle.content_google_doc_id}/edit\n"
                    f"Write your solution here: https://docs.google.com/document/d/{puzzle.solution_google_doc_id}/edit\n"
                    f"Keep any additional resources you need to help with writing here: https://drive.google.com/drive/folders/{puzzle.resource_google_folder_id}\n"
                    f"Author(s): {', '.join(author_tags)}",
                )
            add_comment(
                request=request,
                puzzle=puzzle,
                author=user,
                is_system=True,
                send_email=False,
                content="Created puzzle",
                status_change="II",
            )

            messages.info(
                request,
                f'This puzzle has been assigned the codename "{puzzle.codename}". You can use that instead of the title to refer to it without spoilers.',
            )

            return redirect(urls.reverse("puzzle", kwargs={"id": puzzle.id}))
        else:
            return render(request, "new.html", {"form": form})
    else:
        form = PuzzleInfoForm(request.user)
        return render(request, "new.html", {"form": form})


@login_required
def mine(request):
    puzzles = Puzzle.objects.filter(authors=request.user)
    editing_puzzles = Puzzle.objects.filter(editors=request.user)
    return render(
        request,
        "mine.html",
        {
            "puzzles": puzzles,
            "editing_puzzles": editing_puzzles,
        },
    )


@permission_required("puzzle_editing.list_puzzle", raise_exception=True)
def all_puzzles(request):
    puzzles = Puzzle.objects.all().prefetch_related("authors").order_by("id")
    return render(request, "all.html", {"puzzles": puzzles})


@permission_required("puzzle_editing.list_puzzle", raise_exception=True)
def bystatus(request) -> HttpResponse:
    all_puzzles = (
        Puzzle.objects.exclude(
            status__in=[
                status.INITIAL_IDEA,
                status.DEFERRED,
                status.DEAD,
            ]
        )
        .prefetch_related("authors", "tags")
        .order_by("name")
    )

    puzzles_by_status: dict[str, list[Puzzle]] = defaultdict(list)
    for puzzle in all_puzzles:
        puzzles_by_status[puzzle.status].append(puzzle)

    return render(
        request,
        "bystatus.html",
        {
            "puzzles": {
                status.get_display(s): puzzles
                for s, puzzles in puzzles_by_status.items()
            }
        },
    )


def add_comment(
    *,
    request,
    puzzle: Puzzle,
    author: User,
    is_system: bool,
    content: str,
    testsolve_session=None,
    send_email: bool = True,
    send_discord: bool = False,
    status_change: str = "",
    action_text: str = "posted a comment",
    is_feedback: bool = False,
):
    comment = PuzzleComment(
        puzzle=puzzle,
        author=author,
        testsolve_session=testsolve_session,
        is_system=is_system,
        is_feedback=is_feedback,
        content=content,
        status_change=status_change,
    )
    comment.save()

    if testsolve_session:
        subject = "New comment on {} (testsolve #{})".format(
            puzzle.spoiler_free_title(), testsolve_session.id
        )
        emails = testsolve_session.get_emails(exclude_emails=(author.email,))
    else:
        subject = f"New comment on {puzzle.spoiler_free_title()}"
        emails = puzzle.get_emails(exclude_emails=(author.email,))

    if send_email:
        messaging.send_mail_wrapper(
            subject,
            "new_comment_email",
            {
                "request": request,
                "puzzle": puzzle,
                "author": author,
                "content": content,
                "is_system": is_system,
                "testsolve_session": testsolve_session,
                "status_change": status.get_display(status_change)
                if status_change
                else None,
            },
            emails,
        )

    if send_discord and content and puzzle.discord_channel_id:
        c = discord.get_client()
        name = author.display_name or author.credits_name
        if author.discord_user_id:
            name = discord.mention_user(author.discord_user_id)
        discord.safe_post_message(
            c, puzzle.discord_channel_id, f"{name} ({action_text}):\n{content}"
        )


@permission_required("puzzle_editing.list_puzzle", raise_exception=True)
def all_hints(request: HttpRequest):
    return render(request, "all_hints.html", {"puzzles": Puzzle.objects.all()})


@login_required
def puzzle_hints(request: HttpRequest, id):
    puzzle: Puzzle = get_object_or_404(Puzzle, id=id)
    if request.method == "POST":
        form = PuzzleHintForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect(urls.reverse("puzzle_hints", kwargs={"id": id}))
        else:
            return render(
                request, "puzzle_hints.html", {"puzzle": puzzle, "hint_form": form}
            )

    return render(
        request,
        "puzzle_hints.html",
        {"hint_form": PuzzleHintForm(initial={"puzzle": puzzle}), "puzzle": puzzle},
    )


@login_required
def puzzle_other_credits(request: HttpRequest, id):
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
def puzzle_other_credit_update(request: HttpRequest, id, puzzle_id):
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
    puzzle: Puzzle = get_object_or_404(
        (
            Puzzle.objects.select_related("lead_author")
            .select_related("factcheck")
            .prefetch_related("spoiled")
            .prefetch_related("authors")
            .prefetch_related("editors")
            .prefetch_related("postprodders")
            .prefetch_related("factcheckers")
            .prefetch_related("pseudo_answers")
            .prefetch_related("hints")
            .prefetch_related("other_credits")
            .prefetch_related("tags")
        ),
        id=id,
    )
    if slug is None:
        new_slug = puzzle.slug
        if new_slug:
            return redirect(
                urls.reverse("puzzle_w_slug", kwargs={"id": id, "slug": new_slug})
            )

    user: User = request.user

    exclude = []
    if not user.has_perm("puzzle_editing.change_testsolvesession"):
        exclude.append("logistics_clean_testsolve_count")
    LogisticsForm = forms.modelform_factory(
        Puzzle, form=LogisticsInfoForm, exclude=exclude
    )

    vis, vis_created = PuzzleVisited.objects.get_or_create(puzzle=puzzle, user=user)
    if not vis_created:
        # update the auto_now=True DateTimeField anyway
        vis.save()

    def add_system_comment_here(message, status_change="", send_discord=False):
        add_comment(
            request=request,
            puzzle=puzzle,
            author=user,
            is_system=True,
            send_email=False,
            send_discord=send_discord,
            content=message,
            status_change=status_change,
        )

    def check_permission(perm):
        if not user.has_perm(perm):
            raise PermissionDenied

    if request.method == "POST":
        form: forms.Form | forms.ModelForm | None = None
        c = discord.get_client()
        channel_exists = DiscordTextChannelCache.objects.filter(
            id=puzzle.discord_channel_id
        ).exists()
        if c:
            if puzzle.discord_channel_id and not channel_exists:
                # If the puzzle has a channel_id but it doesn't exist, clear it
                # here to save time in the future.
                puzzle.discord_channel_id = ""
                puzzle.save()

        if "do_spoil" in request.POST:
            puzzle.spoiled.add(user)
        elif "subscribe-me" in request.POST:
            discord.set_puzzle_visibility(c, puzzle, user, True)
        elif "unsubscribe-me" in request.POST:
            discord.set_puzzle_visibility(c, puzzle, user, False)
        elif "resync-discord" in request.POST:
            discord.sync_puzzle_channel(c, puzzle)
        elif "change_status" in request.POST:
            check_permission("puzzle_editing.change_status_puzzle")
            new_status = request.POST["change_status"]
            if new_status != puzzle.status:
                puzzle.status = new_status
                puzzle.save()
                if c:
                    discord.sync_puzzle_channel(c, puzzle)
                    message = status.get_message_for_status(new_status, puzzle)
                    c.post_message(puzzle.discord_channel_id, message)

            add_system_comment_here("", status_change=new_status)

            if puzzle.status in [status.DEAD, status.DEFERRED]:
                puzzle.answers.clear()

            if new_status == status.TESTSOLVING:
                ### SEND CUSTOM EMAIL TO Testsolve Coordinators
                messaging.send_mail_wrapper(
                    f"✏️✏️✏️ {puzzle.spoiler_free_title()} ({puzzle.id})",
                    "emails/testsolving_time",
                    {
                        "request": request,
                        "puzzle": puzzle,
                        "user": user,
                        "logistics_info": get_logistics_info(puzzle),
                    },
                    User.get_testsolve_coordinators()
                    .exclude(email="")
                    .exclude(email__isnull=True)
                    .values_list("email", flat=True),
                )
            else:
                for session in puzzle.testsolve_sessions.filter(joinable=True):
                    session.joinable = False
                    add_comment(
                        request=request,
                        puzzle=puzzle,
                        author=user,
                        testsolve_session=session,
                        is_system=True,
                        send_email=False,
                        content="Puzzle status changed, automaticaly marking session as no longer joinable",
                    )
                    session.save()

        elif "change_priority" in request.POST:
            form = PuzzlePriorityForm(request.POST, instance=puzzle)
            if form.is_valid():
                form.save()
                add_system_comment_here(
                    "Priority changed to " + puzzle.get_priority_display()
                )
        elif "approve_flavor" in request.POST:
            puzzle.flavor_approved_time = datetime.datetime.now()
            puzzle.save()
            add_system_comment_here("Puzzle flavor approved")
        elif "unapprove_flavor" in request.POST:
            puzzle.flavor_approved_time = None
            puzzle.save()
            add_system_comment_here("Puzzle flavor unapproved")
        elif "add_author" in request.POST:
            puzzle.authors.add(user)
            discord.set_puzzle_visibility(c, puzzle, user, True)
            discord.announce_ppl(c, puzzle.discord_channel_id, authors=[user])
            add_system_comment_here("Added author " + str(user))
        elif "remove_author" in request.POST:
            puzzle.authors.remove(user)
            add_system_comment_here("Removed author " + str(user))
        elif "add_editor" in request.POST:
            check_permission("puzzle_editing.change_round")
            puzzle.editors.add(user)
            discord.set_puzzle_visibility(c, puzzle, user, True)
            discord.announce_ppl(c, puzzle.discord_channel_id, editors=[user])
            add_system_comment_here("Added editor " + str(user))
        elif "remove_editor" in request.POST:
            check_permission("puzzle_editing.change_round")
            puzzle.editors.remove(user)
            add_system_comment_here("Removed editor " + str(user))
        elif "add_factchecker" in request.POST:
            check_permission("puzzle_editing.change_puzzlefactcheck")
            puzzle.factcheckers.add(user)
            discord.sync_puzzle_channel(c, puzzle)
            discord.announce_ppl(c, puzzle.discord_channel_id, factcheckers=[user])
            add_system_comment_here("Added factchecker " + str(user))
        elif "remove_factchecker" in request.POST:
            check_permission("puzzle_editing.change_puzzlefactcheck")
            puzzle.factcheckers.remove(user)
            add_system_comment_here("Removed factchecker " + str(user))
        elif "add_postprodder" in request.POST:
            check_permission("puzzle_editing.change_puzzlepostprod")
            puzzle.postprodders.add(user)
            add_system_comment_here("Added postprodder " + str(user))
        elif "remove_postprodder" in request.POST:
            check_permission("puzzle_editing.change_puzzlepostprod")
            puzzle.postprodders.remove(user)
            add_system_comment_here("Removed postprodder " + str(user))
        elif "edit_logistics" in request.POST:
            form = LogisticsForm(request.POST, instance=puzzle)
            if form and form.is_valid():
                form.save()
                add_system_comment_here("Edited logistics info")
        elif "add_pseudo_answer" in request.POST:
            form = PuzzlePseudoAnswerForm(request.POST)
            if form.is_valid():
                form.save()
                add_system_comment_here("Added partial answer")
        elif "edit_postprod" in request.POST:
            form = EditPostprodForm(request.POST, instance=puzzle.postprod)
            if form.is_valid():
                form.save()
                add_system_comment_here("Edited puzzle postprod host url")
        elif "edit_factcheck" in request.POST:
            form = PuzzleFactcheckForm(request.POST, instance=puzzle.factcheck)
            if form.is_valid():
                form.save()
                add_system_comment_here(
                    "Updated factcheck output:\n" + puzzle.factcheck.output,
                    send_discord=True,
                )
        elif "add_hint" in request.POST:
            form = PuzzleHintForm(request.POST)
            if form.is_valid():
                form.save()
                add_system_comment_here("Added hint")
                return redirect(urls.reverse("puzzle_hints", args=[puzzle.id]))
            else:
                return render(
                    request, "puzzle_hints.html", {"puzzle": puzzle, "hint_form": form}
                )

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
                check_permission("puzzle_editing.change_status_puzzle")
                puzzle.status = status_change
                puzzle.save()
                if c:
                    discord.sync_puzzle_channel(c, puzzle)
                    message = status.get_message_for_status(status_change, puzzle)
                    c.post_message(puzzle.discord_channel_id, message)
            if comment_form.is_valid():
                add_comment(
                    request=request,
                    puzzle=puzzle,
                    author=user,
                    is_system=False,
                    send_email=True,
                    send_discord=True,
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

    unspoiled_users = (
        User.objects.exclude(pk__in=puzzle.spoiled.all())
        .exclude(groups__name="EIC")
        .filter(is_active=True)
        .exclude(testsolve_participations__session__puzzle=puzzle)
        .annotate(testsolve_count=Count("testsolve_participations"))
    )

    unspoiled = [
        u.display_name or u.credits_name or u.username for u in unspoiled_users
    ]
    unspoiled = sorted(unspoiled)

    if is_spoiled_on(user, puzzle):
        discord_status = "disabled"
        discord_channel = None
        discord_visible = False
        if c := discord.get_client():
            discord_status = "enabled"
            with contextlib.suppress(DiscordTextChannelCache.DoesNotExist):
                discord_channel = DiscordTextChannelCache.objects.get(
                    id=puzzle.discord_channel_id
                )
                for cached_overwrite in discord_channel.permission_overwrites:
                    overwrite = discord.PermissionOverwrite.from_cache(cached_overwrite)
                    if (
                        overwrite.entity == user.discord_user_id
                        and overwrite.permission.view_channel
                    ):
                        discord_visible = True
                        break

        comments = puzzle.comments.all()
        requests = (
            m.SupportRequest.objects.filter(puzzle=puzzle)
            .filter(Q(status="REQ") | Q(status="APP"))
            .all()
        )

        # TODO: participants is still hitting the database once per session;
        # might be possible to craft a Prefetch to get the list of
        # participants; or maybe we can abstract out the handrolled user list
        # logic and combine with the other views that do this

        # I inspected the query and Count with filter does become a SUM of CASE
        # expressions so it's using the same left join as everything else,
        # correctly for what we want
        testsolve_sessions = (
            TestsolveSession.objects.filter(puzzle=puzzle)
            .order_by("started")
            .annotate(
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
            .select_related("puzzle")
            .prefetch_related("participations__user")
            .prefetch_related("guesses")
        )
        is_author = is_author_on(user, puzzle)
        is_editor = is_editor_on(user, puzzle)
        can_manage_discord = (
            is_author or is_editor or user.has_perm("puzzle_editing.change_round")
        )
        can_unspoil = user.has_perm("puzzle_editing.unspoil_puzzle")

        return render(
            request,
            "puzzle.html",
            {
                "puzzle": puzzle,
                "discord": {
                    "status": discord_status,
                    "channel": discord_channel,
                    "visible": discord_visible,
                },
                "support_requests": requests,
                "comments": comments,
                "comment_form": PuzzleCommentForm(),
                "testsolve_sessions": testsolve_sessions,
                "all_statuses": status.ALL_STATUSES,
                "is_author": is_author,
                "is_editor": is_editor,
                "can_manage_discord": can_manage_discord,
                "can_unspoil": can_unspoil,
                "is_factchecker": is_factchecker_on(user, puzzle),
                "is_postprodder": is_postprodder_on(user, puzzle),
                "difficulty_form": LogisticsForm(instance=puzzle),
                "postprod_form": EditPostprodForm(instance=puzzle.postprod)
                if puzzle.has_postprod()
                else None,
                "factcheck_form": PuzzleFactcheckForm(instance=puzzle.factcheck)
                if puzzle.has_factcheck()
                else None,
                "pseudo_answer_form": PuzzlePseudoAnswerForm(
                    initial={"puzzle": puzzle}
                ),
                "priority_form": PuzzlePriorityForm(instance=puzzle),
                "hint_form": PuzzleHintForm(initial={"puzzle": puzzle}),
                "unspoiled": unspoiled,
                "logistics_info": get_logistics_info(puzzle),
            },
        )
    else:
        unspoiled_testsolve_sessions = (
            TestsolveSession.objects.filter(puzzle=puzzle)
            .order_by("started")
            .annotate(
                participation_count=Count("participations"),
                participation_done_count=Count(
                    "participations", filter=Q(participations__ended__isnull=False)
                ),
            )
        )
        comments = PuzzleComment.objects.filter(puzzle=puzzle, author=user)

        if (
            status.get_status_rank(puzzle.status)
            >= status.get_status_rank(status.NEEDS_POSTPROD)
            or user.is_testsolve_coordinator
        ):
            logistics_info = get_logistics_info(puzzle)
        else:
            logistics_info = {}

        return render(
            request,
            "puzzle_unspoiled.html",
            {
                "puzzle": puzzle,
                "role": get_user_role(user, puzzle),
                "comments": comments,
                "comment_form": PuzzleCommentForm(),
                "testsolve_sessions": unspoiled_testsolve_sessions,
                "is_in_testsolving": puzzle.status == status.TESTSOLVING,
                "status": status.get_display(puzzle.status),
                "unspoiled": unspoiled,
                "difficulty_form": LogisticsInfoForm(instance=puzzle),
                "logistics_info": logistics_info,
            },
        )


@permission_required("puzzle_editing.change_round", raise_exception=True)
def puzzle_answers(request, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    user = request.user
    spoiled = is_spoiled_on(user, puzzle)

    if request.method == "POST":
        form = PuzzleAnswersForm(user, request.POST, instance=puzzle)
        if form.is_valid():
            form.save()

            answers = form.cleaned_data["answers"]
            if answers:
                if len(answers) == 1:
                    comment = "Assigned answer " + answers[0].answer
                else:
                    comment = "Assigned answers " + ", ".join(
                        answer.answer for answer in answers
                    )
            else:
                comment = "Unassigned answer"

            add_comment(
                request=request,
                puzzle=puzzle,
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
def puzzle_tags(request, id):
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

            add_comment(
                request=request,
                puzzle=puzzle,
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
@auto_postprodding_required
@permission_required("puzzle_editing.change_puzzlepostprod", raise_exception=True)
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
                pp.host_url = settings.POSTPROD_BRANCH_URL.format(slug=pp.slug)
            branch = utils.export_puzzle(
                pp,
                puzzle_directory=puzzle_directory,
                puzzle_html=puzzle_html,
                solution_html=solution_html,
                max_image_width=max_image_width,
            )
            if branch:
                messages.success(
                    request,
                    f"Successfully pushed commit to {settings.HUNT_REPO_URL} ({branch})",
                )
            else:
                messages.error(
                    request,
                    "Failed to commit new changes. Please contact @web if this is not expected.",
                )
                return redirect(urls.reverse("puzzle_postprod", args=[id]))

            pp.save()
            add_comment(
                request=request,
                puzzle=puzzle,
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
                    "form": form,
                    "spoiled": spoiled,
                },
            )

    elif puzzle.has_postprod():
        form = PuzzlePostprodForm(instance=puzzle.postprod)
    else:
        default_slug = slugify(puzzle.name.lower())
        authors = [get_credits_name(user) for user in puzzle.authors.all()]
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
            "form": form,
            "spoiled": spoiled,
        },
    )


@login_required
@auto_postprodding_required
@permission_required("puzzle_editing.change_puzzlepostprod", raise_exception=True)
def puzzle_postprod_metadata(request, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    authors = [get_credits_name(u) for u in puzzle.authors.all()]
    authors.sort(key=lambda a: a.upper())

    metadata = JsonResponse(puzzle.metadata)

    metadata["Content-Disposition"] = 'attachment; filename="metadata.json"'

    return metadata


@login_required
@auto_postprodding_required
@permission_required("puzzle_editing.change_puzzlepostprod", raise_exception=True)
def puzzle_yaml(request, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    return HttpResponse(puzzle.get_yaml_fixture(), content_type="text/plain")


@auto_postprodding_required
@permission_required(
    ["puzzle_editing.change_puzzlepostprod", "puzzle_editing.change_round"],
    raise_exception=True,
)
def export(request):
    output = ""
    if request.method == "POST" and "export" in request.POST:
        branch_name = utils.export_all()
        output = (
            f"Successfully exported all metadata to {settings.HUNT_REPO_URL} ({branch_name})"
            if branch_name
            else "Failed to export. Please report this issue to tech."
        )

    return render(request, "export.html", {"output": output})


@login_required
@auto_postprodding_required
@permission_required("puzzle_editing.change_puzzlepostprod", raise_exception=True)
def check_metadata(request):
    puzzleFolder = settings.HUNT_REPO / "hunt/data/puzzle"
    mismatches = []
    credits_mismatches = []
    notfound = []
    exceptions = []
    for puzzledir in os.listdir(puzzleFolder):
        datafile = puzzleFolder / puzzledir / "metadata.json"
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
def puzzle_edit(request, id) -> HttpResponse:
    puzzle = get_object_or_404(Puzzle, id=id)
    user = request.user

    if request.method == "POST":
        form = PuzzleInfoForm(user, request.POST, instance=puzzle)
        if form.is_valid():
            new_authors = None
            if "authors" in form.changed_data:
                old_authors = set(puzzle.authors.all())
                new_authors = set(form.cleaned_data["authors"]) - old_authors
            form.save()

            if form.changed_data:
                add_comment(
                    request=request,
                    puzzle=puzzle,
                    author=user,
                    is_system=True,
                    send_email=False,
                    content=get_changed_data_message(form),
                )
                c = discord.get_client()
                discord.sync_puzzle_channel(c, puzzle)
                if new_authors:
                    discord.announce_ppl(
                        c, puzzle.discord_channel_id, authors=new_authors
                    )

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
def puzzle_people(request, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    user = request.user

    exclude = []
    if not user.has_perm("puzzle_editing.unspoil_puzzle"):
        exclude.append("spoiled")
    if not user.has_perm("puzzle_editing.change_round"):
        exclude.append("editors")
    if not user.has_perm("puzzle_editing.change_puzzlefactcheck"):
        exclude.append("factcheckers")
    if not user.has_perm("puzzle_editing.change_puzzlepostprod"):
        exclude.append("postprodders")
    PeopleForm = forms.modelform_factory(
        Puzzle,
        form=PuzzlePeopleForm,
        exclude=exclude,
    )

    if request.method == "POST":
        form = PeopleForm(request.POST, instance=puzzle)
        if form.is_valid():
            added = {}
            if form.changed_data:
                for key in ["authors", "editors", "factcheckers"]:
                    if key not in form.cleaned_data:
                        continue
                    added[key] = set(form.cleaned_data[key]) - set(
                        getattr(puzzle, key).all()
                    )
            form.save()
            if added:
                c = discord.get_client()
                discord.sync_puzzle_channel(c, puzzle)
                discord.announce_ppl(c, puzzle.discord_channel_id, **added)

            if form.changed_data:
                add_comment(
                    request=request,
                    puzzle=puzzle,
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
            "form": PeopleForm(instance=puzzle),
        }

    return render(request, "puzzle_people.html", context)


@permission_required("puzzle_editing.unspoil_puzzle", raise_exception=True)
def puzzle_escape(request, id) -> HttpResponse:
    puzzle: Puzzle = get_object_or_404(Puzzle, id=id)
    user: User = request.user

    if request.method == "POST":
        if "unspoil" in request.POST:
            puzzle.spoiled.remove(user)
            if user.discord_user_id:
                c = discord.get_client()
                if c:
                    try:
                        c.delete_channel_permission(
                            puzzle.discord_channel_id, user.discord_user_id
                        )
                    except HTTPError as e:
                        if e.response.status_code != 404:
                            # The user isn't in the channel, so we don't need to remove them
                            raise
            add_comment(
                request=request,
                puzzle=puzzle,
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
def edit_comment(request, id):
    comment = get_object_or_404(PuzzleComment, id=id)

    if request.user != comment.author:
        return render(
            request,
            "edit_comment.html",
            {
                "comment": comment,
                "not_author": True,
                "is_system": comment.is_system,
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

            return redirect(urls.reverse("edit_comment", args=[id]))
        else:
            return render(
                request,
                "edit_comment.html",
                {"comment": comment, "form": form, "is_system": comment.is_system},
            )

    return render(
        request,
        "edit_comment.html",
        {
            "comment": comment,
            "form": PuzzleCommentForm(
                {"content": comment.content, "is_system": comment.is_system}
            ),
        },
    )


@login_required
def edit_hint(request, id):
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
@require_testsolving_enabled
def testsolve_history(request):
    past_sessions = TestsolveSession.objects.filter(
        participations__in=TestsolveParticipation.objects.filter(
            user=request.user, ended__isnull=False
        ).all()
    ).order_by("started")

    context = {
        "past_sessions": past_sessions,
    }
    return render(request, "testsolve_history.html", context)


@require_testsolving_enabled
@login_required
def testsolve_main(request):
    user = request.user

    if request.method == "POST":
        if "start_session" in request.POST:
            puzzle_id = request.POST["start_session"]
            puzzle = get_object_or_404(Puzzle, id=puzzle_id)
            session = TestsolveSession(puzzle=puzzle)
            session.save()

            participation = TestsolveParticipation(session=session, user=user)
            participation.save()

            add_comment(
                request=request,
                puzzle=puzzle,
                author=user,
                is_system=True,
                send_email=False,
                content=f"Created testsolve session #{session.id}",
                testsolve_session=session,
            )

            return redirect(urls.reverse("testsolve_one", args=[session.id]))

    current_user_sessions = TestsolveSession.objects.filter(
        participations__in=TestsolveParticipation.objects.filter(
            user=request.user, ended__isnull=True
        ).all()
    ).order_by("started")

    joinable_sessions = (
        TestsolveSession.objects.exclude(
            pk__in=TestsolveSession.objects.filter(
                participations__in=TestsolveParticipation.objects.filter(
                    user=request.user
                ).all()
            ).all()
        )
        .filter(joinable=True)
        .order_by("started")
    )

    testsolvable_puzzles = (
        Puzzle.objects.filter(status=status.TESTSOLVING)
        .annotate(
            is_author=Exists(
                User.objects.filter(authored_puzzles=OuterRef("pk"), id=user.id)
            ),
            is_spoiled=Exists(
                User.objects.filter(spoiled_puzzles=OuterRef("pk"), id=user.id)
            ),
            in_session=Exists(current_user_sessions.filter(puzzle=OuterRef("pk"))),
            has_session=Exists(joinable_sessions.filter(puzzle=OuterRef("pk"))),
        )
        .order_by("priority")
        .prefetch_related(
            "authors",
            "editors",
            "tags",
        )
    )

    testsolvable = [
        {
            "puzzle": puzzle,
            "warning": warn_about_testsolving(
                puzzle.is_spoiled, puzzle.in_session, puzzle.has_session
            ),
        }
        for puzzle in testsolvable_puzzles
    ]

    is_testsolve_coordinator = request.user in User.get_testsolve_coordinators()

    all_current_sessions = None
    if is_testsolve_coordinator:
        all_current_sessions = (
            TestsolveSession.objects.filter(
                Exists(
                    TestsolveParticipation.objects.filter(
                        ended__isnull=True, session=OuterRef("pk")
                    )
                )
            )
            .order_by("started")
            .prefetch_related("participations")
        )

    context = {
        "current_user_sessions": current_user_sessions,
        "joinable_sessions": joinable_sessions,
        "testsolvable": testsolvable,
        "is_testsolve_coordinator": is_testsolve_coordinator,
        "all_current_sessions": all_current_sessions,
    }

    return render(request, "testsolve_main.html", context)


@login_required
def my_spoiled(request):
    spoiled = request.user.spoiled_puzzles.all()

    context = {"spoiled": spoiled}
    return render(request, "my_spoiled.html", context)


@login_required
@require_testsolving_enabled
def testsolve_finder(request) -> HttpResponse:
    @dataclass
    class PuzzleData:
        puzzle: Puzzle
        user_data: list[str]
        unspoiled_count: int

    solvers = request.GET.getlist("solvers")
    users = User.objects.filter(pk__in=solvers) if solvers else None
    if users:
        testsolveable_puzzles = list(
            Puzzle.objects.filter(status=status.TESTSOLVING).order_by("priority")
        )
        puzzle_data: list[PuzzleData] = []
        for puzzle in testsolveable_puzzles:
            puzzle_data.append(
                PuzzleData(puzzle=puzzle, user_data=[], unspoiled_count=0)
            )
        for user in users:
            authored_ids = set(user.authored_puzzles.values_list("id", flat=True))
            editor_ids = set(user.editing_puzzles.values_list("id", flat=True))
            spoiled_ids = set(user.spoiled_puzzles.values_list("id", flat=True))
            for pdata in puzzle_data:
                if pdata.puzzle.id in authored_ids:
                    pdata.user_data.append("📝 Author")
                elif pdata.puzzle.id in editor_ids:
                    pdata.user_data.append("💬 Editor")
                elif pdata.puzzle.id in spoiled_ids:
                    pdata.user_data.append("👀 Spoiled")
                else:
                    pdata.user_data.append("❓ Unspoiled")
                    pdata.unspoiled_count += 1

        puzzle_data.sort(key=lambda pdata: -pdata.unspoiled_count)
    else:
        puzzle_data = []

    form = TestsolveFinderForm(solvers or request.user)

    return render(
        request,
        "testsolve_finder.html",
        {"puzzle_data": puzzle_data, "solvers": solvers, "form": form, "users": users},
    )


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
def testsolve_csv(request, id):
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
def testsolve_participants(request, id):
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
@require_testsolving_enabled
def testsolve_one(request, id) -> HttpResponse:
    session = get_object_or_404(
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
    testsolve_adder_form = TestsolveParticipantPicker(None, current_testers)

    if request.method == "POST":
        if "join" in request.POST:
            if not TestsolveParticipation.objects.filter(
                session=session, user=user
            ).exists():
                new_participation = TestsolveParticipation()
                new_participation.session = session
                new_participation.user = user
                new_participation.save()

                add_comment(
                    request=request,
                    puzzle=puzzle,
                    author=user,
                    testsolve_session=session,
                    is_system=True,
                    send_email=False,
                    content=f"Joined testsolve session #{session.id}",
                )

        elif "edit_notes" in request.POST:
            notes_form = TestsolveSessionNotesForm(request.POST, instance=session)
            if notes_form.is_valid():
                notes_form.save()

        elif "do_guess" in request.POST:
            # Ensure the user is a participant
            get_object_or_404(
                TestsolveParticipation,
                session=session,
                user=user,
            )
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
                c = discord.get_client()
                guess_comment = f"Correct answer: {guess}."

                if session.joinable:
                    guess_comment += (
                        " Automatically marking session as no longer joinable."
                    )

                    session.joinable = False
                    session.save()

                    # Send a congratulatory message to the thread.
                    discord.safe_post_message(
                        c,
                        session.discord_thread_id,
                        f":tada: Congratulations on solving this puzzle! :tada:\nTime since testsolve started: {session.time_since_started}",
                    )
            else:
                # Guess might still be partially correct
                for answer in session.puzzle.pseudo_answers.all():
                    if answer.is_correct(guess):
                        partially_correct = True
                        partial_response = answer.response
                        guess_comment = (
                            f"Guessed: {guess}. Response: {partial_response}"
                        )
                        break

                if not partially_correct:
                    guess_comment = f"Incorrect answer: {guess}."

            guess_model = TestsolveGuess(
                session=session,
                user=user,
                guess=guess,
                correct=correct,
                partially_correct=partially_correct,
                partial_response=partial_response,
            )
            guess_model.save()

            add_comment(
                request=request,
                puzzle=puzzle,
                author=user,
                testsolve_session=session,
                is_system=True,
                send_email=False,
                content=guess_comment,
            )

        elif "change_joinable" in request.POST:
            session.joinable = request.POST["change_joinable"] == "1"
            session.save()

        elif "add_comment" in request.POST:
            comment_form = PuzzleCommentForm(request.POST)
            if comment_form.is_valid():
                add_comment(
                    request=request,
                    puzzle=puzzle,
                    author=user,
                    testsolve_session=session,
                    is_system=False,
                    send_email=True,
                    content=comment_form.cleaned_data["content"],
                )

        elif "react_comment" in request.POST:
            emoji = request.POST.get("emoji")
            react_comment = PuzzleComment.objects.get(id=request.POST["react_comment"])
            # This just lets you react with any string to a comment, but it's
            # not the end of the world.
            if emoji and react_comment:
                CommentReaction.toggle(emoji, react_comment, user)

        elif "add_testsolvers" in request.POST:
            new_testers = User.objects.filter(
                pk__in=request.POST.getlist("add_testsolvers")
            )
            for new_tester in new_testers:
                if not TestsolveParticipation.objects.filter(
                    session=session, user=new_tester
                ).exists():
                    TestsolveParticipation(session=session, user=new_tester).save()

        elif "get_help" in request.POST:
            ### SEND CUSTOM EMAIL TO Testsolve Coordinators
            messaging.send_mail_wrapper(
                f"✏️✏️✏️ {puzzle.spoiler_free_title()} ({puzzle.id}) is stuck!",
                "emails/testsolving_stuck",
                {
                    "request": request,
                    "puzzle": puzzle,
                    "user": user,
                    "session": session,
                    "logistics_info": get_logistics_info(puzzle),
                },
                User.get_testsolve_coordinators()
                .exclude(email="")
                .exclude(email__isnull=True)
                .values_list("email", flat=True),
            )
            c = discord.get_client()
            discord.safe_post_message(
                c,
                session.discord_thread_id,
                "Don't worry - help is on the way! You have successfully requested for help. The Testsolve Coordinators will reach out when they've found someone.",
            )

        # refresh
        return redirect(urls.reverse("testsolve_one", args=[id]))

    try:
        participation = TestsolveParticipation.objects.get(session=session, user=user)
    except TestsolveParticipation.DoesNotExist:
        participation = None

    spoiled = is_spoiled_on(user, puzzle)
    answers_exist = puzzle.answers.exists()
    comments = session.comments.filter(puzzle=puzzle)
    is_solved = session.has_correct_guess()

    if not spoiled:
        comments = comments.filter(is_feedback=False)

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
        "testsolve_adder_form": testsolve_adder_form,
        "true_participants": true_participants,
        "user_is_hidden_from_list": user_is_participant,
        "discord_guild_id": settings.DISCORD_GUILD_ID,
    }

    return render(request, "testsolve_one.html", context)


@require_POST
@permission_required("puzzle_editin.unspoil_puzzle", raise_exception=True)
@require_testsolving_enabled
def testsolve_escape(request, id):
    participation = get_object_or_404(
        TestsolveParticipation,
        session=id,
        user=request.user,
    )
    participation.delete()
    return redirect(urls.reverse("testsolve_main"))


@login_required
@require_testsolving_enabled
def testsolve_feedback(request, id):
    session = get_object_or_404(TestsolveSession, id=id)

    feedback = session.participations.filter(ended__isnull=False)
    no_feedback = session.participations.filter(ended__isnull=True)

    context = {
        "session": session,
        "no_feedback": no_feedback,
        "feedback": feedback,
        "participants": len(feedback),
        "title": f"Testsolving Feedback - {session.puzzle}",
        "bulk": False,
    }

    return render(request, "testsolve_feedback.html", context)


@login_required
def puzzle_feedback(request, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    feedback = (
        TestsolveParticipation.objects.filter(session__puzzle=puzzle)
        .filter(ended__isnull=False)
        .select_related("session")
        .order_by("session__id")
    )

    context = {
        "puzzle": puzzle,
        "feedback": feedback,
        "title": f"Testsolve Feedback for {puzzle.spoilery_title}",
        "bulk": True,
    }

    return render(request, "testsolve_feedback.html", context)


@login_required
def puzzle_feedback_all(request):
    feedback = (
        TestsolveParticipation.objects.filter(ended__isnull=False)
        .select_related("session")
        .order_by("session__puzzle__id", "session__id")
    )

    context = {"feedback": feedback, "title": "All Testsolve Feedback", "bulk": True}

    return render(request, "testsolve_feedback.html", context)


@login_required
def puzzle_feedback_csv(request, id):
    puzzle = get_object_or_404(Puzzle, id=id)
    feedback = (
        TestsolveParticipation.objects.filter(session__puzzle=puzzle)
        .filter(ended__isnull=False)
        .select_related("session")
        .order_by("session__id")
    )

    return HttpResponse(testsolve_queryset_to_csv(feedback), content_type="text/csv")


@login_required
def puzzle_feedback_all_csv(request):
    feedback = (
        TestsolveParticipation.objects.filter(ended__isnull=False)
        .select_related("session")
        .order_by("session__puzzle__id", "session__id")
    )

    return HttpResponse(testsolve_queryset_to_csv(feedback), content_type="text/csv")


@login_required
@require_testsolving_enabled
def testsolve_finish(request, id) -> HttpResponse:
    session = get_object_or_404(TestsolveSession, id=id)
    puzzle = session.puzzle
    user = request.user

    try:
        participation = TestsolveParticipation.objects.get(session=session, user=user)
    except TestsolveParticipation.DoesNotExist:
        participation = None

    if request.method == "POST" and participation:
        completed_form = TestsolveParticipationForm(
            request.POST,
            instance=participation,
        )
        already_spoiled = is_spoiled_on(user, puzzle)
        if completed_form.is_valid():
            fun = completed_form.cleaned_data["fun_rating"] or None
            difficulty = completed_form.cleaned_data["difficulty_rating"] or None
            hours_spent = completed_form.cleaned_data["hours_spent"] or None

            if already_spoiled:
                spoil_message = "(solver was already spoiled)"
            else:
                spoil_message = "❌ solver was not spoiled"

            ratings_text = "Fun: {} / Difficulty: {} / Hours spent: {} / {}".format(
                fun or "n/a", difficulty or "n/a", hours_spent or "n/a", spoil_message
            )

            # Post a comment and send to discord when first finished.
            if not participation.ended:
                comment_content = "\n\n".join(
                    filter(
                        None,
                        [
                            "Finished testsolve",
                            completed_form.cleaned_data["general_feedback"],
                            completed_form.cleaned_data.get("misc_feedback"),
                            ratings_text,
                        ],
                    )
                )
                add_comment(
                    request=request,
                    puzzle=puzzle,
                    author=user,
                    testsolve_session=session,
                    is_system=False,
                    is_feedback=True,
                    send_email=False,
                    send_discord=True,
                    content=comment_content,
                    action_text="finished a testsolve with comment",
                )
            participation.ended = datetime.datetime.now()
            participation.save()

            return redirect(urls.reverse("testsolve_one", args=[id]))
        else:
            context = {
                "session": session,
                "participation": participation,
                "form": completed_form,
            }

            return render(request, "testsolve_finish.html", context)

    form = TestsolveParticipationForm(instance=participation) if participation else None

    context = {
        "session": session,
        "participation": participation,
        "form": form,
    }

    return render(request, "testsolve_finish.html", context)


@login_required
@permission_required("puzzle_editing.change_puzzlepostprod", raise_exception=True)
def postprod(request):
    postprodding = Puzzle.objects.filter(
        status=status.NEEDS_POSTPROD,
        postprodders=request.user,
    )
    needs_postprod = Puzzle.objects.annotate(
        has_postprodder=Exists(User.objects.filter(postprodding_puzzles=OuterRef("pk")))
    ).filter(status=status.NEEDS_POSTPROD, has_postprodder=False)

    context = {
        "postprodding": postprodding,
        "needs_postprod": needs_postprod,
    }
    return render(request, "postprod.html", context)


@login_required
def postprod_all(request):
    needs_postprod = (
        Puzzle.objects.filter(
            status__in=[
                status.NEEDS_POSTPROD,
                status.AWAITING_POSTPROD_APPROVAL,
                status.NEEDS_FACTCHECK,
                status.NEEDS_FINAL_REVISIONS,
                status.NEEDS_FINAL_DAY_FACTCHECK,
                # status.NEEDS_HINTS,
                # status.AWAITING_HINTS_APPROVAL,
            ]
        )
        .prefetch_related("tags")
        .prefetch_related("postprodders")
    )

    sorted_puzzles = sorted(
        needs_postprod, key=lambda a: (status.STATUSES.index(a.status), a.name)
    )

    context = {
        "puzzles": sorted_puzzles,
    }
    return render(request, "postprod_all.html", context)


@login_required
@permission_required("puzzle_editing.change_puzzlefactcheck", raise_exception=True)
def factcheck(request):
    factchecking = Puzzle.objects.filter(
        status__in=(
            status.NEEDS_FACTCHECK,
            status.NEEDS_FINAL_DAY_FACTCHECK,
        ),
        factcheckers=request.user,
    )
    needs_factcheck = Puzzle.objects.annotate(
        has_factchecker=Exists(User.objects.filter(factchecking_puzzles=OuterRef("pk")))
    ).filter(status=status.NEEDS_FACTCHECK)

    needs_final_day_factcheck = Puzzle.objects.filter(
        status=status.NEEDS_FINAL_DAY_FACTCHECK
    )

    context = {
        "factchecking": factchecking,
        "needs_factchecking": needs_factcheck,
        "needs_final_day_factcheck": needs_final_day_factcheck,
    }
    return render(request, "factcheck.html", context)


@login_required
@group_required("EIC", "Editor", "Art")
def flavor(request):
    needs_flavor = Puzzle.objects.filter(
        flavor="", flavor_approved_time__isnull=True
    ).prefetch_related("answers__round")
    needs_flavor_approved = (
        Puzzle.objects.exclude(flavor="")
        .filter(flavor_approved_time__isnull=True)
        .prefetch_related("answers__round")
    )
    has_flavor_approved = Puzzle.objects.filter(
        flavor_approved_time__isnull=False
    ).prefetch_related("answers__round")

    context = {
        "has_flavor_approved": has_flavor_approved,
        "needs_flavor": needs_flavor,
        "needs_flavor_approved": needs_flavor_approved,
    }
    return render(request, "flavor.html", context)


@group_required("EIC")
def eic(request, template="awaiting_editor.html"):
    def puzzles_for_status(status: str) -> Iterable[Puzzle]:
        return (
            Puzzle.objects.filter(status=status)
            .order_by("status_mtime")
            .prefetch_related("answers", "tags", "authors")
        )

    return render(
        request,
        template,
        {
            "awaiting_answer": puzzles_for_status(status.AWAITING_ANSWER),
            "awaiting_answer_flexible": puzzles_for_status(
                status.AWAITING_ANSWER_FLEXIBLE
            ),
            "initial_idea": puzzles_for_status(status.INITIAL_IDEA),
        },
    )


@group_required("EIC")
def eic_overview(request) -> HttpResponse:
    def sort_key(t: tuple[Puzzle, PuzzleAnswer | None, datetime.datetime | None]):
        p, a, _ = t
        # answer/round before no answer/round
        round_present_key = 0 if a else 1
        round_key = a.round.name if a else None
        # metas before feeders
        meta_key = 0 if p.is_meta else 1

        return (round_present_key, round_key, meta_key, -p.get_status_rank(), p.name)

    last_visits = dict(PuzzleVisited.objects.values_list("puzzle", "date"))
    last_comments = dict(
        Puzzle.objects.exclude(comments__isnull=True).values_list(
            "id", Max("comments__date")
        )
    )
    unread = {
        id
        for id in set(last_comments.keys())
        if id not in last_visits or last_comments[id] > last_visits[id]
    }

    puzzles: list[tuple[Puzzle, PuzzleAnswer | None, datetime.datetime | None]] = []

    puzzle_query = Puzzle.objects.prefetch_related(
        "answers__round", "authors", "editors", "tags"
    ).exclude(status__in=(status.DEFERRED, status.DEAD))
    for p in puzzle_query:
        answers = list(p.answers.all())
        if answers:
            for a in answers:
                puzzles.append((p, a, last_comments.get(p.id)))
        else:
            puzzles.append((p, None, last_comments.get(p.id)))

    return render(
        request,
        "eic_overview.html",
        {
            "puzzles": sorted(puzzles, key=sort_key),
            "unread": unread,
        },
    )


@group_required("EIC")
def editor_overview(request) -> HttpResponse:
    active_statuses = [
        status.INITIAL_IDEA,
        status.IN_DEVELOPMENT,
        status.AWAITING_ANSWER,
        status.WRITING,
        status.WRITING_FLEXIBLE,
        status.TESTSOLVING,
        status.NEEDS_SOLUTION,
        status.NEEDS_POSTPROD,
        status.AWAITING_POSTPROD_APPROVAL,
    ]

    puzzle_editors = (
        User.objects.exclude(editing_puzzles__isnull=True)
        .annotate(num_editing=Count("editing_puzzles"))
        .order_by("id")
    )

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

    class CountsByEditor(TypedDict):
        active: int
        with_drafts: int
        testsolved: int

    counts_by_editor = defaultdict[int, CountsByEditor](
        lambda: {
            "active": 0,
            "with_drafts": 0,
            "testsolved": 0,
        }
    )
    for p in active_puzzles:
        has_draft = status.past_writing(p.status)
        testsolved = status.past_testsolving(p.status)
        for pe in p.editors.all():
            counts_by_editor[pe.id]["active"] += 1
            if has_draft:
                counts_by_editor[pe.id]["with_drafts"] += 1
            if testsolved:
                counts_by_editor[pe.id]["testsolved"] += 1

    actively_editing = [(e.id, counts_by_editor[e.id]) for e in puzzle_editors]

    context = {
        "editors": puzzle_editors,
        "actively_editing": actively_editing,
        "editored_puzzles": editored_puzzles,
    }
    return render(request, "editor_overview.html", context)


@login_required
def needs_editor(request):
    needs_editors = Puzzle.objects.annotate(
        remaining_des=(F("needed_editors") - Count("editors"))
    ).filter(remaining_des__gt=0)

    context = {"needs_editors": needs_editors}
    return render(request, "needs_editor.html", context)


@permission_required("puzzle_editing.list_puzzle", raise_exception=True)
def byround(request):
    round_objs = Round.objects.all()
    round_objs = (
        round_objs.order_by(Lower("name"))
        .prefetch_related("editors")
        .prefetch_related("answers__puzzles__authors")
        .prefetch_related("answers__puzzles__postprod")
    )

    spoiled_answer_ids = set(
        request.user.spoiled_puzzles.filter(answers__isnull=False).values_list(
            "answers", flat=True
        )
    )
    spoiled_answer_ids.update(
        request.user.spoiled_rounds.filter(answers__isnull=False).values_list(
            "answers", flat=True
        )
    )

    rounds = []
    for round in round_objs:
        answers = sorted(round.answers.all(), key=lambda a: a.answer.lower())
        num_unspoiled = len(answers) - len(
            [answer for answer in answers if answer.id in spoiled_answer_ids]
        )

        rounds.append(
            {
                "id": round.id,
                "name": round.name,
                "description": round.description,
                "spoiled": round.spoiled.contains(request.user),
                "num_unspoiled": num_unspoiled,
                "answers": [
                    answer.to_json()
                    for answer in answers
                    if answer.id in spoiled_answer_ids
                ],
                "editors": list(round.editors.all()),
            }
        )

    unassigned = (
        Puzzle.objects.filter(answers__isnull=True)
        .exclude(status__in=(status.DEFERRED, status.DEAD))
        .order_by("name")
    )

    return render(
        request,
        "byround.html",
        {
            "rounds": rounds,
            "unassigned": unassigned,
        },
    )


@permission_required("puzzle_editing.change_round", raise_exception=True)
def rounds(request, id=None):
    user = request.user

    new_round_form = RoundForm()
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
                answer.to_json()
                for answer in round.answers.all()
                .order_by(Lower("answer"))
                .prefetch_related("puzzles")
            ],
            "form": AnswerForm(round),
            "editors": round.editors.all().order_by(Lower("display_name")),
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


@permission_required("puzzle_editing.change_round", raise_exception=True)
def edit_round(request, id):
    round = get_object_or_404(Round, id=id)
    if request.method == "POST":
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
def support_all(request):
    user = request.user
    team = request.GET.get("team", "ALL")

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

    # Only show the ones this user is spoiled on, or that matches the filters.
    is_spoiled = User.objects.filter(
        Q(spoiled_puzzles=OuterRef("puzzle"))
        | Q(assigned_support_requests=OuterRef("pk")),
        pk=user.id,
    )
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
            "hidden_count": all_open_requests.count()
            + all_closed_requests.count()
            - open_requests.count()
            - closed_requests.count(),
            "type": "all",
            "team": team,
        },
    )


@login_required
def support_by_puzzle(request, id):
    """Show all requests for a puzzle or create one"""
    puzzle = get_object_or_404(Puzzle, id=id)
    support = []
    for team, team_name in SupportRequest.Team.choices:
        support.append(
            {
                "obj": SupportRequest.objects.filter(team=team)
                .filter(puzzle=puzzle)
                .first(),
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
def support_by_puzzle_id(request, id, team):
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
                    datetime.UTC
                ).astimezone()
                support.save()
                new_notes = support.team_notes
                messaging.send_mail_wrapper(
                    f"{support.get_team_display()} team support request update for {support.puzzle.name}",
                    "emails/support_update",
                    {
                        "request": request,
                        "support": support,
                        "assignees": support.assignees.all(),
                        "old_notes": old_notes,
                        "new_notes": new_notes,
                        "old_status": old_status,
                    },
                    support.get_emails(),
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
                    datetime.UTC
                ).astimezone()
                if support.status in ["APP", "COMP"]:
                    support.outdated = True
                support.save()
                new_notes = support.author_notes
                messaging.send_mail_wrapper(
                    f"{support.get_team_display()} team support request update for {support.puzzle.name}",
                    "emails/support_update",
                    {
                        "request": request,
                        "support": support,
                        "assignees": support.assignees.all(),
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


@permission_required("puzzle_editing.change_round", raise_exception=True)
def edit_answer(request, id):
    answer = get_object_or_404(PuzzleAnswer, id=id)

    if request.method == "POST":
        answer_form = AnswerForm(answer.round, request.POST, instance=answer)
        if answer_form.is_valid():
            answer_form.save()

            return redirect(urls.reverse("edit_answer", args=[id]))
    else:
        answer_form = AnswerForm(answer.round, instance=answer)

    return render(request, "edit_answer.html", {"answer": answer, "form": answer_form})


@permission_required("puzzle_editing.change_round", raise_exception=True)
def bulk_add_answers(request, id):
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
@permission_required("puzzle_editing.change_round", raise_exception=True)
def tags(request):
    return render(
        request,
        "tags.html",
        {"tags": PuzzleTag.objects.all().annotate(count=Count("puzzles"))},
    )


@login_required
def statistics(request):
    past_writing = 0
    past_testsolving = 0
    non_puzzle_schedule_tags = ["meta", "navigation", "event"]

    all_counts = (
        Puzzle.objects.values("status")
        .order_by("status")
        .annotate(count=Count("status"))
    )
    rest = {p["status"]: p["count"] for p in all_counts}
    tags = PuzzleTag.objects.filter(important=True)
    tag_counts = {}
    for tag in tags:
        query = (
            Puzzle.objects.filter(tags=tag)
            .values("status")
            .order_by("status")
            .annotate(count=Count("status"))
        )
        tag_counts[tag.name] = {p["status"]: p["count"] for p in query}
        for p in query:
            rest[p["status"]] -= p["count"]
    statuses = []
    for p in sorted(all_counts, key=lambda x: status.get_status_rank(x["status"])):
        status_obj = {
            "status": status.get_display(p["status"]),
            "count": p["count"],
            "rest_count": rest[p["status"]],
        }
        if status.past_writing(p["status"]):
            past_writing += p["count"]
        if status.past_testsolving(p["status"]):
            past_testsolving += p["count"]

        for tag in tags:
            status_obj[tag.name] = tag_counts[tag.name].get(p["status"], 0)

            if tag.name in non_puzzle_schedule_tags:
                if status.past_writing(p["status"]):
                    past_writing -= status_obj[tag.name]
                if status.past_testsolving(p["status"]):
                    past_testsolving -= status_obj[tag.name]
        statuses.append(status_obj)
    answers = {
        "assigned": PuzzleAnswer.objects.filter(puzzles__isnull=False).count(),
        "rest": PuzzleAnswer.objects.filter(puzzles__isnull=False).count(),
        "waiting": PuzzleAnswer.objects.filter(puzzles__isnull=True).count(),
    }
    for tag in tags:
        answers[tag.name] = PuzzleAnswer.objects.filter(
            puzzles__isnull=False, puzzles__tags=tag
        ).count()
        answers["rest"] -= answers[tag.name]

    target_count = SiteSetting.get_int_setting("TARGET_PUZZLE_COUNT")
    unreleased_count = SiteSetting.get_int_setting("UNRELEASED_PUZZLE_COUNT")
    image_base64 = curr_puzzle_graph_b64(
        request.GET.get("time", "alltime"), target_count
    )

    return render(
        request,
        "statistics.html",
        {
            "status": statuses,
            "tags": tags,
            "answers": answers,
            "image_base64": image_base64,
            "past_writing": past_writing,
            "past_testsolving": past_testsolving,
            "target_count": target_count,
            "unreleased_count": unreleased_count,
        },
    )


@permission_required("puzzle_editing.change_round", raise_exception=True)
def new_tag(request):
    if request.method == "POST":
        form = PuzzleTagForm(request.POST)
        if form.is_valid():
            form.save()

            return redirect(urls.reverse("tags"))
        else:
            return render(request, "new_tag.html", {"form": form})
    return render(request, "new_tag.html", {"form": PuzzleTagForm()})


@permission_required("puzzle_editing.change_round", raise_exception=True)
def single_tag(request, id):
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


@permission_required("puzzle_editing.change_round", raise_exception=True)
def edit_tag(request, id):
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


def get_last_action(comment):
    if not comment:
        return "N/A"

    if comment.testsolve_session_id is not None:
        if not comment.is_system:
            return f"Commented on testsolve session #{comment.testsolve_session_id}"
        if comment.content.startswith("Created") or comment.content.startswith(
            "Joined"
        ):
            return comment.content
        return f"Participated in testsolve session #{comment.testsolve_session_id}"

    if comment.status_change:
        if (
            comment.status_change == status.INITIAL_IDEA
            and comment.content == "Created puzzle"
        ):
            return comment.content
        if comment.status_change not in (status.DEFERRED, status.DEAD):
            return (
                f"Changed puzzle status to {status.get_display(comment.status_change)}"
            )

    if comment.is_system:
        return "Updated puzzle"

    # TODO: Add fact-checking, post-prodding details here.

    return "Commented on puzzle"


@login_required
def users(request):
    users = {
        u.id: u
        for u in User.objects.all()
        .order_by(Lower("display_name"))
        .annotate(authored_lead=Count("led_puzzles", distinct=True))
    }
    for user in users.values():
        user.attrs = defaultdict(int)

    attr_keys = []
    statuses = {
        status.DEAD: "dead",
        status.DEFERRED: "deferred",
        status.DONE: "done",
    }
    for key in ("authored", "editing", "postprodding", "factchecking"):
        for st in statuses.values():
            attr_keys.append(f"{key}_{st}")
        attr_keys.append(f"{key}_active")
        puzzle_status_qs = (
            getattr(User, f"{key}_puzzles")
            .through.objects.all()
            .values("user", "puzzle__status")
            .annotate(count=Count("puzzle__status"))
        )
        for ps in puzzle_status_qs:
            if ps["user"] in users:
                st = statuses.get(ps["puzzle__status"], "active")
                users[ps["user"]].attrs[f"{key}_{st}"] += ps["count"]

    attr_keys.append("testsolving_done")
    attr_keys.append("testsolving_in_progress")
    testsolve_participations_qs = (
        TestsolveParticipation.objects.all()
        .annotate(
            in_progress=ExpressionWrapper(Q(ended=None), output_field=BooleanField())
        )
        .values("user", "in_progress")
        .annotate(count=Count("in_progress"))
    )
    for ts in testsolve_participations_qs:
        if ts["user"] in users:
            key = "testsolving_in_progress" if ts["in_progress"] else "testsolving_done"
            users[ts["user"]].attrs[key] += ts["count"]

    last_comments = {
        comment.author_id: comment
        for comment in PuzzleComment.objects.order_by("author_id", "-last_updated")
        .distinct("author_id")
        .select_related("puzzle")
    }

    users = list(users.values())
    for user in users:
        for key in attr_keys:
            setattr(user, key, user.attrs[key])
        # Annotate with the last action a user performed.
        user.last_comment = last_comments.get(user.id, None)
        user.last_action = get_last_action(user.last_comment)

    return render(
        request,
        "users.html",
        {
            "users": users,
        },
    )


@login_required
def users_statuses(request):
    # distinct=True because https://stackoverflow.com/questions/59071464/django-how-to-annotate-manytomany-field-with-count
    annotation_kwargs = {
        stat: Count(
            "authored_puzzles", filter=Q(authored_puzzles__status=stat), distinct=True
        )
        for stat in status.STATUSES
    }

    users = User.objects.all().annotate(**annotation_kwargs)

    users = list(users)
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
@permission_required("puzzle_editing.list_puzzle", raise_exception=True)
def user(request, username: str):
    them = get_object_or_404(User, username=username)
    if request.user.is_superuser and request.method == "POST":
        perm = Permission.objects.filter(name="Can change round").first()
        if not perm:
            msg = "Permission not found"
            raise Exception(msg)
        if "remove-editor" in request.POST:
            them.user_permissions.remove(perm)
        elif "make-editor" in request.POST:
            them.user_permissions.add(perm)

    return render(
        request,
        "user.html",
        {
            "them": them,
            "testsolving_sessions": TestsolveSession.objects.filter(
                participations__user=them.id
            ).order_by("started"),
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
