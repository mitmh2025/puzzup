import datetime
import re
import zoneinfo
from functools import partial
from types import MappingProxyType

from django import forms
from django.conf import settings
from django.core import validators
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.forms.models import ModelChoiceIterator
from django.utils.text import normalize_newlines
from googleapiclient.errors import HttpError

from puzzle_editing.google_integration import GoogleManager
from puzzle_editing.models import (
    Hint,
    PseudoAnswer,
    Puzzle,
    PuzzleAnswer,
    PuzzleCredit,
    PuzzleFactcheck,
    PuzzlePostprod,
    PuzzleTag,
    Round,
    SupportRequest,
    TestsolveParticipation,
    TestsolveSession,
    User,
)


class RadioSelect(forms.RadioSelect):
    def __init__(self, *args, attrs=None, **kwargs):
        # Default attrs to no class to silence dumb KeyError in django widget.
        # This can still be overridden if passed in as an argument.
        super().__init__(*args, attrs={"class": None, **(attrs or {})}, **kwargs)


class MarkdownTextarea(forms.Textarea):
    template_name = "widgets/markdown_textarea.html"


class UserMultipleChoiceField(forms.ModelMultipleChoiceField):
    def __init__(self, *args, only_group=None, **kwargs):
        orderings = [Lower("display_name")]
        if only_group:
            kwargs["queryset"] = User.objects.filter(
                groups__name__in=[only_group]
            ).order_by(*orderings)
        if "queryset" not in kwargs:
            kwargs["queryset"] = User.objects.all().order_by(*orderings)
        if "widget" not in kwargs:
            kwargs["widget"] = UserCheckboxSelectMultiple()
        super().__init__(*args, **kwargs)

    def label_from_instance(self, obj):
        return obj.full_display_name


class UserCheckboxSelectMultiple(forms.CheckboxSelectMultiple):
    template_name = "widgets/user_checkbox_select_multiple.html"


class UserCheckboxSelect(RadioSelect):
    template_name = "widgets/user_checkbox_select_multiple.html"


class UserChoiceField(forms.ModelChoiceField):
    def __init__(self, *args, only_group=None, **kwargs):
        orderings = [Lower("display_name")]
        if only_group:
            kwargs["queryset"] = User.objects.filter(
                groups__name__in=[only_group]
            ).order_by(*orderings)
        if "queryset" not in kwargs:
            kwargs["queryset"] = User.objects.all().order_by(*orderings)
        if "widget" not in kwargs:
            kwargs["widget"] = UserCheckboxSelect()
        super().__init__(*args, **kwargs)

    def label_from_instance(self, obj):
        return obj.full_display_name


class AnswerCheckboxSelectMultiple(forms.CheckboxSelectMultiple):
    # https://stackoverflow.com/a/55129913/3243497
    template_name = "widgets/answer_checkbox_select_multiple.html"

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        if value:
            # option["instance"] = self.choices.queryset.get(pk=value)  # get instance
            # Django 3.1 breaking change! value used to be the primary key or
            # something but now it's
            # https://docs.djangoproject.com/en/3.1/ref/forms/fields/#django.forms.ModelChoiceIteratorValue
            option["instance"] = value.instance
            option["whitespace_sensitive"] = value.instance.whitespace_sensitive
        return option

    # smuggle extra stuff through to the template
    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["options"] = list(self.options(name, context["widget"]["value"], attrs))
        return context


class AnswerMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, answer):
        # don't display the round, which would be in the default str; our
        # custom widget is taking care of that
        return answer.answer


class CustomModelChoiceIterator(ModelChoiceIterator):
    """Iterator that adds the object as the last output."""

    def choice(self, obj):
        return (
            *super().choice(obj),
            obj,
        )


class CustomModelChoiceField(forms.ModelMultipleChoiceField):
    def _get_choices(self):
        if hasattr(self, "_choices"):
            return self._choices
        return CustomModelChoiceIterator(self)

    choices = property(_get_choices, forms.ChoiceField.choices.fset)  # type: ignore


