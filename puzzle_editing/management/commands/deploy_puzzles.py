import json
import shutil
from pathlib import Path

import git
from django.conf import settings
from django.core import management
from django.core.management.base import BaseCommand, CommandError

from puzzle_editing.models import PuzzlePostprod


class Command(BaseCommand):
    help = """Sync puzzles into Hunt Repository."""

    def handle(self, *args, **options):
        if not Path(settings.HUNT_REPO).exists() and settings.HUNT_REPO:
            management.call_command("setup_git")

        repo = git.Repo.init(settings.HUNT_REPO)
        if (
            repo.is_dirty()
            or len(repo.untracked_files) > 0
            or repo.head.reference.name not in ["master", "main"]
        ):
            msg = f"Repository is in a broken state. [{repo.is_dirty()} / {repo.untracked_files} / {repo.head.reference.name}]"
            raise CommandError(msg)

        origin = repo.remotes.origin
        origin.pull()

        puzzleFolder = Path(settings.HUNT_REPO) / "hunt/data/puzzle"

        shutil.rmtree(puzzleFolder)
        Path(puzzleFolder).mkdir(parents=True, exist_ok=True)

        for pp in PuzzlePostprod.objects.all():
            metadata = pp.puzzle.metadata
            puzzlePath = Path(puzzleFolder) / pp.slug
            puzzlePath.mkdir(parents=True, exist_ok=True)
            # zipFile = pp.zip_file
            # with ZipFile(zipFile) as zf:
            #    zf.extractall(puzzlePath)
            with (Path(puzzlePath) / "metadata.json").open("w") as mf:
                json.dump(metadata, mf)
            repo.git.add(puzzlePath)

        if repo.is_dirty() or len(repo.untracked_files) > 0:
            repo.git.add(update=True)
            repo.git.add(A=True)
            repo.git.commit("-m", "Postprodding all puzzles.")
            origin.push()
