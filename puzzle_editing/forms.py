from collections.abc import Callable

from django import forms
from django.conf import settings
from django.core import validators
from django.core.exceptions import ValidationError
from django.db.models import Count, Model, Q
from django.db.models.functions import Lower
from django.utils.safestring import mark_safe
from googleapiclient.errors import HttpError

from .google_integration import GoogleManager
from .models import (
    Hint,
    PseudoAnswer,
    Puzzle,
    PuzzleAnswer,
    PuzzleCredit,
    PuzzlePostprod,
    PuzzleTag,
    Round,
    SupportRequest,
    TestsolveFeedback,
    TestsolveParticipation,
    TestsolveSession,
    User,
)
from .utils.postprod import guess_google_doc_id


class MarkdownTextarea(forms.Textarea):
    template_name = "widgets/markdown_textarea.html"


class UserCheckboxSelectMultiple(forms.CheckboxSelectMultiple):
    template_name = "widgets/user_checkbox_select_multiple.html"


class UserMultipleChoiceField(forms.ModelMultipleChoiceField):
    def __init__(
        self,
        label_fn: Callable[[User], str] = User.full_display_name.__get__,
        *args,
        **kwargs,
    ):
        orderings = []
        # if kwargs.get("editors_first", False):
        #     orderings.append("-user_permissions")
        #     del kwargs["editors_first"]
        orderings.append(Lower("credits_name"))
        if "eics_only" in kwargs:
            kwargs["queryset"] = User.objects.filter(groups__name__in=["EIC"]).order_by(
                *orderings
            )
            del kwargs["eics_only"]
        if "queryset" not in kwargs:
            kwargs["queryset"] = User.objects.all().order_by(*orderings)
        if "widget" not in kwargs:
            kwargs["widget"] = UserCheckboxSelectMultiple()
        super().__init__(*args, **kwargs)
        self.label_fn = label_fn

    def label_from_instance(self, obj: Model):
        if isinstance(obj, User):
            return self.label_fn(obj)
        return super().label_from_instance(obj)


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

    credits_name = forms.CharField(
        label="Credits name",
        help_text=(
            "(required) Name you want displayed in the credits for hunt and author"
            " field on your puzzles, likely your full name"
        ),
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    bio = forms.CharField(
        widget=MarkdownTextarea(attrs={"class": "textarea", "rows": 6}),
        required=False,
        help_text=(
            "(optional) Tell us about yourself. What kinds of puzzle genres or subject"
            " matter do you like?"
        ),
    )

    class Meta:
        model = User
        fields = ("username", "email", "bio", "credits_name")
        widgets = {
            "username": forms.TextInput(attrs={"class": "input"}),
        }

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            msg = "The two password fields didn't match."
            raise forms.ValidationError(msg, code="password_mismatch")
        return password2

    def clean_site_password(self):
        site_password = self.cleaned_data.get("site_password")
        if site_password and site_password != settings.SITE_PASSWORD:
            msg = "The site password was incorrect."
            raise forms.ValidationError(msg, code="password_mismatch")
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

    credits_name = forms.CharField(
        label="Credits name",
        help_text=(
            "(required) Name you want displayed in the credits for hunt and author"
            " field on your puzzles, likely your full name."
        ),
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    github_username = forms.CharField(
        label="Github username",
        help_text="(optional) For integration with the hunt site repo.",
        widget=forms.TextInput(attrs={"class": "input"}),
    )

    bio = forms.CharField(
        required=False,
        help_text=(
            "(optional) Tell us about yourself. What kinds of puzzle genres or subject"
            " matter do you like?"
        ),
        widget=MarkdownTextarea(attrs={"class": "textarea", "rows": 6}),
    )
    keyboard_shortcuts = forms.BooleanField(
        label="Enable keyboard shortcuts",
        required=False,
        help_text="On puzzle pages only. Press ? for help.",
    )


class PuzzleInfoForm(forms.ModelForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not user.is_staff:
            del self.fields["discord_channel_id"]
        self.fields["authors"] = UserMultipleChoiceField(initial=user)
        self.fields["notes"].label = "Answer & Round requests"
        self.fields["authors_addl"].label = "Additional authors"

    class Meta:
        model = Puzzle
        fields = [
            "name",
            "codename",
            "authors",
            "authors_addl",
            "discord_channel_id",
            "discord_emoji",
            "summary",
            "description",
            "editor_notes",
            "notes",
            "is_meta",
        ]
        widgets = {
            "authors": forms.CheckboxSelectMultiple(),
            "name": forms.TextInput(attrs={"class": "input"}),
            "authors_addl": forms.TextInput(attrs={"class": "input"}),
            "codename": forms.TextInput(attrs={"class": "input"}),
            "summary": MarkdownTextarea(),
            "description": MarkdownTextarea(),
            "editor_notes": forms.TextInput(attrs={"class": "input"}),
            "notes": MarkdownTextarea(),
            "is_meta": forms.CheckboxInput(),
        }


class PuzzleCommentForm(forms.Form):
    content = forms.CharField(widget=MarkdownTextarea)


class PuzzleContentForm(forms.ModelForm):
    class Meta:
        model = Puzzle
        fields = ["content"]
        widgets = {
            "content": forms.Textarea(attrs={"class": "textarea"}),
        }


class PuzzleSolutionForm(forms.ModelForm):
    class Meta:
        model = Puzzle
        fields = ["solution"]
        widgets = {
            "solution": forms.Textarea(attrs={"class": "textarea"}),
        }


class PuzzlePseudoAnswerForm(forms.ModelForm):
    class Meta:
        model = PseudoAnswer
        fields = [
            "puzzle",
            "answer",
            "response",
            "case_sensitive",
            "whitespace_sensitive",
            "special_sensitive",
        ]
        help_texts = {
            "response": (
                "This could be a 'keep going' message, a nudge in the right direction,"
                " or special instructions on how to obtain the actual answer."
            ),
        }
        widgets = {
            "puzzle": forms.HiddenInput(),
        }
        labels = {
            "answer": "Partial Answer",
        }


class PuzzlePriorityForm(forms.ModelForm):
    class Meta:
        model = Puzzle
        fields = ["priority"]


class PuzzlePostprodForm(forms.ModelForm):
    DEFAULT_PUZZLE_WIDTH_PX = 900

    puzzle_google_doc_id = forms.CharField(
        help_text=(
            "The puzzle's Google Doc ID or URL, e.g."
            " https://docs.google.com/document/d/{doc_id}/edit"
        ),
        required=False,
    )
    solution_google_doc_id = forms.CharField(
        help_text=(
            "The solution's Google Doc ID or URL, e.g."
            " https://docs.google.com/document/d/{doc_id}/edit"
        ),
        required=False,
    )
    slug = forms.CharField(
        help_text=(
            "The part of the URL on the hunt site referring to this puzzle. E.g. for"
            " https://puzzle.hunt/puzzle/fifty-fifty, this would be 'fifty-fifty'."
        ),
        validators=[validators.validate_slug],
    )
    puzzle_directory = forms.CharField(
        help_text=(
            "Where you would like to save the puzzle postprod. You should use the"
            " default value for most puzzles."
        ),
        initial="client/pages/puzzles/",
    )
    image_type = forms.ChoiceField(
        help_text=(
            "Whether the maximum image size is in pixels or % of the puzzle container."
        ),
        choices=[
            ("PERCENT", "%"),
            ("PIXEL", "px"),
        ],
        initial="PERCENT",
    )
    max_image_width = forms.IntegerField(
        help_text=(
            "The maximum size (in px or % of container) that an image should take in"
            " the puzzle. PuzzUp will autoresize images to this. Ignored if there are"
            " no images to postprod."
        ),
        initial=50,
        min_value=0,
        max_value=5000,
    )

    class Meta:
        model = PuzzlePostprod
        fields = ["puzzle", "slug"]
        widgets = {
            "puzzle": forms.HiddenInput(),
        }

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
            self.fields["puzzle_google_doc_id"].help_text += (
                " (Note: Puzzle has already been postprodded - leave blank or it will"
                " be overwritten)."
            )
            self.fields["solution_google_doc_id"].help_text += (
                " (Note: Puzzle has already been postprodded - leave blank or it will"
                " be overwritten)"
            )
            if doc_id := guess_google_doc_id(puzzle.content):
                self.fields["puzzle_google_doc_id"].help_text += (
                    f" Current link: https://docs.google.com/document/d/{doc_id}/)"
                )
            if doc_id := guess_google_doc_id(puzzle.solution):
                self.fields["solution_google_doc_id"].help_text += (
                    f" Current link: https://docs.google.com/document/d/{doc_id}/)"
                )

        # Try to guess the google doc id from the puzzle content or solution.
        else:
            if doc_id := guess_google_doc_id(puzzle.content):
                self.fields["puzzle_google_doc_id"].initial = doc_id
                self.fields["puzzle_google_doc_id"].help_text = (
                    "(Automatically extracted from"
                    f" https://docs.google.com/document/d/{doc_id}/)"
                )
            if doc_id := guess_google_doc_id(puzzle.solution):
                self.fields["solution_google_doc_id"].initial = doc_id
                self.fields["solution_google_doc_id"].help_text = (
                    "(Automatically extracted from"
                    f" https://docs.google.com/document/d/{doc_id}/)"
                )

    @staticmethod
    def get_gdoc_html(google_doc_id: str) -> str:
        if not google_doc_id:
            return ""

        cleaned_id = google_doc_id

        # If it's a url, try to grab the doc id from the url.
        if "docs.google.com" in google_doc_id:
            cleaned_id = guess_google_doc_id(google_doc_id)
            if not cleaned_id:
                err = "Unable to parse Google doc ID"
                raise ValidationError(err)

        try:
            return GoogleManager.instance().get_gdoc_html(cleaned_id)
        except HttpError as e:
            err = (
                "Could not find Google doc with corresponding ID! Please make sure that"
                " it is shared with everyone."
            )
            raise ValidationError(err) from e

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
                err = "Must be between 0-100%"
                raise ValidationError(err)

            return int(max_image_width / 100 * self.DEFAULT_PUZZLE_WIDTH_PX)
        return max_image_width


class EditPostprodForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["host_url"].widget.attrs["style"] = "width: 100%;"

    class Meta:
        model = PuzzlePostprod
        fields = ["host_url"]


class PuzzleHintForm(forms.ModelForm):
    class Meta:
        model = Hint
        exclude: list[str] = []
        widgets = {
            "order": forms.TextInput(
                attrs={"class": "input", "placeholder": "e.g. 10.1"}
            ),
            "description": forms.TextInput(
                attrs={
                    "class": "input",
                    "placeholder": "e.g. Solvers are stuck on getting started.",
                }
            ),
            "keywords": forms.TextInput(
                attrs={"class": "input", "placeholder": "e.g. extraction"}
            ),
            "puzzle": forms.HiddenInput(),
            "content": forms.Textarea(attrs={"class": "textarea", "rows": 4}),
        }


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
        fields = ["users", "credit_type", "puzzle", "text"]
        widgets = {
            "puzzle": forms.HiddenInput(),
            "text": forms.TextInput(attrs={"class": "input"}),
        }


class PuzzlePeopleForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["authors"] = UserMultipleChoiceField(required=False)
        self.fields["editors"] = UserMultipleChoiceField(required=False)
        self.fields["discussion_editors"] = UserMultipleChoiceField(required=False)
        self.fields["quickcheckers"] = UserMultipleChoiceField(required=False)
        self.fields["factcheckers"] = UserMultipleChoiceField(required=False)
        self.fields["postprodders"] = UserMultipleChoiceField(required=False)
        self.fields["spoiled"] = UserMultipleChoiceField(required=False)

    def clean(self):
        """On clean, ensure that all authors and editors are spoiled."""
        cleaned_data = super().clean()
        spoiled = set(cleaned_data["spoiled"])
        authors = set(cleaned_data["authors"])
        editors = set(cleaned_data["editors"])
        disc_editors = set(cleaned_data["discussion_editors"])
        cleaned_data["spoiled"] = list(spoiled | authors | editors | disc_editors)
        return cleaned_data

    class Meta:
        model = Puzzle
        fields = [
            "authors",
            "editors",
            "discussion_editors",
            "quickcheckers",
            "factcheckers",
            "postprodders",
            "spoiled",
        ]


# https://stackoverflow.com/a/55129913/3243497
class AnswerCheckboxSelectMultiple(forms.CheckboxSelectMultiple):
    template_name = "widgets/answer_checkbox_select_multiple.html"

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        if value:
            # option["instance"] = self.choices.queryset.get(pk=value)  # get instance
            # Django 3.1 breaking change! value used to be the primary key or
            # something but now it's
            # https://docs.djangoproject.com/en/3.1/ref/forms/fields/#django.forms.ModelChoiceIteratorValue
            option["instance"] = value.instance
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


class PuzzleAnswersForm(forms.ModelForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)

        puzzle = kwargs["instance"]

        self.fields["answers"] = AnswerMultipleChoiceField(
            queryset=PuzzleAnswer.objects.filter(round__spoiled=user)
            .select_related("round")
            .order_by("round__name")
            .annotate(
                other_puzzle_count=Count("puzzles", filter=~Q(puzzles__id=puzzle.id)),
            ),
            widget=AnswerCheckboxSelectMultiple(),
            required=False,
        )

    class Meta:
        model = Puzzle
        fields = ["answers"]


class AnswerForm(forms.ModelForm):
    def __init__(self, round, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["round"] = forms.ModelChoiceField(
            queryset=Round.objects.all(),  # ???
            initial=round,
            widget=forms.HiddenInput(),
        )

    class Meta:
        model = PuzzleAnswer
        fields = [
            "answer",
            "round",
            "notes",
            "case_sensitive",
            "whitespace_sensitive",
            "special_sensitive",
        ]
        widgets = {
            "notes": forms.Textarea(
                attrs={"rows": 4, "cols": 20, "class": "notes-field"}
            ),
        }


class RoundForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["editors"] = UserMultipleChoiceField(required=False, eics_only=True)

    class Meta:
        model = Round
        fields = [
            "name",
            "description",
            "editors",
            "puzzle_template",
            "solution_template",
        ]


class TagMultipleChoiceField(forms.ModelMultipleChoiceField):
    def __init__(self, *args, **kwargs):
        if "queryset" not in kwargs:
            kwargs["queryset"] = PuzzleTag.objects.all()
        if "widget" not in kwargs:
            kwargs["widget"] = forms.CheckboxSelectMultiple()
        super().__init__(*args, **kwargs)

    def label_from_instance(self, tag):
        tpc = tag.puzzles.count()
        return "{} ({} puzzle{})".format(tag.name, tpc, "s" if tpc != 1 else "")


class PuzzleTaggingForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["tags"] = TagMultipleChoiceField(required=False)

    class Meta:
        model = Puzzle
        fields = ["tags"]
        widgets = {
            "tags": forms.CheckboxSelectMultiple(),
        }


class PuzzleTagForm(forms.ModelForm):
    description = forms.CharField(
        widget=MarkdownTextarea,
        required=False,
        help_text="(optional) Elaborate on the meaning of this tag.",
    )

    class Meta:
        model = PuzzleTag
        fields = ["name", "description", "important"]


class TestsolveFinderForm(forms.Form):
    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["solvers"] = UserMultipleChoiceField(initial=user)

    solvers = forms.CheckboxSelectMultiple()


class TestsolveParticipantPicker(forms.Form):
    def __init__(self, user, exclude, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["add_testsolvers"] = UserMultipleChoiceField(
            initial=user, queryset=exclude
        )

    add_testsolvers = forms.CheckboxSelectMultiple()


class TestsolveSessionInfoForm(forms.ModelForm):
    def __init__(
        self,
        puzzle: Puzzle,
        admin: User | None = None,
        initial_solvers: list[User] | None = None,
        exclude=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        authors = set(puzzle.authors.values_list("id", flat=True))
        editors = set(puzzle.editors.values_list("id", flat=True))
        spoiled_users = set(puzzle.spoiled.values_list("id", flat=True))
        spoiled_round_users = set(
            puzzle.answers.values("round")
            .distinct()
            .values_list("round__spoiled", flat=True)
        )

        if exclude is None:
            exclude = User.objects.all()

        def testsolve_user_label_fn(user: User):
            res = user.full_display_name
            if user.id in authors:
                res += " 📝 (AUTHOR)"
            elif user.id in editors:
                res += " 💬 (EDITOR)"
            elif user.id in spoiled_users:
                res += " 👀 (PUZZLE-SPOILED)"
            elif user.id in spoiled_round_users:
                res += " 🔮 (ROUND-SPOILED)"
            elif user.is_testsolve_coordinator:
                res += " 🧪 (UNSPOILED TEST ADMIN)"
            return res

        self.fields["admin"] = forms.ModelChoiceField(
            queryset=User.objects.filter(groups__name__in=["Testsolve Coordinators"]),
            initial=admin,
            empty_label=None,
        )
        self.fields["solvers"] = UserMultipleChoiceField(
            label_fn=testsolve_user_label_fn,
            required=False,
            initial=initial_solvers,
            queryset=exclude,
        )

    solvers = UserMultipleChoiceField()

    class Meta:
        model = TestsolveSession
        fields = [
            "admin",
        ]

    field_order = ["admin", "solvers"]


class TestsolveSessionNotesForm(forms.ModelForm):
    notes = forms.CharField(widget=MarkdownTextarea, required=False)

    class Meta:
        model = TestsolveSession
        fields = ["notes"]


class GuessForm(forms.Form):
    guess = forms.CharField()


class TestsolveParticipationForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["finish_method"] = forms.ChoiceField(
            choices=[
                (
                    "INCOMPLETE",
                    mark_safe(
                        "<strong>Incomplete</strong> - I'll come back to finish later"
                        " but wanted to submit interim feedback."
                    ),
                ),
                (
                    "NO_SPOIL",
                    mark_safe(
                        "<strong>Finished, don't spoil me</strong> - I'm very much done"
                        " with this instance (or have completely given up on this meta,"
                        " and understand I shouldn't peek at other feeder answers)."
                    ),
                ),
                (
                    "SPOIL",
                    mark_safe(
                        "<strong>Finished, spoil me</strong> - (do NOT use this on"
                        " METAs)"
                    ),
                ),
            ],
            widget=forms.RadioSelect(attrs={"class": ""}),
            initial="INCOMPLETE",
        )

    class Meta:
        model = TestsolveParticipation
        fields = ["fun_rating", "difficulty_rating", "hours_spent"]
        widgets = {
            "fun_rating": forms.RadioSelect(
                choices=[
                    (None, "n/a"),
                    (1, "1: unfun"),
                    (2, "2: neutral"),
                    (3, "3: somewhat fun"),
                    (4, "4: fun"),
                    (5, "5: very fun"),
                    (6, "6: one of the best puzzles I've done"),
                ],
                attrs={"class": ""},
            ),
            "difficulty_rating": forms.RadioSelect(
                choices=[
                    (None, "n/a"),
                    (
                        1,
                        (
                            "1: very easy - straightforward for new solvers (e.g."
                            " Puzzled Pint, Grand Hunt Digital R1-2)"
                        ),
                    ),
                    (
                        2,
                        (
                            "2: easy - doable but challenging for new solvers (e.g."
                            " Fish/Students, teammate hunt intro round)"
                        ),
                    ),
                    (
                        3,
                        (
                            "3: somewhat difficult - still straightforward for"
                            " experienced teams (e.g. Ministry)"
                        ),
                    ),
                    (
                        4,
                        (
                            "4: difficult - challenges most teams (e.g. main round MH,"
                            " teammate / galactic hunt main rounds)"
                        ),
                    ),
                    (5, "5: very difficult - hard even for MH"),
                    (6, "6: extremely difficult - too hard even for MH"),
                ],
                attrs={"class": ""},
            ),
        }


class TestsolveFeedbackForm(forms.ModelForm):
    def __init__(
        self,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

    class Meta:
        model = TestsolveFeedback
        fields = [
            "solve_path",
            "meta_info",
            "general_feedback",
            "aspects_accessibility",
        ]
        widgets = {
            "solve_path": MarkdownTextarea(),
            "meta_info": MarkdownTextarea(),
            "general_feedback": MarkdownTextarea(),
            "aspects_accessibility": MarkdownTextarea(),
        }


class SupportForm(forms.ModelForm):
    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not user.is_staff:
            del self.fields["discord_channel_id"]
        self.fields["authors"] = UserMultipleChoiceField(initial=user)
        self.fields["notes"].label = "Answer & Round requests"

    class Meta:
        model = SupportRequest
        fields = ["status", "author_notes", "team_notes"]
        widgets = {
            "status": forms.Textarea(attrs={"class": "textarea", "rows": 6}),
            "author_notes": forms.TextInput(attrs={"class": "input"}),
            "team_notes": forms.Textarea(attrs={"class": "textarea", "rows": 6}),
        }


class SupportRequestAuthorNotesForm(forms.ModelForm):
    author_notes = forms.CharField(widget=MarkdownTextarea, required=False)

    class Meta:
        model = SupportRequest
        fields = ["author_notes", "status"]


class SupportRequestTeamNotesForm(forms.ModelForm):
    team_notes = forms.CharField(widget=MarkdownTextarea, required=False)

    class Meta:
        model = SupportRequest
        fields = ["team_notes", "status", "assignees"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignees"] = UserMultipleChoiceField()


class SupportRequestStatusForm(forms.ModelForm):
    class Meta:
        model = SupportRequest
        fields = ["status"]


class GDocHtmlPreviewForm(forms.Form):
    gdoc_url = forms.CharField(
        label="Google doc URL",
        widget=forms.TextInput(attrs={"class": "input"}),
    )

    class Meta:
        fields = ["gdoc_url"]