class TagMultipleChoiceField(CustomModelChoiceField):
    def __init__(self, *args, **kwargs):
        if "queryset" not in kwargs:
            kwargs["queryset"] = PuzzleTag.objects.order_by("name")
        if "widget" not in kwargs:
            kwargs["widget"] = forms.CheckboxSelectMultiple()
        super().__init__(*args, **kwargs)

    def label_from_instance(self, tag):
        tpc = tag.puzzles.count()
        return f"{tag.name} ({tpc} puzzle{'s' if tpc != 1 else ''})"


class NormalizeEndingsField(forms.CharField):
    def to_python(self, value):
        return super().to_python(normalize_newlines(value))


class LogisticsInfoForm(forms.ModelForm):
    class Meta:
        model = Puzzle
        fields = (
            "logistics_difficulty_testsolve",
            "logistics_difficulty_postprod",
            "logistics_difficulty_factcheck",
            "logistics_number_testsolvers",
            "logistics_testsolve_length",
            "logistics_testsolve_skills",
            "logistics_specialized_type",
        )

        widgets = MappingProxyType(
            {
                "logistics_difficulty_testsolve": RadioSelect(
                    choices=[
                        (0, "0 - do not foresee problems with getting testsolvers"),
                        (
                            1,
                            "1 - not all testsolvers may enjoy this (e.g. cryptics or logic puzzles)",
                        ),
                        (2, "2 - puzzle has a niche audience (e.g. blaseball)"),
                    ],
                ),
                "logistics_difficulty_postprod": RadioSelect(
                    choices=[
                        (0, "0 - should be straightforward"),
                        (
                            1,
                            "1 - might be a little tricky (e.g. very specific formatting, minor art, some client-side interactivity)",
                        ),
                        (
                            2,
                            "2 - will need to involve tech or art team (e.g. server-side code, websockets, hand-drawn art) - **please create a Support Request through PuzzUp**",
                        ),
                    ],
                ),
                "logistics_difficulty_factcheck": RadioSelect(
                    choices=[
                        (0, "0 - anyone could factcheck this puzzle"),
                        (
                            1,
                            "1 - might be a little tricky (e.g. large puzzles, logic puzzles with branching)",
                        ),
                        (
                            2,
                            "2 - requires special skills, programming, or many people (e.g. hundreds of autogenerated minis, pen-testers, advanced math)",
                        ),
                    ],
                ),
                "logistics_number_testsolvers": forms.TextInput(
                    attrs={"class": "input"}
                ),
                "logistics_testsolve_length": forms.TextInput(attrs={"class": "input"}),
                "logistics_testsolve_skills": forms.TextInput(attrs={"class": "input"}),
                "logistics_specialized_type": RadioSelect(),
            }
        )


# List of timezones that should cover all common use cases
common_timezones = [
    "Pacific/Midway",
    "US/Hawaii",
    "US/Alaska",
    "US/Pacific",
    "US/Mountain",
    "US/Central",
    "US/Eastern",
    "America/Puerto_Rico",
    "America/Argentina/Buenos_Aires",
    "Atlantic/Cape_Verde",
    "Europe/London",
    "Europe/Paris",
    "Asia/Jerusalem",
    "Africa/Addis_Ababa",
    "Asia/Dubai",
    "Asia/Karachi",
    "Asia/Kolkata",
    "Asia/Dhaka",
    "Asia/Bangkok",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Adelaide",
    "Australia/Sydney",
    "Pacific/Efate",
    "Pacific/Auckland",
]


class UserTimezoneForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("timezone",)
        widgets = MappingProxyType(
            {
                "timezone": forms.Select(
                    choices=[
                        (
                            tz,
                            f"{tz} (UTC{datetime.datetime.now(tz=zoneinfo.ZoneInfo(tz)).strftime("%:z")})",
                        )
                        for tz in common_timezones
                    ],
                ),
            }
        )


