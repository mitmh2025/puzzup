import logging
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError
from PIL import Image, UnidentifiedImageError

from puzzle_editing.git import GitRepo
from puzzle_editing.models import Puzzle, PuzzlePostprod

logger = logging.getLogger(__name__)

DEFAULT_PUZZLE_TEMPLATE = "client/templates/puzzle.template.tsx"
DEFAULT_SOLUTION_TEMPLATE = "client/templates/solution.template.tsx"


def export_all():
    try:
        repo = GitRepo()
    except Exception:
        logger.warning("Failed to instantiate git repo.", exc_info=True)
        return None

    branch_name = f"export-{int(time.time())}"
    repo.checkout_branch(branch_name)

    # Export all puzzles with an assigned answer.
    puzzles = Puzzle.objects.filter(answers__isnull=False).distinct()

    fixture_path = repo.fixture_path()
    for puzzle in puzzles:
        if puzzle.has_postprod():
            pp = puzzle.postprod
        else:
            logger.info("Creating postprod obj for %s", puzzle.name)
            pp = PuzzlePostprod(puzzle=puzzle, slug=puzzle.slug)
            pp.save()

        with (Path(fixture_path) / f"{pp.slug}.yaml").open("w") as f:
            f.write(puzzle.get_yaml_fixture())

    if repo.commit("Export puzzle fixtures"):
        repo.push()
        return branch_name

    return None


def export_puzzle(
    pp, puzzle_directory, puzzle_html="", solution_html="", max_image_width=None
):
    """Writes and commits puzzle template, solution, and yaml fixtures into the hunt repo."""
    try:
        repo = GitRepo()
    except Exception:
        logger.warning("Failed to instantiate git repo.", exc_info=True)
        return

    branch_name = pp.slug
    repo.checkout_branch(branch_name)

    puzzle_path = repo.puzzle_path(pp.slug, puzzle_directory) / "index.tsx"
    if not puzzle_path.exists() or puzzle_html:
        assets_path = repo.assets_puzzle_path(pp.slug)
        puzzle_html, images = download_images(puzzle_html, assets_path, max_image_width)
        with puzzle_path.open("w") as f:
            f.write(
                get_puzzle_html(
                    pp.puzzle.round.puzzle_template or DEFAULT_PUZZLE_TEMPLATE,
                    puzzle_html,
                    pp.slug,
                    images=images,
                )
            )

    solution_path = repo.solution_path(pp.slug) / "index.tsx"
    if not solution_path.exists() or solution_html:
        assets_path = repo.assets_solution_path(pp.slug)
        solution_html, images = download_images(
            solution_html, assets_path, max_image_width
        )
        with solution_path.open("w") as f:
            f.write(
                get_puzzle_html(
                    pp.puzzle.round.solution_template or DEFAULT_SOLUTION_TEMPLATE,
                    solution_html,
                    pp.slug,
                    images=images,
                    title=pp.puzzle.name,
                    answer=pp.puzzle.answer,
                    authors=pp.puzzle.author_byline,
                )
            )

    fixture_path = repo.fixture_path()
    with (fixture_path / f"{pp.slug}.yaml").open("w") as f:
        f.write(pp.puzzle.get_yaml_fixture())

    if repo.commit(f"Postprodding '{pp.slug}'"):
        repo.push()
        return branch_name

    return ""


def download_images(html: str, assets_path: Path, max_image_width: int):
    if not settings.HUNT_REPO_CLIENT:
        msg = "HUNT_REPO is not set"
        raise ValueError(msg)

    # Search for all images in HTML
    images = re.findall(r'src="([^"]+)"', html)
    new_images = []
    image_map = {}

    for i, src in enumerate(images):
        full_assets_path = assets_path / f"{i}.png"
        relative_path = full_assets_path.relative_to(settings.HUNT_REPO_CLIENT)

        # Download the image and save it to the hunt repo
        try:
            urllib.request.urlretrieve(src, full_assets_path)
            # Resize the image, while preserving aspect ratio.
            image = Image.open(full_assets_path)
            if image.width > max_image_width:
                aspect_ratio = image.width / float(image.height)
                image = image.resize(
                    (max_image_width, int(max_image_width / aspect_ratio)),
                    resample=Image.BICUBIC,
                )
                image.save(full_assets_path, format="PNG", optimize=True)

            new_images.append((relative_path, f"image{i}"))
        except (urllib.error.URLError, UnidentifiedImageError):
            logger.exception("Failed to download asset from %s", src)
            new_images.append(
                (Path("FAILED/TO/DOWNLOAD/PLS/IMPORT/MANUALLY.png"), f"image{i}")
            )

        # Save the relative path to the image_map
        image_map[src] = f"image{i}"

    def replace_img_src(matchobj):
        return f"src={{{image_map[matchobj.group(1)]}}}"

    # Replace images with new variable names
    if images:
        html = re.sub(r'src="([^"]+)"', replace_img_src, html)

    return html, new_images


def get_puzzle_html(template, html, slug, images=None, title="", answer="", authors=""):
    template_file = settings.HUNT_REPO / template

    try:
        with template_file.open() as f:
            puzzle_tsx = f.read()

            # Add imports to top of file
            imports = [f"import {var} from '{path}';" for (path, var) in (images or [])]
            puzzle_tsx = puzzle_tsx.replace(
                "/*[[INSERT IMPORTS]]*/", "\n".join(imports) or ""
            )

            if html:
                puzzle_tsx = puzzle_tsx.replace("[[INSERT CONTENT]]", html)
            if slug:
                puzzle_tsx = puzzle_tsx.replace("[[INSERT SLUG]]", slug)
            if title:
                puzzle_tsx = puzzle_tsx.replace("[[INSERT TITLE]]", title)
            if answer:
                puzzle_tsx = puzzle_tsx.replace("[[INSERT ANSWER]]", answer)
            if authors:
                puzzle_tsx = puzzle_tsx.replace("[[INSERT AUTHORS]]", authors)

            # Fix some HTML -> React
            puzzle_tsx = re.sub(
                r'(col|row)Span="(\d+)"', r"\g<1>Span={\g<2>}", puzzle_tsx
            )

            return puzzle_tsx
    except Exception as e:
        msg = f"Failed to open {template_file}"
        raise CommandError(msg) from e
