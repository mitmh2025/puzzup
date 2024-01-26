# Just a fake enum and namespace to keep status-related things in. If we use a
# real Enum, Django weirdly doesn't want to display the human-readable version.

INITIAL_IDEA = "II"
AWAITING_APPROVAL = "AE"  # Authors submitted to EICs
NEEDS_DISCUSSION = "ND"  # EICs have seen but not yet decided
IDEA_IN_DEVELOPMENT = "ID"
AWAITING_ANSWER = "AA"
WRITING = "W"
AWAITING_APPROVAL_FOR_TESTSOLVING = "AT"
NEEDS_TESTSOLVE_FACTCHECK = "PF"
TESTSOLVE_FACTCHECK_REVISION = "FR"
TESTSOLVING = "T"
ACTIVELY_TESTSOLVING = "TT"
AWAITING_TESTSOLVE_REVIEW = "TR"
REVISING = "R"
AWAITING_APPROVAL_POST_TESTSOLVING = "AO"
NEEDS_HINTS = "NH"
AWAITING_HINTS_APPROVAL = "AH"
NEEDS_POSTPROD = "NP"
ACTIVELY_POSTPRODDING = "PP"
POSTPROD_BLOCKED = "PB"
POSTPROD_BLOCKED_ON_TECH = "BT"
AWAITING_POSTPROD_APPROVAL = "AP"
NEEDS_FACTCHECK = "NF"
NEEDS_FINAL_REVISIONS = "NR"
NEEDS_COPY_EDITS = "NC"
DONE = "D"
DEFERRED = "DF"
DEAD = "X"

# for ordering
# unclear if this was a good idea, but it does mean we can insert and reorder
# statuses without a database migration (?)
STATUSES = [
    INITIAL_IDEA,
    AWAITING_APPROVAL,
    NEEDS_DISCUSSION,
    IDEA_IN_DEVELOPMENT,
    AWAITING_ANSWER,
    WRITING,
    AWAITING_APPROVAL_FOR_TESTSOLVING,
    NEEDS_TESTSOLVE_FACTCHECK,
    TESTSOLVE_FACTCHECK_REVISION,
    TESTSOLVING,
    ACTIVELY_TESTSOLVING,
    AWAITING_TESTSOLVE_REVIEW,
    REVISING,
    AWAITING_APPROVAL_POST_TESTSOLVING,
    NEEDS_HINTS,
    AWAITING_HINTS_APPROVAL,
    NEEDS_POSTPROD,
    POSTPROD_BLOCKED,
    ACTIVELY_POSTPRODDING,
    POSTPROD_BLOCKED_ON_TECH,
    AWAITING_POSTPROD_APPROVAL,
    NEEDS_FACTCHECK,
    NEEDS_FINAL_REVISIONS,
    NEEDS_COPY_EDITS,
    DONE,
    DEFERRED,
    DEAD,
]


def get_status_rank(status):
    try:
        return STATUSES.index(status)
    except ValueError:  # not worth crashing imo
        return -1


def past_writing(status):
    return get_status_rank(status) > get_status_rank(WRITING) and get_status_rank(
        status
    ) <= get_status_rank(DONE)


def past_testsolving(status):
    return get_status_rank(status) > get_status_rank(REVISING) and get_status_rank(
        status
    ) <= get_status_rank(DONE)


# Possible blockers:

EIC = "editor(s)-in-chief"
EDITORS = "editor(s)"
AUTHORS = "the author(s)"
TESTSOLVE_ADMINS = "testsolve admins"
POSTPRODDERS = "postprodders"
FACTCHECKERS = "factcheckers"
NOBODY = "nobody"

