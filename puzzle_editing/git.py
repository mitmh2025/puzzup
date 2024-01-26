from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core import management
from git.cmd import Git
from git.exc import CommandError, HookExecutionError
from git.index.fun import run_commit_hook
from git.repo.base import Repo

logger = logging.getLogger(__name__)

ASSETS_PUZZLE_DIR = "client/assets/puzzles/"
ASSETS_SOLUTION_DIR = "client/assets/solutions/"
PUZZLE_DIR = "client/pages/puzzles/"
SOLUTION_DIR = "client/pages/solutions/"
FIXTURE_DIR = "server/tph/fixtures/puzzles/"


class GitRepo:
    @classmethod
    def make_dir(cls, path):
        Path(path).mkdir(parents=True, exist_ok=True)
        return path

    def hunt_dir(self, directory: str):
        return self.make_dir(Path(self.path, directory))

    def puzzle_path(self, slug: str, puzzle_dir: str = PUZZLE_DIR):
        return self.make_dir(Path(self.hunt_dir(puzzle_dir), slug))

    def solution_path(self, slug: str):
        return self.make_dir(Path(self.hunt_dir(SOLUTION_DIR), slug))

    def assets_puzzle_path(self, slug: str):
        return self.make_dir(Path(self.hunt_dir(ASSETS_PUZZLE_DIR), slug))

    def assets_solution_path(self, slug: str):
        return self.make_dir(Path(self.hunt_dir(ASSETS_SOLUTION_DIR), slug))

    def fixture_path(self):
        return self.make_dir(self.hunt_dir(FIXTURE_DIR))

    def __init__(self, branch: str = "main"):
        # Initialize repo if it does not exist.
        if not settings.HUNT_REPO_PATH.exists() and settings.HUNT_REPO:
            management.call_command("setup_git")

        base_repo = Repo.init(settings.HUNT_REPO_PATH)
        if base_repo.bare:
            base_repo.remotes.origin.pull()

        # Check out and pull latest branch into base repo
        base_repo.git.checkout(branch)
        base_repo.remotes.origin.pull()
        GitRepo.health_check(base_repo)

        # Copy out into temp directory to avoid simultaneous request clobbers
        if not settings.TMP_REPO_DIR.exists():
            settings.TMP_REPO_DIR.mkdir(parents=True)
        self.path = Path(tempfile.mkdtemp(dir=settings.TMP_REPO_DIR)) / "tph-site"
        shutil.copytree(
            settings.HUNT_REPO_PATH, self.path, dirs_exist_ok=True, symlinks=True
        )
        self.repo = Repo.init(self.path)
        self.branch = branch
        self.origin = self.repo.remotes.origin

    def __del__(self):
        if hasattr(self, "tmpdir") and self.path.exists() and not settings.DEBUG:
            shutil.rmtree(self.path)

    @staticmethod
    def health_check(repo: Repo, branch: str = settings.HUNT_REPO_BRANCH):
        if (
            repo.is_dirty()
            or len(repo.untracked_files) > 0
            or repo.head.reference.name != branch
        ):
            msg = (
                f"Repository is in a broken state. [{repo.is_dirty()=} /"
                f" {repo.untracked_files=} / {repo.head.reference.name=}]"
            )
            raise CommandError(msg)

    @classmethod
    def has_remote_branch(cls, *branch_names):
        g = Git()
        return any(g.ls_remote(settings.HUNT_REPO_URL, name) for name in branch_names)

    def checkout_branch(self, branch_name):
        self.branch = branch_name
        if self.has_remote_branch(settings.HUNT_REPO_URL, branch_name):
            self.repo.git.fetch("origin", branch_name)
            self.repo.git.checkout("FETCH_HEAD", "-B", branch_name)
        else:
            self.repo.git.checkout("-B", branch_name)
        GitRepo.health_check(self.repo, branch=branch_name)

    def pre_commit(self) -> bool:
        """Runs pre-commit and returns true if it fails."""
        if not Path(settings.HUNT_REPO_PATH, ".git/hooks/pre-commit").is_file():
            logger.warning("Pre-commit skipped because hooks not installed.")
        try:
            run_commit_hook("pre-commit", self.repo.index)
        except HookExecutionError:
            return True  # pre-commit failed
        return False

    def commit(self, message, skip_hooks: bool = False) -> bool:
        if self.repo.is_dirty() or len(self.repo.untracked_files) > 0:
            self.repo.git.add(update=True)
            self.repo.git.add(A=True)

            # Run pre-commit on index, if it exists.
            if not skip_hooks and self.pre_commit():
                self.repo.git.add(update=True)
                self.repo.git.add(A=True)

            commit_args = ["-m", message]
            if skip_hooks:
                commit_args.append("--no-verify")
            self.repo.git.commit(*commit_args)
            return True
        return False

    def push(self):
        # if settings.DEBUG:  # Don't push locally.
        #     logger.debug("Skipping push due to DEBUG mode")
        #     return

        if self.branch in ("main", "master"):
            self.origin.push()
        else:
            self.repo.git.push("--set-upstream", self.repo.remote().name, self.branch)
