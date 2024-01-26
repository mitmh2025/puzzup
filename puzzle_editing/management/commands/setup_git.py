import os
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from git.repo.base import Repo


class Command(BaseCommand):
    help = """Set up git repository, deleting any directory at HUNT_REPO"""

    def handle(self, *args, **options):
        if not (settings.HUNT_REPO_URL and settings.HUNT_REPO):
            print(f"Missing one of: {settings.HUNT_REPO_URL=}, {settings.HUNT_REPO=}")
            return

        if settings.HUNT_REPO_PATH.exists():
            shutil.rmtree(settings.HUNT_REPO_PATH)

        ssh_key_path = Path(settings.SSH_KEY)
        if not ssh_key_path.is_file():
            ssh_key_path.parent.mkdir(exist_ok=True, parents=True)
            with os.fdopen(
                os.open(
                    ssh_key_path.expanduser(),
                    os.O_WRONLY | os.O_CREAT,
                    0o600,
                ),
                "w",
            ) as handle:
                handle.write(os.environ.get("BUILDPACK_SSH_KEY") or "")

            with Path("~/.ssh/config").expanduser().open("a") as config:
                config.write("Host github.com\n")
                config.write("  StrictHostKeyChecking no\n")
                config.write("  UserKnownHostsFile /dev/null\n")
                config.write("  LogLevel ERROR\n")

        git_ssh_id_file = ssh_key_path.expanduser()
        git_ssh_cmd = "ssh -i %s" % git_ssh_id_file

        # We might have been pre-empted. Strictly, should obtain a lock, but...
        # Hopefully this isn't too raceful.
        if not settings.HUNT_REPO_PATH.exists():
            repo = Repo.clone_from(
                settings.HUNT_REPO_URL,
                settings.HUNT_REPO_PATH,
                env={"GIT_SSH_COMMAND": git_ssh_cmd},
                depth=1,
            )
            repo.remotes.origin.set_url(settings.HUNT_REPO_URL)

        os.system(
            f'cd {settings.HUNT_REPO_PATH} && git config user.name "TTBNL Puzzup"'
        )
        os.system(
            f"cd {settings.HUNT_REPO_PATH} && git config user.email"
            f' "{settings.AUTOPOSTPROD_EMAIL}"'
        )
