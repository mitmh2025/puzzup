import json
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from puzzle_editing.models import Puzzle


class Command(BaseCommand):
    help = """Export hints as JSON."""

    def handle(self, *args, **options):
        place = Path(settings.HUNT_REPO, "puzzle")
        for puzzledir in os.listdir(place):
            datafile = Path(place, puzzledir, "metadata.json")
            outdata = []
            try:
                with datafile.open() as data:
                    metadata = json.load(data)
                    puzzle = Puzzle.objects.get(id=metadata["puzzle_idea_id"])
                    for hint in puzzle.hints.all():
                        outdata.append(
                            [hint.order, hint.keywords.split(","), hint.content]
                        )
            except FileNotFoundError:
                pass
            except Exception as e:
                print(datafile, e)
                # sys.exit(1)
            hintfilename = Path(place, puzzledir, "hints.json")
            if outdata:
                with hintfilename.open("w") as hintfile:
                    json.dump(outdata, hintfile)
