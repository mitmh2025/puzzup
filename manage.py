#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

IMPORT_ERROR = """Couldn't import Django. Are you sure it's installed and
               available on your PYTHONPATH environment variable? Did you
               forget to activate a virtual environment?"""


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(IMPORT_ERROR) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
