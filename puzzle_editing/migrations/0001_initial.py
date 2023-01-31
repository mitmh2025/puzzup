# Generated by Django 3.1.13 on 2022-01-20 15:59

from django.conf import settings
import django.contrib.auth.models
import django.contrib.auth.validators
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import puzzle_editing.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='User',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('password', models.CharField(max_length=128, verbose_name='password')),
                ('last_login', models.DateTimeField(blank=True, null=True, verbose_name='last login')),
                ('is_superuser', models.BooleanField(default=False, help_text='Designates that this user has all permissions without explicitly assigning them.', verbose_name='superuser status')),
                ('username', models.CharField(error_messages={'unique': 'A user with that username already exists.'}, help_text='Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.', max_length=150, unique=True, validators=[django.contrib.auth.validators.UnicodeUsernameValidator()], verbose_name='username')),
                ('first_name', models.CharField(blank=True, max_length=150, verbose_name='first name')),
                ('last_name', models.CharField(blank=True, max_length=150, verbose_name='last name')),
                ('email', models.EmailField(blank=True, max_length=254, verbose_name='email address')),
                ('is_staff', models.BooleanField(default=False, help_text='Designates whether the user can log into this admin site.', verbose_name='staff status')),
                ('is_active', models.BooleanField(default=True, help_text='Designates whether this user should be treated as active. Unselect this instead of deleting accounts.', verbose_name='active')),
                ('date_joined', models.DateTimeField(default=django.utils.timezone.now, verbose_name='date joined')),
                ('discord_username', models.CharField(blank=True, max_length=500)),
                ('discord_nickname', models.CharField(blank=True, max_length=500)),
                ('discord_user_id', models.CharField(blank=True, max_length=500)),
                ('avatar_url', models.CharField(blank=True, max_length=500)),
                ('display_name', models.CharField(blank=True, help_text='How you want your name to appear to other puzzup users.', max_length=500)),
                ('credits_name', models.CharField(help_text='How you want your name to appear in puzzle credits, e.g. Ben Bitdiddle', max_length=80)),
                ('bio', models.TextField(blank=True, help_text='Tell us about yourself. What kinds of puzzle genres or subject matter do you like?')),
                ('enable_keyboard_shortcuts', models.BooleanField(default=False)),
                ('groups', models.ManyToManyField(blank=True, help_text='The groups this user belongs to. A user will get all permissions granted to each of their groups.', related_name='user_set', related_query_name='user', to='auth.Group', verbose_name='groups')),
                ('user_permissions', models.ManyToManyField(blank=True, help_text='Specific permissions for this user.', related_name='user_set', related_query_name='user', to='auth.Permission', verbose_name='user permissions')),
            ],
            options={
                'verbose_name': 'user',
                'verbose_name_plural': 'users',
                'abstract': False,
            },
            managers=[
                ('objects', django.contrib.auth.models.UserManager()),
            ],
        ),
        migrations.CreateModel(
            name='Puzzle',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=500)),
                ('codename', models.CharField(blank=True, help_text="A non-spoilery name if you're concerned about the name being a spoiler. Leave empty otherwise.", max_length=500)),
                ('discord_channel_id', models.CharField(blank=True, max_length=19)),
                ('authors_addl', models.CharField(blank=True, help_text="The second line of author credits. Only use in cases where a standard author credit isn't accurate.", max_length=200)),
                ('needed_editors', models.IntegerField(default=2)),
                ('status', models.CharField(choices=[('II', 'Initial Idea'), ('AE', 'Awaiting Approval By EIC'), ('ND', 'EICs are Discussing'), ('WR', 'Waiting for Round to Open'), ('AR', 'Awaiting Input By Editor'), ('ID', 'Idea in Development'), ('AA', 'Awaiting Answer'), ('W', 'Writing (Answer Assigned)'), ('WF', 'Writing (Answer Flexible)'), ('AT', 'Awaiting Approval for Testsolving'), ('T', 'Ready to be Testsolved'), ('TR', 'Awaiting Testsolve Review'), ('R', 'Revising (Needs Testsolving)'), ('RP', 'Revising (Done with Testsolving)'), ('AO', 'Awaiting Approval (Done with Testsolving)'), ('NS', 'Needs Solution'), ('AS', 'Awaiting Solution Approval'), ('PB', 'Postproduction Blocked'), ('BT', 'Postproduction Blocked On Tech Request'), ('NP', 'Ready for Postprodding'), ('PP', 'Actively Postprodding'), ('AP', 'Awaiting Approval After Postprod'), ('NF', 'Needs Factcheck'), ('NR', 'Needs Final Revisions'), ('NC', 'Needs Copy Edits'), ('NH', 'Needs Hints'), ('AH', 'Awaiting Hints Approval'), ('D', 'Done'), ('DF', 'Deferred'), ('X', 'Dead')], default='II', max_length=2)),
                ('status_mtime', models.DateTimeField(editable=False)),
                ('last_updated', models.DateTimeField(auto_now=True)),
                ('summary', models.TextField(blank=True, help_text="A **non-spoilery description.** For potential testsolvers to get a sense if it'll be something they enjoy (without getting spoiled). Useful to mention: how long it'll take, how difficult it is, good for 1 solver or for a group, etc.")),
                ('description', models.TextField(help_text='A **spoilery description** of how the puzzle works.')),
                ('editor_notes', models.TextField(blank=True, help_text='A **succinct list** of mechanics and themes used.', verbose_name='Mechanics')),
                ('notes', models.TextField(blank=True, help_text='Notes and requests to the editors, like for a particular answer or inclusion in a particular round.')),
                ('priority', models.IntegerField(choices=[(1, 'Very High'), (2, 'High'), (3, 'Medium'), (4, 'Low'), (5, 'Very Low')], default=3)),
                ('content', models.TextField(blank=True, help_text='The puzzle itself. An external link is fine.')),
                ('solution', models.TextField(blank=True)),
                ('is_meta', models.BooleanField(default=False, help_text='Check the box if yes.', verbose_name='Is this a meta?')),
            ],
        ),
        migrations.CreateModel(
            name='PuzzleTag',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=500)),
                ('description', models.TextField(blank=True)),
                ('important', models.BooleanField(default=False, help_text='Important tags are displayed prominently with the puzzle title.')),
            ],
        ),
        migrations.CreateModel(
            name='SiteSetting',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=100, unique=True)),
                ('value', models.TextField()),
            ],
        ),
        migrations.CreateModel(
            name='TestsolveSession',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('started', models.DateTimeField(auto_now_add=True)),
                ('joinable', models.BooleanField(default=True, help_text='Whether this puzzle is advertised to other users as a session they can join.')),
                ('notes', models.TextField(blank=True)),
                ('puzzle', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='testsolve_sessions', to='puzzle_editing.puzzle')),
            ],
        ),
        migrations.CreateModel(
            name='TestsolveParticipation',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('started', models.DateTimeField(auto_now_add=True)),
                ('ended', models.DateTimeField(blank=True, null=True)),
                ('fun_rating', models.IntegerField(blank=True, null=True)),
                ('difficulty_rating', models.IntegerField(blank=True, null=True)),
                ('hours_spent', models.FloatField(help_text='**Hours spent**. Your best estimate of how many hours you spent on this puzzle. Decimal numbers are allowed.', null=True)),
                ('clues_needed', models.TextField(blank=True, help_text='Did you solve the complete puzzle before getting the answer, or did you shortcut, and if so, how much remained unsolved?', null=True)),
                ('aspects_enjoyable', models.TextField(blank=True, help_text='What parts of the puzzle were particularly enjoyable, if any?', null=True)),
                ('aspects_unenjoyable', models.TextField(blank=True, help_text='What parts of the puzzle were not enjoyable, if any?', null=True)),
                ('aspects_accessibility', models.TextField(blank=True, help_text='If you have physical issues such as a hearing impairment, vestibular disorder, etc., what problems did you encounter with this puzzle, if any?', null=True)),
                ('technical_issues', models.BooleanField(default=False, help_text='Did you encounter any technical problems with any aspect of the puzzle, including problems with your browser, any assistive device, etc. as well as any puzzle-specific tech?')),
                ('technical_issues_device', models.TextField(blank=True, help_text='**If Yes:** What type of device was the issue associated with? Please be as specific as possible (PC vs Mac, what browser, etc', null=True)),
                ('technical_issues_description', models.TextField(blank=True, help_text='**If Yes:** Please describe the issue', null=True)),
                ('instructions_overall', models.BooleanField(default=True, help_text='Were the instructions clear?', null=True)),
                ('instructions_feedback', models.TextField(blank=True, help_text='**If No:** What was confusing about the instructions?', null=True)),
                ('flavortext_overall', models.CharField(choices=[('helpful', 'It was helpful and appropriate'), ('too_leading', 'It was too leading'), ('not_helpful', 'It was not helpful'), ('confused', 'It confused us, or led us in a wrong direction'), ('none_but_ok', 'There was no flavor text, and that was fine'), ('none_not_ok', 'There was no flavor text, and I would have liked some')], help_text='Which best describes the flavor text?', max_length=20, null=True)),
                ('flavortext_feedback', models.TextField(blank=True, help_text='**If Helpful:** How did the flavor text help?', null=True)),
                ('stuck_overall', models.BooleanField(default=False, help_text='**Were you stuck at any point?** E.g. not sure how to start, not sure which data to gather, etc.')),
                ('stuck_points', models.TextField(blank=True, help_text='**If Yes:** Where did you get stuck? List as many places as relevant.', null=True)),
                ('stuck_time', models.FloatField(blank=True, help_text='**If Yes:** For about how long were you stuck?', null=True)),
                ('stuck_unstuck', models.TextField(blank=True, help_text='**If Yes:** What helped you get unstuck? Was it a satisfying aha?', null=True)),
                ('errors_found', models.TextField(blank=True, help_text='What errors, if any, did you notice in the puzzle?', null=True)),
                ('suggestions_change', models.TextField(blank=True, help_text='Do you have suggestions to change the puzzle? Please explain why your suggestion(s) will help.', null=True)),
                ('suggestions_keep', models.TextField(blank=True, help_text='Do you have suggestions for things that should definitely stay in the puzzle? Please explain what you like about them.', null=True)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='participations', to='puzzle_editing.testsolvesession')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='testsolve_participations', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='TestsolveGuess',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('guess', models.TextField(blank=True, max_length=500)),
                ('correct', models.BooleanField()),
                ('date', models.DateTimeField(auto_now_add=True)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='guesses', to='puzzle_editing.testsolvesession')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='guesses', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name_plural': 'testsolve guesses',
            },
        ),
        migrations.CreateModel(
            name='StatusSubscription',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('II', 'Initial Idea'), ('AE', 'Awaiting Approval By EIC'), ('ND', 'EICs are Discussing'), ('WR', 'Waiting for Round to Open'), ('AR', 'Awaiting Input By Editor'), ('ID', 'Idea in Development'), ('AA', 'Awaiting Answer'), ('W', 'Writing (Answer Assigned)'), ('WF', 'Writing (Answer Flexible)'), ('AT', 'Awaiting Approval for Testsolving'), ('T', 'Ready to be Testsolved'), ('TR', 'Awaiting Testsolve Review'), ('R', 'Revising (Needs Testsolving)'), ('RP', 'Revising (Done with Testsolving)'), ('AO', 'Awaiting Approval (Done with Testsolving)'), ('NS', 'Needs Solution'), ('AS', 'Awaiting Solution Approval'), ('PB', 'Postproduction Blocked'), ('BT', 'Postproduction Blocked On Tech Request'), ('NP', 'Ready for Postprodding'), ('PP', 'Actively Postprodding'), ('AP', 'Awaiting Approval After Postprod'), ('NF', 'Needs Factcheck'), ('NR', 'Needs Final Revisions'), ('NC', 'Needs Copy Edits'), ('NH', 'Needs Hints'), ('AH', 'Awaiting Hints Approval'), ('D', 'Done'), ('DF', 'Deferred'), ('X', 'Dead')], max_length=2)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='Round',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=500)),
                ('description', models.TextField(blank=True)),
                ('editors', models.ManyToManyField(blank=True, related_name='editors', to=settings.AUTH_USER_MODEL)),
                ('spoiled', models.ManyToManyField(blank=True, help_text="Users spoiled on the round's answers.", related_name='spoiled_rounds', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='PuzzleVisited',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateTimeField(auto_now=True)),
                ('puzzle', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='puzzle_editing.puzzle')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='PuzzlePostprod',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.CharField(help_text="The part of the URL on the hunt site referrring to this puzzle. E.g. for https://puzzle.hunt/puzzle/fifty-fifty, this would be 'fifty-fifty'.", max_length=50, validators=[django.core.validators.RegexValidator(regex='[^<>#%"\\\'|{})(\\[\\]\\/\\\\\\^?=`;@&, ]{1,50}')])),
                ('zip_file', models.FileField(blank=True, help_text='A zip file as described above. Leave it blank to keep it the same if you already uploaded one and just want to change the metadata.', null=True, upload_to=lambda instance, _: f"puzzle_postprods/puzzle_{instance.puzzle_id}.zip", validators=[django.core.validators.FileExtensionValidator(['zip'])])),
                ('authors', models.CharField(help_text='The puzzle authors, as displayed on the solution page', max_length=200)),
                ('complicated_deploy', models.BooleanField(help_text="Check this box if your puzzle involves a serverside component of some sort, and it is not entirely contained in the zip file. If you don't know what this means, you probably don't want to check this box.")),
                ('mtime', models.DateTimeField(auto_now=True)),
                ('puzzle', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='postprod', to='puzzle_editing.puzzle')),
            ],
        ),
        migrations.CreateModel(
            name='PuzzleComment',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateTimeField(auto_now_add=True)),
                ('last_updated', models.DateTimeField(auto_now=True)),
                ('is_system', models.BooleanField()),
                ('content', models.TextField(blank=True, help_text='The content of the comment. Should probably only be blank if the status_change is set.')),
                ('status_change', models.CharField(blank=True, choices=[('II', 'Initial Idea'), ('AE', 'Awaiting Approval By EIC'), ('ND', 'EICs are Discussing'), ('WR', 'Waiting for Round to Open'), ('AR', 'Awaiting Input By Editor'), ('ID', 'Idea in Development'), ('AA', 'Awaiting Answer'), ('W', 'Writing (Answer Assigned)'), ('WF', 'Writing (Answer Flexible)'), ('AT', 'Awaiting Approval for Testsolving'), ('T', 'Ready to be Testsolved'), ('TR', 'Awaiting Testsolve Review'), ('R', 'Revising (Needs Testsolving)'), ('RP', 'Revising (Done with Testsolving)'), ('AO', 'Awaiting Approval (Done with Testsolving)'), ('NS', 'Needs Solution'), ('AS', 'Awaiting Solution Approval'), ('PB', 'Postproduction Blocked'), ('BT', 'Postproduction Blocked On Tech Request'), ('NP', 'Ready for Postprodding'), ('PP', 'Actively Postprodding'), ('AP', 'Awaiting Approval After Postprod'), ('NF', 'Needs Factcheck'), ('NR', 'Needs Final Revisions'), ('NC', 'Needs Copy Edits'), ('NH', 'Needs Hints'), ('AH', 'Awaiting Hints Approval'), ('D', 'Done'), ('DF', 'Deferred'), ('X', 'Dead')], help_text="Any status change caused by this comment. Only used for recording history and computing statistics; not a source of truth (i.e. the puzzle will still store its current status, and this field's value on any comment doesn't directly imply anything about that in any technically enforced way).", max_length=2)),
                ('author', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='comments', to=settings.AUTH_USER_MODEL)),
                ('puzzle', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='comments', to='puzzle_editing.puzzle')),
                ('testsolve_session', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='comments', to='puzzle_editing.testsolvesession')),
            ],
        ),
        migrations.CreateModel(
            name='PuzzleAnswer',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('answer', models.CharField(blank=True, max_length=500)),
                ('notes', models.TextField(blank=True)),
                ('round', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='answers', to='puzzle_editing.round')),
            ],
        ),
        migrations.AddField(
            model_name='puzzle',
            name='answers',
            field=models.ManyToManyField(blank=True, related_name='puzzles', to='puzzle_editing.PuzzleAnswer'),
        ),
        migrations.AddField(
            model_name='puzzle',
            name='authors',
            field=models.ManyToManyField(blank=True, related_name='authored_puzzles', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='puzzle',
            name='editors',
            field=models.ManyToManyField(blank=True, related_name='editing_puzzles', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='puzzle',
            name='factcheckers',
            field=models.ManyToManyField(blank=True, related_name='factchecking_puzzles', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='puzzle',
            name='postprodders',
            field=models.ManyToManyField(blank=True, related_name='postprodding_puzzles', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='puzzle',
            name='spoiled',
            field=models.ManyToManyField(blank=True, help_text='Users spoiled on the puzzle.', related_name='spoiled_puzzles', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='puzzle',
            name='tags',
            field=models.ManyToManyField(blank=True, related_name='puzzles', to='puzzle_editing.PuzzleTag'),
        ),
        migrations.CreateModel(
            name='Hint',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order', models.FloatField(help_text='Order in the puzzle - use 0 for a hint at the very beginning of the puzzle, or 100 for a hint on extraction, and then do your best to extrapolate in between. Decimals are okay. For multiple subpuzzles, assign a whole number to each subpuzzle and use decimals off of that whole number for multiple hints in the subpuzzle.')),
                ('keywords', models.CharField(blank=True, help_text="Comma-separated keywords to look for in hunters' hint requests before displaying this hint suggestion", max_length=100)),
                ('content', models.CharField(help_text='Canned hint to give a team (can be edited by us before giving it)', max_length=1000)),
                ('puzzle', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='hints', to='puzzle_editing.puzzle')),
            ],
            options={
                'ordering': ['order'],
            },
        ),
        migrations.CreateModel(
            name='SupportRequest',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('team', models.CharField(choices=[('ART', '🎨 Art'), ('ACC', '🔎 Accessibility'), ('TECH', '👩🏽\u200d💻 Tech')], max_length=4)),
                ('status', models.CharField(choices=[('NO', 'No need'), ('REQ', 'Requested'), ('APP', 'Approved'), ('BLOK', 'Blocking'), ('COMP', 'Completed'), ('X', 'Cancelled')], default='REQ', max_length=4)),
                ('team_notes', models.TextField(blank=True)),
                ('team_notes_mtime', models.DateTimeField(null=True)),
                ('author_notes', models.TextField(blank=True)),
                ('author_notes_mtime', models.DateTimeField(null=True)),
                ('outdated', models.BooleanField(default=False)),
                ('author_notes_updater', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='support_author_requests', to=settings.AUTH_USER_MODEL)),
                ('puzzle', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='puzzle_editing.puzzle')),
                ('team_notes_updater', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='support_team_requests', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('puzzle', 'team')},
            },
        ),
        migrations.CreateModel(
            name='PuzzleCredit',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text', models.TextField(blank=True)),
                ('credit_type', models.CharField(choices=[('ART', 'Art'), ('TCH', 'Tech'), ('OTH', 'Other')], default=('ART', 'Art'), max_length=3)),
                ('puzzle', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='other_credits', to='puzzle_editing.puzzle')),
                ('users', models.ManyToManyField(blank=True, related_name='other_credits', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('puzzle', 'credit_type')},
            },
        ),
        migrations.CreateModel(
            name='CommentReaction',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('emoji', models.CharField(max_length=8)),
                ('comment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reactions', to='puzzle_editing.puzzlecomment')),
                ('reactor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reactions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('emoji', 'comment', 'reactor')},
            },
        ),
    ]
