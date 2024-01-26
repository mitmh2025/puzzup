from django.core.management.base import BaseCommand

from puzzle_editing import utils


class Command(BaseCommand):
    help = """Export metadata as JSON and push to Hunt repo."""

    def handle(self, *args, **options):
        utils.export_all()
