# PuzzUp

A Django app for editing/testing puzzles for a puzzlehunt. Cloned from [PuzzLord](https://github.com/galacticpuzzlehunt/puzzlord), which was a reincarnate of [Puzzletron](https://github.com/mysteryhunt/puzzle-editing/).

# Design / How it works

Some goals and consequences of PuzzUp's design:

### Simplicity and low maintenance costs, with the goal of letting PuzzUp last many years without continuous development effort

- Few roles
- JavaScript dependence is minimized. When useful, use modern JS and don't try to support old browsers.
- To reduce code, rely on Django built-in features when possible.

### Connection between PuzzUp and hunt repo

- Upload postprod (aka drafting puzzles) files and commit directly to the repo
- Export metadata and hints directly to the hunt repo
- Not yet implemented: partial answers

### Permissiveness, which dovetails with simplicity, is OK because we generally trust our writing team

- Anybody can change the status of puzzles and add/remove themselves or other people to/from any puzzle-related role (author, discussion editor, factchecker, postprodder, spoiled).

### Spoiler safety

- Puzzles can have custom codenames, which PuzzUp will try to show to non-spoiled people.
- PuzzUp always shows you an interstitial and requires you to confirm that you would like to be spoiled. You cannot accidentally spoil yourself on puzzles or metas.

### Other workflow points

- Factchecking comes after postprodding
- Some additional useful puzzle statuses

# Installation

## Requirements

- Python 3.10+
- poetry
- Postgres
- [Dart sass](https://github.com/sass/dart-sass)

## Local setup

Make sure you have **Python 3.x** and **poetry** installed.

```
pip install poetry
```

`cd` into the root of this repo

Install the **requirements**:

```
poetry env use python3
poetry shell
poetry install
```

Create a folder for **logs** to go in:

```
mkdir logs
```

Duplicate `.env.template` to a file called `.env`.

`cp .env.template .env`

Inside your `.env` file, update the following values:

- Change the `PUZZUP_SECRET` to something long, random, and highly secure.

- Change the `SITE_PASSWORD` if desired. (This is used in the user registration flow.)

You can skip the other environment variables for now.

Use Postgres to **create a new database**:

```
createdb puzzup
```

**Migrate** by running

```
./manage.py migrate
```

Install pre-commit hooks (may need to `pip3 install pre-commit` first): **Note: we skipped precommit in the latest implementation**.

```
pre-commit install
```

Load user and group fixtures.

```
inv load-users
```

If all went well, run this to start the dev server:

```
./manage.py runserver
```

The local IP and port should be printed to stdout, and it should helpfully tell you that you're running Django 3.1.x.

### Server set-up

You only need to do this once, after you clone the repo and are setting it up for your team.

**Set the site password**

`SITE_PASSWORD` as an environment variable. (This is what you will give out to users to let them register accounts)

**Define the sender and reply-to email**

in `puzzle_editing/messaging.py`

**Define the ALLOWED_HOSTS**

in `settings/staging.py` and `settings/prod.py`

## Setting up the server

**Spin up server instance** and pull latest code.

We used Heroku, and the following buildpacks in this order:

1. [Python poetry](https://elements.heroku.com/buildpacks/moneymeets/python-poetry-buildpack)
2. [Dart sass buildpack](https://elements.heroku.com/buildpacks/spectrum-md/heroku-buildpack-dartsass)
3. Official heroku/python
4. [heroku-buildpack-django-sass](https://elements.heroku.com/buildpacks/drpancake/heroku-buildpack-django-sass)

**Environment vars for integrating Discord**

- `DISCORD_APP_PUBLIC_KEY`
- `DISCORD_BOT_TOKEN`
- `DISCORD_CLIENT_ID`
- `DISCORD_CLIENT_SECRET`
- `DISCORD_GUILD_ID`

**Other environment variables**

- `BUILDPACK_SSH_KEY` = for integration with Hunt site repo
- `DATABASE_URL` = full path w/ credentials, to your DB
- `DJANGO_SETTINGS_MODULE` = `settings.prod`
- `HUNT_REPO` = path to Hunt repo. `/tmp/hunt` works.
- `POSTPROD_URL` = staging URl
- `PUZZUP_SECRET` = random string
- `SITE_PASSWORD` = needed for your users use to register
- `SSH_KEY_PATH` = Likely `~/.ssh/id_rsa`
- `AWS_ACCESS_KEY_ID` = used for S3 integration
- `AWS_SECRET_ACESS_KEY` = used for S3 integration

**Install auth fixture**

If you're using Heroku, you can use `inv load-all-prod`. Otherwise, SSH into your PuzzUp server and run `./manage.py loaddata auth`.

## Installing packages

If you ever need to install more pip packages for this project, make sure you're in the poetry shell. Then just type

```
poetry add [package-name]
```

It'll automatically get added to the **pyproject.toml** and update **poetry.lock**.

## File Uploads

We use AWS S3 to store file uploads. Uploads are stored in the `ttbnl-uploads` bucket. The AWS IAM user `puzzup` is used in production and has access to this bucket.

The server generates [presigned URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/ShareObjectPreSignedURL.html) to provide upload and download links for assets.

## Running Tests

To run all tests:

```
./manage.py test
```

# FAQ

## Why are things broken?

- Did you forget to go into `poetry shell`
- Did you forget to install all the requirements?
- Are you running python 2? If `python version` starts with 2, you might need to install python 3, or to swap `python` to `python3` at the beginning of your commands.

## How do I use `manage.py`?

- You can always run `python manage.py --help` to get a list of subcommands
- To create a superuser (so you can access the `/admin` page locally) run `python manage.py createsuperuser`
- If you get a warning (red text) about making migrations run `python manage.py migrate`

## Where are things?

The Django project (currently) has only one app, called "puzzle_editing". Most business logic and UI lives inside the `puzzle_editing` directory.

## Where are static files?

Static files (CSS etc.) live in `puzzle_editing/static`.

# Discord Integration

Puzzup integrates a fair bit with Discord, allowing for channels to be managed. To use it, there's a little bit of setup. This integration should be stable through version 8 of the Discord API.

# Google Drive Integration

Google Drive integration is configured by the `DRIVE_SETTINGS` Django setting.

Set the `GOOGLE_CREDENTIALS_PATH` to the location of your service account configuration file (do not check this into git), or set `GOOGLE_CREDENTIALS` to a JSON string containing service account credentials.

## Setup

You will need to create a [Discord application here](https://discord.com/developers/applications).

- Set this application's **Interactions endpoint URL** to `https://your-puzzup-url/slashcommands`
- Enable a bot for your application.

Make a note of:

- your discord server ID (you can switch on Developer Mode and the right-click > Copy ID to do this; called a guild ID below)
- your bot's bot token
- your application's public key
- your application's client ID
- your application's client secret

Set necessary **Discord environment variables**. See above.

By default, `DISCORD_OAUTH_SCOPES` is set to `identify` only, since that is all the site needs at this time.

You'll need to add a bot to your server with the following permissions - the below link will do this for you, just add your client ID:

```
https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=268438544&scope=applications.commands%20bot
```

- Manage channels - needed to rename, create and reorganise channels
- Manage roles - needed to override visibility for users and roles on puzzle channels
- Commands - needed for your users to be able to invoke the below slash commands

Finally, make a `POST` request to `https://discord.com/api/v8/applications/YOUR_APPLICATION_CLIENT_ID/guilds/YOUR_GUILD_ID/commands` with the below JSON payload, authorised with your bot token (Authorization: Bot BOT_TOKEN)

Alternately, you can instead make the `POST` request to `https://discord.com/api/v8/applications/YOUR_APPLICATION_CLIENT_ID/commands` and then wait up to an hour for commands to propagate. This will enable your commands globally, but there's really no need for this.

```json
{
  "name": "up",
  "description": "Interact with Puzzup",
  "options": [
    {
      "type": 1,
      "name": "create",
      "description": "Create a puzzle in Puzzup linked to the current channel"
    },
    {
      "type": 1,
      "name": "archive",
      "description": "Archive the current channel"
    },
    {
      "type": 1,
      "name": "info",
      "description": "Get information about the current channel's puzzle"
    },
    {
      "type": 1,
      "name": "url",
      "description": "Get the link for the current channel's puzzle"
    }
  ]
}
```

# Credits

Forked from [PuzzLord](https://github.com/galacticpuzzlehunt/puzzlord), which is maintained by [@betaveros](https://github.com/betaveros). Lots of infrastructure by [@mitchgu](https://github.com/mitchgu). Many contributions from [@jakob223](https://github.com/jakob223), [@dvorak42](https://github.com/dvorak42), and [@fortenforge](https://github.com/fortenforge).

UI reskin by [Sandy Weisz](https://github.com/santheo). Lots of work by Discord improvements by [James Sugrono](https://github.com/jimsug).

Contains a lightly modified copy of [sorttable.js](https://kryogenix.org/code/browser/sorttable/), licensed under the X11 license.