BLOCKERS_AND_TRANSITIONS: dict[str, dict[str, list[tuple[str, str]]]] = {
    INITIAL_IDEA: {
        AUTHORS: [
            (AWAITING_APPROVAL, "💬 Request approval by EICs"),
            (DEFERRED, "⏸️  Mark deferred"),
            (DEAD, "⏹️  Mark as dead"),
        ],
    },
    AWAITING_APPROVAL: {
        EIC: [
            (IDEA_IN_DEVELOPMENT, "❌ Request revision"),
            (NEEDS_DISCUSSION, "👥 EICs need to discuss as a group"),
            (AWAITING_ANSWER, "✅ Idea approved, 🤷🏽‍♀️ No answer yet"),
            (DEFERRED, "⏸️  Mark deferred"),
        ],
    },
    NEEDS_DISCUSSION: {
        EIC: [
            (IDEA_IN_DEVELOPMENT, "❌ Request revision"),
            (AWAITING_ANSWER, "✅ Idea approved, 🤷🏽‍♀️ No answer yet"),
            (DEFERRED, "⏸️  Mark deferred"),
        ],
    },
    IDEA_IN_DEVELOPMENT: {
        AUTHORS: [
            (AWAITING_APPROVAL, "💬 Changes made, request approval by EICs"),
        ],
    },
    AWAITING_ANSWER: {
        EIC: [
            (WRITING, "✅ Mark as answer assigned"),
        ],
    },
    WRITING: {
        AUTHORS: [
            (AWAITING_ANSWER, "❌ Reject answer"),
            (AWAITING_APPROVAL_FOR_TESTSOLVING, "📝 Request approval for testsolving"),
        ],
    },
    AWAITING_APPROVAL_FOR_TESTSOLVING: {
        EDITORS: [
            (NEEDS_TESTSOLVE_FACTCHECK, "🔎 Request pre-testsolve factcheck"),
        ],
    },
    NEEDS_TESTSOLVE_FACTCHECK: {
        FACTCHECKERS: [
            (TESTSOLVING, "✅ Puzzle is ready to be testsolved"),
            (TESTSOLVE_FACTCHECK_REVISION, "❌ Request puzzle revision"),
        ],
    },
    TESTSOLVE_FACTCHECK_REVISION: {
        AUTHORS: [
            (NEEDS_TESTSOLVE_FACTCHECK, "🔎 Request pre-testsolve factcheck"),
        ],
    },
    TESTSOLVING: {
        TESTSOLVE_ADMINS: [
            (ACTIVELY_TESTSOLVING, "🎢 Testsolve started"),
        ],
    },
    ACTIVELY_TESTSOLVING: {
        TESTSOLVE_ADMINS: [
            (AWAITING_TESTSOLVE_REVIEW, "🧐 Testsolve done; author to review feedback"),
        ],
    },
    AWAITING_TESTSOLVE_REVIEW: {
        AUTHORS: [
            (REVISING, "❌ Needs revision"),
        ],
        TESTSOLVE_ADMINS: [
            (TESTSOLVING, "🔄 Ready for more testsolving"),
            (REVISING, "❌ Needs revision"),
            (
                AWAITING_APPROVAL_POST_TESTSOLVING,
                "📝 Send to EICs for approval to leave testsolving",
            ),
        ],
    },
    REVISING: {
        AUTHORS: [
            (
                AWAITING_APPROVAL_FOR_TESTSOLVING,
                "📝 Request approval for testsolving (significant changes)",
            ),
            (
                NEEDS_TESTSOLVE_FACTCHECK,
                "🔎 Request pre-testsolve factcheck (minor changes)",
            ),
            (
                AWAITING_APPROVAL_POST_TESTSOLVING,
                "⏭️ Request EIC approval to skip testsolving",
            ),
        ],
    },
    AWAITING_APPROVAL_POST_TESTSOLVING: {
        EIC: [
            (REVISING, "❌ Request puzzle revision"),
            (TESTSOLVING, "🔙 Return to testsolving"),
            (NEEDS_HINTS, "⏩ Accept puzzle and solution; send to hints"),
        ],
    },
    NEEDS_HINTS: {
        AUTHORS: [
            (AWAITING_HINTS_APPROVAL, "📝 Request approval for hints"),
        ],
    },
    AWAITING_HINTS_APPROVAL: {
        EIC: [
            (NEEDS_HINTS, "❌ Request revisions to hints"),
            (POSTPROD_BLOCKED, "✏️ Finalize hints, request postprod preparation"),
        ],
    },
    NEEDS_POSTPROD: {
        POSTPRODDERS: [
            (ACTIVELY_POSTPRODDING, "🏠 Postprodding has started"),
            (AWAITING_POSTPROD_APPROVAL, "📝 Request approval after postprod"),
            (POSTPROD_BLOCKED, "❌✏️ Request revisions from author/art"),
            (POSTPROD_BLOCKED_ON_TECH, "❌💻 Blocked on tech request"),
        ],
    },
    ACTIVELY_POSTPRODDING: {
        POSTPRODDERS: [
            (AWAITING_POSTPROD_APPROVAL, "📝 Request approval after postprod"),
            (POSTPROD_BLOCKED, "❌✏️ Request revisions from author/art"),
            (POSTPROD_BLOCKED_ON_TECH, "❌💻 Blocked on tech request"),
        ],
    },
    POSTPROD_BLOCKED: {
        AUTHORS: [
            (NEEDS_POSTPROD, "📝 Mark as Ready for Postprod"),
            (ACTIVELY_POSTPRODDING, "🏠 Postprodding can resume"),
            (POSTPROD_BLOCKED_ON_TECH, "❌💻 Blocked on tech request"),
        ],
    },
    POSTPROD_BLOCKED_ON_TECH: {
        POSTPRODDERS: [
            (ACTIVELY_POSTPRODDING, "🏠 Postprodding can resume"),
            (NEEDS_POSTPROD, "📝 Mark as Ready for Postprod"),
            (POSTPROD_BLOCKED, "❌✏️ Request revisions from author/art"),
            (AWAITING_POSTPROD_APPROVAL, "📝 Request approval after postprod"),
        ],
    },
    AWAITING_POSTPROD_APPROVAL: {
        EIC: [
            (ACTIVELY_POSTPRODDING, "❌ Request revisions to postprod"),
            (NEEDS_FACTCHECK, "⏩ Mark postprod as finished; request factcheck"),
        ],
    },
    NEEDS_FACTCHECK: {
        FACTCHECKERS: [
            (REVISING, "❌ Request large revisions (needs more testsolving)"),
            (
                NEEDS_FINAL_REVISIONS,
                "🟡 Needs revisions (doesn't need testsolving)",
            ),
            (DONE, "✅🎆 Mark as done! 🎆✅"),
        ],
    },
    NEEDS_FINAL_REVISIONS: {
        AUTHORS: [
            (NEEDS_FACTCHECK, "📝 Request factcheck (for large revisions)"),
            (NEEDS_COPY_EDITS, "✅ Request copy edits (for small revisions)"),
        ],
    },
    NEEDS_COPY_EDITS: {
        FACTCHECKERS: [
            (NEEDS_FINAL_REVISIONS, "🟡 Needs revisions"),
            (DONE, "✅🎆 Mark as done! 🎆✅"),
        ],
    },
    DEFERRED: {
        NOBODY: [
            (INITIAL_IDEA, "✅ Back in development"),
        ],
    },
    DEAD: {
        NOBODY: [
            (INITIAL_IDEA, "✅ Back in development"),
        ],
    },
}


