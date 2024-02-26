import asyncio
import re
import urllib.parse
from typing import TYPE_CHECKING, Self

from aiogoogle import Aiogoogle  # type: ignore
from aiogoogle.auth.creds import ServiceAccountCreds  # type: ignore
from bs4 import BeautifulSoup
from django.conf import settings

if TYPE_CHECKING:
    from puzzle_editing import models as m

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


TYPE_FOLDER = "application/vnd.google-apps.folder"
TYPE_DOC = "application/vnd.google-apps.document"
TYPE_SHEET = "application/vnd.google-apps.spreadsheet"


class GoogleManager:
    __instance: Self | None = None

    @classmethod
    def instance(cls):
        """
        Get a single instance per process.
        """
        if (
            cls.__instance is None
            and "credentials" in settings.DRIVE_SETTINGS
            and settings.PUZZLE_DRAFT_FOLDER_ID
            and settings.PUZZLE_SOLUTION_FOLDER_ID
            and settings.PUZZLE_RESOURCES_FOLDER_ID
            and settings.TESTSOLVING_FOLDER_ID
            and settings.FACTCHECKING_FOLDER_ID
        ):
            cls.__instance = cls(
                puzzle_draft_folder_id=settings.PUZZLE_DRAFT_FOLDER_ID,
                puzzle_solution_folder_id=settings.PUZZLE_SOLUTION_FOLDER_ID,
                puzzle_resources_folder_id=settings.PUZZLE_RESOURCES_FOLDER_ID,
                testsolving_folder_id=settings.TESTSOLVING_FOLDER_ID,
                factchecking_folder_id=settings.FACTCHECKING_FOLDER_ID,
            )
        return cls.__instance

    def __init__(
        self,
        puzzle_draft_folder_id: str,
        puzzle_solution_folder_id: str,
        puzzle_resources_folder_id: str,
        testsolving_folder_id: str,
        factchecking_folder_id: str,
    ) -> None:
        self.creds = ServiceAccountCreds(
            scopes=SCOPES,
            **settings.DRIVE_SETTINGS["credentials"],
        )
        self.puzzle_draft_folder_id = puzzle_draft_folder_id
        self.puzzle_solution_folder_id = puzzle_solution_folder_id
        self.puzzle_resources_folder_id = puzzle_resources_folder_id
        self.testsolving_folder_id = testsolving_folder_id
        self.factchecking_folder_id = factchecking_folder_id

        async def fetch_apis():
            async with Aiogoogle(service_account_creds=self.creds) as aiogoogle:
                drive = await aiogoogle.discover("drive", "v3")
                sheets = await aiogoogle.discover("sheets", "v4")
                return drive, sheets

        self.drive, self.sheets = asyncio.run(fetch_apis())

    def make_aiogoogle(self) -> Aiogoogle:
        return Aiogoogle(service_account_creds=self.creds)

    async def _move_to_folder(
        self, aiogoogle: Aiogoogle, file_id: str, folder_id: str
    ) -> None:
        # file_id is allowed to be a folder
        response = await aiogoogle.as_service_account(
            self.drive.files.get(
                fileId=file_id, fields="parents", supportsAllDrives=True
            )
        )
        existing_parents = ",".join(response["parents"])
        await aiogoogle.as_service_account(
            self.drive.files.update(
                fileId=file_id,
                addParents=folder_id,
                removeParents=existing_parents,
                supportsAllDrives=True,
            )
        )

    async def _make_file_public_view(self, aiogoogle: Aiogoogle, file_id: str) -> None:
        await aiogoogle.as_service_account(
            self.drive.permissions.create(
                fileId=file_id,
                json={"role": "reader", "type": "anyone"},
                supportsAllDrives=True,
            )
        )

    async def _make_file_public_edit(self, aiogoogle: Aiogoogle, file_id: str) -> None:
        await aiogoogle.as_service_account(
            self.drive.permissions.create(
                fileId=file_id,
                json={"role": "writer", "type": "anyone"},
                supportsAllDrives=True,
            )
        )

    async def _make_file_public_file_organizer(
        self, aiogoogle: Aiogoogle, file_id: str
    ) -> None:
        await aiogoogle.as_service_account(
            self.drive.permissions.create(
                fileId=file_id,
                json={"role": "fileOrganizer", "type": "anyone"},
                supportsAllDrives=True,
            )
        )

    async def _create_file(
        self, aiogoogle: Aiogoogle, name: str, parent: str, type: str
    ) -> str:
        response = await aiogoogle.as_service_account(
            self.drive.files.create(
                json={
                    "name": name,
                    "mimeType": type,
                    "parents": [parent],
                },
                supportsAllDrives=True,
                fields="id",
            )
        )
        return response["id"]

    async def create_puzzle_content_doc(
        self, aiogoogle: Aiogoogle, puzzle: "m.Puzzle"
    ) -> str:
        content_id = await self._create_file(
            aiogoogle,
            name=f"{puzzle.id:03d} ({puzzle.codename})",
            type=TYPE_DOC,
            parent=self.puzzle_draft_folder_id,
        )
        await self._make_file_public_edit(aiogoogle, content_id)
        return content_id

    async def create_puzzle_solution_doc(
        self, aiogoogle: Aiogoogle, puzzle: "m.Puzzle"
    ) -> str:
        # Create a solution document
        solution_id = await self._create_file(
            aiogoogle,
            name=f"{puzzle.id:03d} ({puzzle.codename}) Solution",
            type=TYPE_DOC,
            parent=self.puzzle_solution_folder_id,
        )
        await self._make_file_public_edit(aiogoogle, solution_id)
        return solution_id

    async def create_puzzle_resources_folder(
        self, aiogoogle: Aiogoogle, puzzle: "m.Puzzle"
    ) -> str:
        resources_id = await self._create_file(
            aiogoogle,
            name=f"{puzzle.id:03d} ({puzzle.codename}) Resources",
            type=TYPE_FOLDER,
            parent=self.puzzle_resources_folder_id,
        )
        await self._make_file_public_file_organizer(aiogoogle, resources_id)
        return resources_id

    async def create_testsolving_folder(
        self, aiogoogle: Aiogoogle, session: "m.TestsolveSession"
    ) -> tuple[str, str]:
        folder_id = await self._create_file(
            aiogoogle,
            name=f"Testsolve #{session.id} ({session.puzzle.codename})",
            type=TYPE_FOLDER,
            parent=self.testsolving_folder_id,
        )

        async def create_testsolve_sheet():
            sheet_id = await self._create_sheet(
                aiogoogle,
                title=f"{session.puzzle.name} (Testsolve #{session.id} Worksheet)",
                text="Puzzup Testsolve Session",
                url=f"{settings.PUZZUP_URL}/testsolve/{session.id}",
                folder_id=folder_id,
            )
            await self._make_file_public_edit(aiogoogle, sheet_id)
            return sheet_id

        async def create_testsolve_content():
            response = await aiogoogle.as_service_account(
                self.drive.files.copy(
                    fileId=session.puzzle.content_google_doc_id,
                    fields="id",
                    supportsAllDrives=True,
                    json={
                        "name": f"{session.puzzle.name} (Testsolve #{session.id} read-only copy)",
                        "parents": [folder_id],
                    },
                )
            )
            content_id = response["id"]
            await self._make_file_public_view(aiogoogle, content_id)
            return content_id

        return await asyncio.gather(
            create_testsolve_content(), create_testsolve_sheet()
        )

    async def create_factchecking_sheet(
        self, aiogoogle: Aiogoogle, puzzle: "m.Puzzle"
    ) -> str:
        # Look up the existing puzzle folder, if any
        file_name = puzzle.spoiler_free_title()
        response = await aiogoogle.as_service_account(
            self.drive.files.copy(
                fileId=settings.FACTCHECKING_TEMPLATE_ID,
                fields="id",
                supportsAllDrives=True,
                json={"name": f"{puzzle.id} {file_name} Factcheck"},
            )
        )
        template_id = response["id"]

        await self._move_to_folder(aiogoogle, template_id, self.factchecking_folder_id)
        return template_id

    async def _create_sheet(
        self, aiogoogle: Aiogoogle, title: str, text: str, url: str, folder_id: str
    ) -> str:
        """Creates spreadsheet where top-left cell is text that goes to given URL."""
        response = await aiogoogle.as_service_account(
            self.sheets.spreadsheets.create(
                json={
                    "properties": {
                        "title": title,
                    },
                    "sheets": [
                        {
                            "data": [
                                {
                                    "startRow": 0,
                                    "startColumn": 0,
                                    "rowData": [
                                        {
                                            "values": [
                                                {
                                                    "userEnteredValue": {
                                                        "formulaValue": f'=HYPERLINK("{url}", "{text}")'
                                                    },
                                                }
                                            ]
                                        }
                                    ],
                                }
                            ]
                        }
                    ],
                },
                fields="spreadsheetId",
            )
        )
        spreadsheet_id = response["spreadsheetId"]

        await self._move_to_folder(aiogoogle, spreadsheet_id, folder_id)
        return spreadsheet_id

    async def get_gdoc_html(self, aiogoogle, file_id):
        return await aiogoogle.as_service_account(
            self.drive.files.export(
                fileId=file_id,
                mimeType="text/html",
            )
        )

    def clean_html(self, html):
        """Cleans up some garbage exported from Google Docs using beautiful soup."""
        cleaner = HtmlCleaner(html)
        cleaner.clean()
        cleaner.map_to_react()
        return cleaner.to_string()


