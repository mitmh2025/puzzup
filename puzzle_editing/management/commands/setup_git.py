import os
import shutil
from pathlib import Path

import git
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = """Set up git repository, deleting any directory at HUNT_REPO"""

    def handle(self, *args, **options):
        if not (settings.HUNT_REPO_URL and settings.HUNT_REPO):
            print(f"Missing one of: {settings.HUNT_REPO_URL}, {settings.HUNT_REPO}")
            return

        if settings.HUNT_REPO.exists():
            shutil.rmtree(settings.HUNT_REPO)

        if not settings.SSH_KEY.is_file():
            with os.fdopen(
                os.open(
                    settings.SSH_KEY.expanduser(),
                    os.O_WRONLY | os.O_CREAT,
                    0o600,
                ),
                "w",
            ) as handle:
                handle.write(os.environ.get("BUILDPACK_SSH_KEY"))

            with Path("~/.ssh/config").expanduser().open("a") as config:
                config.write("Host github.com\n")
                config.write("  StrictHostKeyChecking no\n")
                config.write("  UserKnownHostsFile /dev/null\n")
                config.write("  LogLevel ERROR\n")

        os.system('git config --global user.name "Puzzup"')
        os.system(f'git config --global user.email "{settings.AUTOPOSTPROD_EMAIL}"')

        git_ssh_id_file = settings.SSH_KEY.expanduser()
        git_ssh_cmd = f"ssh -i {git_ssh_id_file}"

        repo = git.Repo.clone_from(
            settings.HUNT_REPO_URL,
            settings.HUNT_REPO,
            env={"GIT_SSH_COMMAND": git_ssh_cmd},
        )
        repo.remotes.origin.set_url(settings.HUNT_REPO_URL)