class SupportForm(forms.ModelForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not user.is_staff:
            del self.fields["discord_channel_id"]
        self.fields["authors"] = UserMultipleChoiceField(initial=user)
        self.fields["notes"].label = "Answer & Round requests"

    class Meta:
        model = SupportRequest
        fields = ("status", "author_notes", "team_notes")
        widgets = MappingProxyType(
            {
                "status": forms.Textarea(attrs={"class": "textarea", "rows": 6}),
                "author_notes": forms.TextInput(attrs={"class": "input"}),
                "team_notes": forms.Textarea(attrs={"class": "textarea", "rows": 6}),
            }
        )


class SupportRequestAuthorNotesForm(forms.ModelForm):
    author_notes = forms.CharField(widget=MarkdownTextarea, required=False)

    class Meta:
        model = SupportRequest
        fields = ("author_notes", "status")


class SupportRequestTeamNotesForm(forms.ModelForm):
    team_notes = forms.CharField(widget=MarkdownTextarea, required=False)

    class Meta:
        model = SupportRequest
        fields = ("team_notes", "status", "assignees")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignees"] = UserMultipleChoiceField()


class SupportRequestStatusForm(forms.ModelForm):
    class Meta:
        model = SupportRequest
        fields = ("status",)


# based on UserCreationForm from Django source
class RegisterForm(forms.ModelForm):
    """
    A form that creates a user, with no privileges, from the given username and
    password.
    """

    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"class": "input"}),
    )
    password2 = forms.CharField(
        label="Password confirmation",
        widget=forms.PasswordInput(attrs={"class": "input"}),
        help_text="Enter the same password as above, for verification.",
    )
    email = forms.EmailField(
        label="Email address",
        required=False,
        help_text="Optional, but you'll get useful email notifications.",
        widget=forms.EmailInput(attrs={"class": "input"}),
    )

    site_password = forms.CharField(
        label="Site password",
        widget=forms.PasswordInput(attrs={"class": "input"}),
        help_text="Get this password from the Discord.",
    )

    display_name = forms.CharField(
        label="Display name",
        required=False,
        help_text="(optional)",
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    credits_name = forms.CharField(
        label="Credits name",
        help_text="(required) Name you want displayed in the credits for hunt and author field on your puzzles, likely your full name",
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    bio = forms.CharField(
        widget=MarkdownTextarea(attrs={"class": "textarea", "rows": 6}),
        required=False,
        help_text="(optional) Tell us about yourself. What kinds of puzzle genres or subject matter do you like?",
    )

    class Meta:
        model = User
        fields = ("username", "email", "display_name", "bio", "credits_name")
        widgets = MappingProxyType(
            {
                "username": forms.TextInput(attrs={"class": "input"}),
            }
        )

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            msg = "The two password fields didn't match."
            raise forms.ValidationError(
                msg,
                code="password_mismatch",
            )
        return password2

    def clean_site_password(self):
        site_password = self.cleaned_data.get("site_password")
        if site_password and site_password != settings.SITE_PASSWORD:
            msg = "The site password was incorrect."
            raise forms.ValidationError(
                msg,
                code="password_mismatch",
            )
        return site_password

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class AccountForm(forms.Form):
    email = forms.EmailField(
        label="Email address",
        required=False,
        help_text="Optional, but you'll get useful email notifications.",
        widget=forms.TextInput(attrs={"class": "input"}),
    )

    display_name = forms.CharField(
        label="Display name",
        required=False,
        widget=forms.TextInput(attrs={"class": "input"}),
    )

    credits_name = forms.CharField(
        label="Credits name",
        help_text="(required) Name you want displayed in the credits for hunt and author field on your puzzles, likely your full name",
        widget=forms.TextInput(attrs={"class": "input"}),
    )

    bio = forms.CharField(
        required=False,
        help_text="(optional) Tell us about yourself. What kinds of puzzle genres or subject matter do you like?",
        widget=MarkdownTextarea(attrs={"class": "textarea", "rows": 6}),
    )


class TestsolveFinderForm(forms.Form):
    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["solvers"] = UserMultipleChoiceField(initial=user)

    solvers = forms.CheckboxSelectMultiple()


class PuzzleInfoForm(forms.ModelForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not user.is_staff:
            del self.fields["discord_channel_id"]
        self.fields["authors"] = UserMultipleChoiceField(initial=user)
        self.fields["lead_author"] = UserChoiceField(
            initial=user,
            help_text=Puzzle._meta.get_field("lead_author").help_text,
        )
        self.fields["notes"].label = "Answer & Round requests"
        self.fields["authors_addl"].label = "Additional authors"

    def clean(self):
        cleaned_data = super().clean()
        lead_author = cleaned_data.get("lead_author")
        authors = cleaned_data.get("authors", [])
        if lead_author and lead_author not in authors:
            cleaned_data["authors"] = [*authors, lead_author]
        return cleaned_data

    class Meta:
        model = Puzzle
        fields = (
            "name",
            "codename",
            "authors",
            "lead_author",
            "authors_addl",
            "discord_channel_id",
            "summary",
            "description",
            "flavor",
            "editor_notes",
            "notes",
            "is_meta",
        )
        widgets = MappingProxyType(
            {
                "authors": forms.CheckboxSelectMultiple(),
                "name": forms.TextInput(attrs={"class": "input"}),
                "authors_addl": forms.TextInput(attrs={"class": "input"}),
                "codename": forms.TextInput(attrs={"class": "input"}),
                "summary": MarkdownTextarea(attrs={"rows": 6}),
                "description": MarkdownTextarea(attrs={"rows": 6}),
                "flavor": MarkdownTextarea(attrs={"rows": 3}),
                "editor_notes": forms.TextInput(attrs={"class": "input"}),
                "notes": MarkdownTextarea(attrs={"rows": 6}),
                "is_meta": forms.CheckboxInput(),
            }
        )


class PuzzleCommentForm(forms.Form):
    content = forms.CharField(widget=MarkdownTextarea)


class PuzzleContentForm(forms.ModelForm):
    class Meta:
        model = Puzzle
        fields = ("content",)
        widgets = MappingProxyType(
            {
                "content": MarkdownTextarea(attrs={"class": "textarea"}),
            }
        )


class PuzzlePseudoAnswerForm(forms.ModelForm):
    class Meta:
        model = PseudoAnswer
        exclude = ()
        help_texts = MappingProxyType(
            {
                "response": (
                    "This could be a nudge in the right direction, or special instructions on how to obtain the actual answer."
                ),
            }
        )
        widgets = MappingProxyType(
            {
                "puzzle": forms.HiddenInput(),
            }
        )
        labels = MappingProxyType(
            {
                "answer": "Partial Answer",
            }
        )


class PuzzleSolutionForm(forms.ModelForm):
    class Meta:
        model = Puzzle
        fields = ("solution",)
        widgets = MappingProxyType(
            {
                "solution": MarkdownTextarea(attrs={"class": "textarea"}),
            }
        )


class PuzzlePriorityForm(forms.ModelForm):
    class Meta:
        model = Puzzle
        fields = ("priority",)


def guess_google_doc_id(google_doc_url="") -> str:
    match = re.search(
        r"docs.google.com/document/d/([A-Za-z0-9_\-]+)/.*", google_doc_url
    )
    return match.group(1) if match else ""


class PuzzlePostprodForm(forms.ModelForm):
    DEFAULT_PUZZLE_WIDTH_PX = 900

    puzzle_google_doc_id = forms.CharField(
        help_text="The puzzle's Google Doc ID or URL, e.g. https://docs.google.com/document/d/{doc_id}/edit",
        required=False,
    )
    solution_google_doc_id = forms.CharField(
        help_text="The solution's Google Doc ID or URL, e.g. https://docs.google.com/document/d/{doc_id}/edit",
        required=False,
    )
    slug = forms.CharField(
        help_text="The part of the URL on the hunt site referring to this puzzle. E.g. for https://puzzle.hunt/puzzle/fifty-fifty, this would be 'fifty-fifty'.",
        validators=[validators.validate_slug],
    )
    puzzle_directory = forms.CharField(
        help_text="Where you would like to save the puzzle postprod. You can use the default value for most puzzles.",
        initial="client/pages/puzzles/",
    )
    image_type = forms.ChoiceField(
        help_text="Whether the maximum image size is in pixels or % of the puzzle container.",
        choices=[
            ("PERCENT", "%"),
            ("PIXEL", "px"),
        ],
        initial="PERCENT",
    )
    max_image_width = forms.IntegerField(
        help_text="The maximum size (in px or % of container) that an image should take in the puzzle. PuzzUp will autoresize images to this. Ignored if there are no images to postprod.",
        initial=50,
        min_value=0,
        max_value=5000,
    )

    class Meta:
        model = PuzzlePostprod
        exclude = ()
        widgets = MappingProxyType(
            {
                "puzzle": forms.HiddenInput(),
            }
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        initial = kwargs.get("initial")
        instance = kwargs.get("instance")
        if initial:
            puzzle = initial["puzzle"]
        elif instance:
            puzzle = instance.puzzle
        else:
            # Both are expected to be None if we are creating a new postprod.
            return

        if puzzle.has_postprod():
            self.fields[
                "puzzle_google_doc_id"
            ].help_text += " (Note: Puzzle has already been postprodded - leave blank or it will be overwritten)"
            self.fields[
                "solution_google_doc_id"
            ].help_text += " (Note: Puzzle has already been postprodded - leave blank or it will be overwritten)"

        # Try to guess the google doc id from the puzzle content or solution.
        else:
            if doc_id := guess_google_doc_id(puzzle.content):
                self.fields["puzzle_google_doc_id"].initial = doc_id
                self.fields[
                    "puzzle_google_doc_id"
                ].help_text = f"(Automatically extracted from https://docs.google.com/document/d/{doc_id}/)"
            if doc_id := guess_google_doc_id(puzzle.solution):
                self.fields["solution_google_doc_id"].initial = doc_id
                self.fields[
                    "solution_google_doc_id"
                ].help_text = f"(Automatically extracted from https://docs.google.com/document/d/{doc_id}/)"

    def get_gdoc_html(self, google_doc_id: str) -> str:
        if not google_doc_id:
            return ""

        cleaned_id = google_doc_id

        # If it's a url, try to grab the doc id from the url.
        if "docs.google.com" in google_doc_id:
            cleaned_id = guess_google_doc_id(google_doc_id)
            if not cleaned_id:
                msg = "Unable to parse Google doc ID"
                raise ValidationError(msg)

        try:
            return GoogleManager.instance().get_gdoc_html(cleaned_id)
        except HttpError as e:
            msg = "Could not find Google doc with corresponding ID! Please make sure it is in our Google Drive folder."
            raise ValidationError(msg) from e

    def clean_puzzle_google_doc_id(self):
        google_doc_id = self.cleaned_data["puzzle_google_doc_id"]
        self.cleaned_data["puzzle_html"] = self.get_gdoc_html(google_doc_id)
        return google_doc_id

    def clean_solution_google_doc_id(self):
        google_doc_id = self.cleaned_data["solution_google_doc_id"]
        self.cleaned_data["solution_html"] = self.get_gdoc_html(google_doc_id)
        return google_doc_id

    def clean_max_image_width(self):
        max_image_width = self.cleaned_data["max_image_width"]
        if self.cleaned_data["image_type"] == "PERCENT":
            if max_image_width < 0 or max_image_width > 100:
                msg = "Must be between 0-100%"
                raise ValidationError(msg)

            return int(max_image_width / 100 * self.DEFAULT_PUZZLE_WIDTH_PX)
        return max_image_width


class EditPostprodForm(forms.ModelForm):
    class Meta:
        model = PuzzlePostprod
        fields = ("host_url",)


class PuzzleFactcheckForm(forms.ModelForm):
    class Meta:
        model = PuzzleFactcheck
        fields = ("output",)
        widgets = MappingProxyType(
            {
                "output": forms.Textarea(attrs={"class": "textarea"}),
            }
        )


class PuzzleHintForm(forms.ModelForm):
    class Meta:
        model = Hint
        exclude = ()
        widgets = MappingProxyType(
            {
                "description": forms.TextInput(attrs={"class": "input"}),
                "order": forms.TextInput(
                    attrs={"class": "input", "placeholder": "e.g. 10.1"}
                ),
                "keywords": forms.TextInput(
                    attrs={"class": "input", "placeholder": "e.g. extraction"}
                ),
                "puzzle": forms.HiddenInput(),
                "content": forms.Textarea(attrs={"class": "textarea", "rows": 4}),
            }
        )


class PuzzleAnswersForm(forms.ModelForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)

        puzzle = kwargs["instance"]

        self.fields["answers"] = AnswerMultipleChoiceField(
            queryset=PuzzleAnswer.objects.filter(round__spoiled=user)
            .order_by("round__name")
            .annotate(
                other_puzzle_count=Count("puzzles", filter=~Q(puzzles__id=puzzle.id)),
            ),
            widget=AnswerCheckboxSelectMultiple(),
            required=False,
        )

    class Meta:
        model = Puzzle
        fields = ("answers",)
        widgets = MappingProxyType(
            {
                "answers": forms.CheckboxSelectMultiple(),
            }
        )


class PuzzleTaggingForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tags"] = TagMultipleChoiceField(
            required=False, initial=kwargs["instance"].tags.values_list("id", flat=True)
        )

    class Meta:
        model = Puzzle
        fields = ("tags",)
        widgets = MappingProxyType(
            {
                "tags": forms.CheckboxSelectMultiple(),
            }
        )


class PuzzleOtherCreditsForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["users"] = UserMultipleChoiceField(required=False)
        self.fields["credit_type"] = forms.ChoiceField(
            required=True,
            choices=[
                ("ART", "Art"),
                ("TCH", "Tech"),
                ("OTH", "Other"),
            ],
        )

    class Meta:
        model = PuzzleCredit
        fields = ("users", "credit_type", "puzzle", "text")
        widgets = MappingProxyType(
            {
                "puzzle": forms.HiddenInput(),
                "text": forms.TextInput(attrs={"class": "input"}),
            }
        )


class PuzzlePeopleForm(forms.ModelForm):
    class Meta:
        model = Puzzle
        fields = (
            "lead_author",
            "authors",
            "editors",
            "factcheckers",
            "postprodders",
            "spoiled",
        )
        field_classes = MappingProxyType(
            {
                "lead_author": UserChoiceField,
                "authors": UserMultipleChoiceField,
                "editors": partial(UserMultipleChoiceField, only_group="Editor"),
                "factcheckers": partial(
                    UserMultipleChoiceField, only_group="Factchecker"
                ),
                "postprodders": partial(
                    UserMultipleChoiceField, only_group="Postprodder"
                ),
                "spoiled": UserMultipleChoiceField,
            }
        )
        help_texts = MappingProxyType(
            {
                "factcheckers": "Factcheckers must be part of the Factchecker group. If you need someone added to the group, contact one of the Tech Leads.",
                "postprodders": "Postprodders must be part of the Postprodder group. If you need someone added to the group, contact one of the Tech Leads.",
                "spoiled": "Note that lead author, authors, and editors will always be marked as spoiled, even if you de-select them here.",
            }
        )


class TestsolveSessionNotesForm(forms.ModelForm):
    notes = forms.CharField(widget=MarkdownTextarea, required=False)

    class Meta:
        model = TestsolveSession
        fields = ("notes",)


class GuessForm(forms.Form):
    guess = forms.CharField()


class TestsolveParticipationForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["general_feedback"].required = True

    class Meta:
        model = TestsolveParticipation
        exclude = (
            "session",
            "user",
            "started",
            "ended",
            # Hide all of these form fields as we felt they are extraneous.
            # FIXME: you may want to show some of them.
            "clues_needed",
            "aspects_enjoyable",
            "aspects_unenjoyable",
            "aspects_accessibility",
            "technical_issues",
            "technical_issues_device",
            "technical_issues_description",
            "instructions_overall",
            "instructions_feedback",
            "flavortext_overall",
            "flavortext_feedback",
            "stuck_overall",
            "stuck_points",
            "stuck_time",
            "stuck_unstuck",
            "errors_found",
            "suggestions_change",
            "suggestions_keep",
        )
        widgets = MappingProxyType(
            {
                "general_feedback": MarkdownTextarea(attrs={"rows": 5}),
                "misc_feedback": MarkdownTextarea(attrs={"rows": 5}),
                "fun_rating": RadioSelect(
                    choices=[
                        (None, "n/a"),
                        (1, "1: unfun"),
                        (2, "2: neutral"),
                        (3, "3: somewhat fun"),
                        (4, "4: fun"),
                        (5, "5: very fun"),
                        (6, "6: one of the best puzzles I've done"),
                    ]
                ),
                "difficulty_rating": RadioSelect(
                    choices=[
                        (None, "n/a"),
                        (
                            1,
                            "1: very easy - straightforward for new solvers (e.g. Puzzled Pint)",
                        ),
                        (
                            2,
                            "2: easy – doable but challenging for new solvers (e.g. Fish/Students, teammate hunt intro round)",
                        ),
                        (
                            3,
                            "3: somewhat difficult – still straightforward for experienced teams (e.g. Ministry)",
                        ),
                        (
                            4,
                            "4: difficult – challenges most teams (e.g. main round MH, teammate hunt main rounds)",
                        ),
                        (5, "5: very difficult – hard even for MH"),
                        (6, "6: extremely difficult - too hard even for MH"),
                    ]
                ),
            }
        )


class AnswerForm(forms.ModelForm):
    def __init__(self, round, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["round"] = forms.ModelChoiceField(
            queryset=Round.objects.all(),  # ???
            initial=round,
            widget=forms.HiddenInput(),
        )
        # Don't strip whitespace for answers
        self.fields["answer"].strip = False

    class Meta:
        model = PuzzleAnswer
        fields = (
            "answer",
            "round",
            "flexible",
            "notes",
            "case_sensitive",
            "whitespace_sensitive",
        )
        widgets = MappingProxyType(
            {
                "answer": forms.Textarea(
                    # Default to 1 row, but allow users to drag if they need more space.
                    attrs={"rows": 1, "cols": 30, "class": "answer notes-field"}
                ),
                "notes": forms.Textarea(
                    attrs={"rows": 4, "cols": 20, "class": "notes-field"}
                ),
            }
        )

        field_classes = MappingProxyType(
            {
                "answer": NormalizeEndingsField,
            }
        )


class RoundForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["editors"] = UserMultipleChoiceField(
            required=False, only_group="Editor"
        )

    class Meta:
        model = Round
        fields = ("name", "description", "editors")
        widgets = MappingProxyType(
            {
                "description": MarkdownTextarea(),
            }
        )


class PuzzleTagForm(forms.ModelForm):
    description = forms.CharField(
        widget=MarkdownTextarea,
        required=False,
        help_text="(optional) Elaborate on the meaning of this tag.",
    )

    class Meta:
        model = PuzzleTag
        fields = ("name", "description", "important")


class TestsolveParticipantPicker(forms.Form):
    def __init__(self, user, exclude, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["add_testsolvers"] = UserMultipleChoiceField(
            initial=user, queryset=exclude
        )

    add_testsolvers = forms.CheckboxSelectMultiple()