class HtmlCleaner:
    def __init__(self, html):
        self.soup = BeautifulSoup(html, "html.parser")

    def map_to_react(self):
        # Attributes mapped from html to React
        MAPPED_ATTRS = {"colspan": "colSpan", "rowspan": "rowSpan"}
        # Tag names mapped from html to React
        MAPPED_TAGS = {"table": "Table", "img": "SheetableImage"}

        for attr in MAPPED_ATTRS:
            for tag in self.soup.find_all(attrs={attr: True}):
                tag[MAPPED_ATTRS[attr]] = tag[attr]
                del tag[attr]

        # Swap some tags for the React component version.
        for tag in self.soup.find_all(MAPPED_TAGS.keys()):
            tag.name = MAPPED_TAGS[tag.name]

    def clean_google_urls(self, url: str):
        # For some reason Google is adding redirects to all urls, fix this with regex
        match = re.search(r"^https:\/\/www\.google\.com\/url\?q=(.*?)&", url)
        if match:
            return urllib.parse.unquote(match.group(1), encoding="utf-8")
        return url

    def clean_tables(self):
        # Wrap <table> in <thead> and <tbody>
        for tag in self.soup.find_all("table"):
            if not tag.contents:
                continue

            # Add thead if the first row contains a bold tag
            first_row = tag.contents[0]
            has_header = bool(first_row.find("b"))
            if has_header:
                thead = self.soup.new_tag("thead")
                thead.append(first_row.extract())
                # Remove bold in thead, since th is bold by default
                for b_tag in thead.find_all("b"):
                    b_tag.unwrap()
                # Swap td for th
                for td_tag in thead.find_all("td"):
                    td_tag.name = "th"
                tag.insert(0, thead)

            tbody = self.soup.new_tag("tbody")
            for content in reversed(tag.contents[1:] if has_header else tag.contents):
                tbody.insert(0, content.extract())
            tag.append(tbody)

        # Remove extra paragraphs inside td/th
        for tag in self.soup.find_all("p"):
            if tag.parent and tag.parent.name in ("td", "th"):
                tag.unwrap()

    def clean(self):
        # Tuple of tags that are allowed to be empty
        ALLOWED_EMPTY_TAGS = ("img",)

        # Nuke the whole head section, including stylesheet and meta tags
        self.soup.head.decompose()

        # Convert bold, italics, and underline styles
        def has_style(search_list):
            return lambda style: any(
                (search in (style or "")) for search in search_list
            )

        styles = {
            "i": ["font-style:italic"],
            "b": ["font-weight:700"],
            "u": ["text-decoration:underline"],
            "Monospace": [
                'font-family:"Consolas"',
                'font-family:"Roboto Mono"',
                'font-family:"Courier New"',
            ],
        }
        for tag_name, style_list in styles.items():
            for tag in self.soup.find_all(style=has_style(style_list)):
                if tag_name == "u" and tag.find("a", recursive=False):
                    # Google Docs wraps links with an extra underline span, ignore those
                    tag.unwrap()
                    continue
                tag.wrap(self.soup.new_tag(tag_name))

        # Remove almost all extraneous html attributes
        for attr in ("id", "class", "style", "start"):
            for tag in self.soup.find_all(attrs={attr: True}):
                del tag[attr]

        # Remove colspan/rowspan = 1
        for attr in ("colspan", "rowspan"):
            for tag in self.soup.find_all(attrs={attr: "1"}):
                del tag[attr]

        # Remove all of the spans, as well as the outer html and body
        for tag in self.soup.find_all(["span", "body", "html"]):
            tag.unwrap()

        # Clean up a tags href
        for tag in self.soup.find_all("a", href=True):
            tag["href"] = self.clean_google_urls(tag["href"])

        self.clean_tables()

        for tag in self.soup.find_all(lambda tag: not tag.contents):
            if tag.name == "p":
                # Clean up empty paragraphs
                tag.decompose()
            elif tag.name not in ALLOWED_EMPTY_TAGS:
                # Remove other empty tags
                tag.unwrap()

        # Delete any extra line breaks at the end of the doc.
        for tag in reversed(self.soup.contents):
            if tag.name == "br":
                tag.decompose()
            else:
                break

    def to_string(self):
        return str(self.soup)