def get_blocker(status) -> list[str]:
    value = BLOCKERS_AND_TRANSITIONS.get(status)
    if value:
        return list(value.keys())
    else:
        return [NOBODY]


def get_transitions(status, user, puzzle):
    value = BLOCKERS_AND_TRANSITIONS.get(status)
    if value:
        ret = {}
        for blocker, transitions in value.items():
            if (
                (blocker == NOBODY or user.is_eic)
                or (blocker == EIC and user.is_eic)
                or (blocker == TESTSOLVE_ADMINS and user.is_testsolve_coordinator)
                or (blocker == EDITORS and puzzle.editors.filter(id=user.id).exists())
                or (blocker == AUTHORS and puzzle.authors.filter(id=user.id).exists())
                or (
                    blocker == POSTPRODDERS
                    and puzzle.postprodders.filter(id=user.id).exists()
                )
                or (
                    blocker == FACTCHECKERS
                    and (
                        puzzle.factcheckers.filter(id=user.id).exists()
                        or puzzle.quickcheckers.filter(id=user.id).exists()
                    )
                )
            ):
                for target, descr in transitions:
                    ret.setdefault(target, descr)

        exclusions = []

        if (
            puzzle
            and user
            and status != INITIAL_IDEA
            and puzzle.authors.filter(id=user.id).exists()
        ):
            ret.setdefault(DEFERRED, "⏸️  Mark deferred")
            ret.setdefault(DEAD, "⏹️  Mark as dead")

        return [(k, v) for k, v in ret.items() if k not in exclusions]
    else:
        return []


