import logging
from pathlib import Path

import git
from django.conf import settings
from django.core import management
from git.exc import CommandError

logger = logging.getLogger(__name__)

ASSETS_PUZZLE_DIR = "client/assets/puzzles/"
ASSETS_SOLUTION_DIR = "client/assets/solutions/"
PUZZLE_DIR = "client/pages/puzzles/"
SOLUTION_DIR = "client/pages/solutions/"
FIXTURE_DIR = "server/tph/fixtures/puzzles/"


class GitRepo:
    @classmethod
    def make_dir(cls, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def hunt_dir(cls, directory: str):
        if not settings.HUNT_REPO:
            msg = "HUNT_REPO is not set"
            raise ValueError(msg)
        return cls.make_dir(settings.HUNT_REPO / directory)

    @classmethod
    def puzzle_path(cls, slug: str, puzzle_dir: str = PUZZLE_DIR):
        return cls.make_dir(cls.hunt_dir(puzzle_dir) / slug)

    @classmethod
    def solution_path(cls, slug: str):
        return cls.make_dir(cls.hunt_dir(SOLUTION_DIR) / slug)

    @classmethod
    def assets_puzzle_path(cls, slug: str):
        return cls.make_dir(cls.hunt_dir(ASSETS_PUZZLE_DIR) / slug)

    @classmethod
    def assets_solution_path(cls, slug: str):
        return cls.make_dir(cls.hunt_dir(ASSETS_SOLUTION_DIR) / slug)

    @classmethod
    def fixture_path(cls):
        return cls.make_dir(cls.hunt_dir(FIXTURE_DIR))

    def __init__(self, branch=None):
        if not branch:
            branch = settings.HUNT_REPO_BRANCH

        if not settings.DEBUG:
            # Initialize repo if it does not exist.
            if not settings.HUNT_REPO.exist() and settings.HUNT_REPO:
                management.call_command("setup_git")

        self.repo = git.Repo.init(settings.HUNT_REPO)
        if self.repo.bare:
            self.origin = self.repo.remotes.origin
            self.origin.pull()

        # Check out and pull latest branch
        self.repo.git.checkout(branch)
        self.branch = branch
        self.origin = self.repo.remotes.origin
        self.origin.pull()
        self.health_check()

    def health_check(self):
        if (
            self.repo.is_dirty()
            or len(self.repo.untracked_files) > 0
            or self.repo.head.reference.name != self.branch
        ):
            msg = "Repository is in a broken state. [{} / {} / {}]".format(
                self.repo.is_dirty(),
                self.repo.untracked_files,
                self.repo.head.reference.name,
            )
            raise CommandError(msg)

    @classmethod
    def has_remote_branch(cls, *branch_names):
        g = git.cmd.Git()
        return any(g.ls_remote(settings.HUNT_REPO_URL, name) for name in branch_names)

    def checkout_branch(self, branch_name):
        self.branch = branch_name
        if self.has_remote_branch(settings.HUNT_REPO_URL, branch_name):
            self.repo.git.checkout(branch_name)
        else:
            self.repo.git.checkout("-B", branch_name)
        self.health_check()

    def pre_commit(self) -> bool:
        """Runs pre-commit and returns true if it fails."""
        if not settings.HUNT_REPO:
            msg = "HUNT_REPO is not set"
            raise ValueError(msg)
        if not (settings.HUNT_REPO / ".git/hooks/pre-commit").is_file():
            logger.warning("Pre-commit skipped because hooks not installed.")
        try:
            git.index.fun.run_commit_hook("pre-commit", self.repo.index)
        except git.exc.HookExecutionError:
            return True  # pre-commit failed
        return False

    def commit(self, message) -> bool:
        if self.repo.is_dirty() or len(self.repo.untracked_files) > 0:
            self.repo.git.add(update=True)
            self.repo.git.add(A=True)

            # Run pre-commit on index, if it exists.
            if self.pre_commit():
                self.repo.git.add(update=True)
                self.repo.git.add(A=True)

            self.repo.git.commit("-m", message)
            return True
        return False

    def push(self):
        if settings.DEBUG:  # Don't push locally.
            logger.debug("Skipping push due to DEBUG mode")
            return

        if self.branch in ("main", "master"):
            self.origin.push()
        else:
            self.repo.git.push("--set-upstream", self.repo.remote().name, self.branch)
