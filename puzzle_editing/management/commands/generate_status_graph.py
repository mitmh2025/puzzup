from django.core.management.base import BaseCommand

from puzzle_editing import status

BLOCKER_COLORS = {
    status.EIC: "lemonchiffon",
    status.AUTHORS_AND_EDITORS: "palegreen",
    status.TESTSOLVERS: "lightpink",
    status.POSTPRODDERS: "orchid",
    status.FACTCHECKERS: "lightskyblue",
    status.NOBODY: "grey",
}


class Command(BaseCommand):
    def handle(self, *args, **options):
        nodes = []
        for s in status.ALL_STATUSES:
            blocker = status.BLOCKERS_AND_TRANSITIONS.get(s["value"], [status.NOBODY])[
                0
            ]
            nodes.append(
                '  {} [label="{} {}",style=filled,color={}];'.format(
                    s["value"],
                    s["emoji"],
                    s["display"],
                    BLOCKER_COLORS.get(blocker, BLOCKER_COLORS[status.NOBODY]),
                )
            )

        edges = []
        for s, (_, transitions) in status.BLOCKERS_AND_TRANSITIONS.items():
            for t, label in transitions:
                edges.append(f'  {s} -> {t} [label="{label}"];')

        print("digraph {")
        print("\n".join(nodes))
        print("\n".join(edges))
        print("}")