STATUSES_BLOCKED_ON_EDITORS = [
    status
    for status, blockers in BLOCKERS_AND_TRANSITIONS.items()
    if EDITORS in blockers
]
STATUSES_BLOCKED_ON_AUTHORS = [
    status
    for status, blockers in BLOCKERS_AND_TRANSITIONS.items()
    if AUTHORS in blockers
]

# Embedded in Discord categories -- do not change strings!
DESCRIPTIONS = {
    INITIAL_IDEA: "Initial Idea",
    AWAITING_APPROVAL: "Awaiting Approval By EIC",
    NEEDS_DISCUSSION: "EICs are Discussing",
    IDEA_IN_DEVELOPMENT: "Idea in Development",
    AWAITING_ANSWER: "Awaiting Answer",
    WRITING: "Writing (Answer Assigned)",
    AWAITING_APPROVAL_FOR_TESTSOLVING: "Awaiting Approval for Testsolving",
    NEEDS_TESTSOLVE_FACTCHECK: "Needs Pre-Testsolve Factcheck",
    TESTSOLVE_FACTCHECK_REVISION: "Factcheck Revisions",
    TESTSOLVING: "Ready to be Testsolved",
    ACTIVELY_TESTSOLVING: "Actively Testsolving",
    AWAITING_TESTSOLVE_REVIEW: "Awaiting Testsolve Review",
    REVISING: "Revising (Needs Testsolving)",
    AWAITING_APPROVAL_POST_TESTSOLVING: "Awaiting Approval (Done with Testsolving)",
    NEEDS_HINTS: "Needs Hints",
    AWAITING_HINTS_APPROVAL: "Awaiting Hints Approval",
    POSTPROD_BLOCKED: "Postproduction Blocked",
    POSTPROD_BLOCKED_ON_TECH: "Postproduction Blocked On Tech Request",
    NEEDS_POSTPROD: "Ready for Postprodding",
    ACTIVELY_POSTPRODDING: "Actively Postprodding",
    AWAITING_POSTPROD_APPROVAL: "Awaiting Approval After Postprod",
    NEEDS_FACTCHECK: "Needs Postprod Factcheck",
    NEEDS_COPY_EDITS: "Needs Copy Edits",
    NEEDS_FINAL_REVISIONS: "Needs Final Revisions",
    DONE: "Done",
    DEFERRED: "Deferred",
    DEAD: "Dead",
}


EMOJIS = {
    INITIAL_IDEA: "🥚",
    AWAITING_APPROVAL: "⏳🎩",
    NEEDS_DISCUSSION: "🗣",
    IDEA_IN_DEVELOPMENT: "🐣",
    AWAITING_ANSWER: "⏳🤷🏽‍♀️",
    WRITING: "✏️",
    AWAITING_APPROVAL_FOR_TESTSOLVING: "⏳➡️💡",
    NEEDS_TESTSOLVE_FACTCHECK: "🔎",
    TESTSOLVE_FACTCHECK_REVISION: "✏️🔄",
    TESTSOLVING: "💡",
    ACTIVELY_TESTSOLVING: "🎢",
    AWAITING_TESTSOLVE_REVIEW: "⏳💡",
    REVISING: "✏️🔄",
    AWAITING_APPROVAL_POST_TESTSOLVING: "⏳💡➡️",
    NEEDS_HINTS: "⁉",
    AWAITING_HINTS_APPROVAL: "⏳⁉✅",
    POSTPROD_BLOCKED: "⚠️✏️",
    POSTPROD_BLOCKED_ON_TECH: "⚠️💻",
    NEEDS_POSTPROD: "🪵",
    ACTIVELY_POSTPRODDING: "🏠",
    AWAITING_POSTPROD_APPROVAL: "⏳🏠✅",
    NEEDS_FACTCHECK: "📋",
    NEEDS_FINAL_REVISIONS: "🔬",
    NEEDS_COPY_EDITS: "📃",
    DONE: "🏁",
    DEFERRED: "💤",
    DEAD: "💀",
}

TEMPLATES: dict[str, str] = {}

MAX_LENGTH = 2


def get_display(status):
    return DESCRIPTIONS.get(status, status)


def get_emoji(status):
    return EMOJIS.get(status, "")


def get_template(status):
    return TEMPLATES.get(status, "status_update_email")


ALL_STATUSES = [
    {
        "value": status,
        "display": description,
        "emoji": get_emoji(status),
    }
    for status, description in DESCRIPTIONS.items()
]
