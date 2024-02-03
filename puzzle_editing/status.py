# Just a fake enum and namespace to keep status-related things in. If we use a
# real Enum, Django weirdly doesn't want to display the human-readable version.

INITIAL_IDEA = "II"
IN_DEVELOPMENT = "ID"
AWAITING_ANSWER = "AA"
WRITING = "W"
WRITING_FLEXIBLE = "WF"
TESTSOLVING = "T"
NEEDS_SOLUTION = "NS"
AWAITING_ANSWER_FLEXIBLE = "AF"
NEEDS_POSTPROD = "NP"
AWAITING_POSTPROD_APPROVAL = "AP"
NEEDS_FACTCHECK = "NF"
NEEDS_FINAL_DAY_FACTCHECK = "NK"
NEEDS_FINAL_REVISIONS = "NR"
DONE = "D"
DEFERRED = "DF"
DEAD = "X"

# for ordering
# unclear if this was a good idea, but it does mean we can insert and reorder
# statuses without a database migration (?)
STATUSES = [
    INITIAL_IDEA,
    IN_DEVELOPMENT,
    AWAITING_ANSWER,
    WRITING,
    WRITING_FLEXIBLE,
    TESTSOLVING,
    NEEDS_SOLUTION,
    AWAITING_ANSWER_FLEXIBLE,
    NEEDS_POSTPROD,
    AWAITING_POSTPROD_APPROVAL,
    NEEDS_FACTCHECK,
    NEEDS_FINAL_REVISIONS,
    NEEDS_FINAL_DAY_FACTCHECK,
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
    return get_status_rank(status) > get_status_rank(
        WRITING_FLEXIBLE
    ) and get_status_rank(status) <= get_status_rank(DONE)


def past_testsolving(status):
    return get_status_rank(status) > get_status_rank(
        NEEDS_SOLUTION
    ) and get_status_rank(status) <= get_status_rank(DONE)


# Possible blockers:

EIC = "editor-in-chief"
AUTHORS_AND_EDITORS = "the author(s) and editors"
TESTSOLVERS = "testsolve coordinators"
POSTPRODDERS = "postprodders"
FACTCHECKERS = "factcheckers"
NOBODY = "nobody"

BLOCKERS = [
    EIC,
    AUTHORS_AND_EDITORS,
    TESTSOLVERS,
    POSTPRODDERS,
    FACTCHECKERS,
    NOBODY,
]

BLOCKERS_AND_TRANSITIONS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    INITIAL_IDEA: (
        AUTHORS_AND_EDITORS,
        [
            (IN_DEVELOPMENT, "✅ Editors assigned"),
            (DEFERRED, "⏸️  Mark deferred"),
            (DEAD, "⏹️  Mark as dead"),
        ],
    ),
    IN_DEVELOPMENT: (
        AUTHORS_AND_EDITORS,
        [
            (AWAITING_ANSWER, "✅ Idea approved 🤷🏽‍♀️ need answer"),
            (WRITING_FLEXIBLE, "✅ Idea approved 👍 Answer flexible"),
        ],
    ),
    AWAITING_ANSWER: (
        EIC,
        [
            (WRITING, "✅ Answer assigned"),
        ],
    ),
    WRITING: (
        AUTHORS_AND_EDITORS,
        [
            (AWAITING_ANSWER, "❌ Reject answer"),
            (TESTSOLVING, "✅ Puzzle is ready to be testsolved"),
        ],
    ),
    WRITING_FLEXIBLE: (
        AUTHORS_AND_EDITORS,
        [
            (TESTSOLVING, "✅ Puzzle is ready to be testsolved"),
        ],
    ),
    TESTSOLVING: (
        TESTSOLVERS,
        [
            (WRITING, "❌ Testsolve done; needs revision"),
            (WRITING_FLEXIBLE, "❌ Testsolve done; needs revision (flexible answer)"),
            (NEEDS_SOLUTION, "✅ Accept testsolve; request solution walkthru"),
            (NEEDS_POSTPROD, "⏩ Accept testsolve and solution; request postprod"),
            (
                AWAITING_ANSWER_FLEXIBLE,
                "⏩ Accept testsolve and solution 🤷🏽‍♀️ need round and answer",
            ),
        ],
    ),
    NEEDS_SOLUTION: (
        AUTHORS_AND_EDITORS,
        [
            (NEEDS_POSTPROD, "✅ Solution finshed 🪵 request postprod"),
            (
                AWAITING_ANSWER_FLEXIBLE,
                "✅ Solution finshed 🤷🏽‍♀️ need round and answer",
            ),
        ],
    ),
    AWAITING_ANSWER_FLEXIBLE: (
        EIC,
        [
            (NEEDS_POSTPROD, "✅ Round and answer assigned 🪵 request postprod"),
        ],
    ),
    NEEDS_POSTPROD: (
        POSTPRODDERS,
        [
            (AWAITING_POSTPROD_APPROVAL, "📝 Request approval after postprod"),
        ],
    ),
    AWAITING_POSTPROD_APPROVAL: (
        AUTHORS_AND_EDITORS,
        [
            (NEEDS_POSTPROD, "❌ Request revisions to postprod"),
            (NEEDS_FACTCHECK, "⏩ Mark postprod as finished 📝 request factcheck"),
        ],
    ),
    NEEDS_FACTCHECK: (
        FACTCHECKERS,
        [
            (NEEDS_FINAL_REVISIONS, "🟡 Needs revisions"),
            (NEEDS_FINAL_DAY_FACTCHECK, "📆 Needs final day factcheck"),
            (DONE, "⏩🎆 Mark as done! 🎆⏩"),
        ],
    ),
    NEEDS_FINAL_REVISIONS: (
        AUTHORS_AND_EDITORS,
        [
            (NEEDS_FACTCHECK, "📝 Review revisions"),
        ],
    ),
    NEEDS_FINAL_DAY_FACTCHECK: (
        FACTCHECKERS,
        [
            (DONE, "⏩🎆 Mark as done! 🎆⏩"),
        ],
    ),
}


def get_blocker(status):
    value = BLOCKERS_AND_TRANSITIONS.get(status)
    if value:
        return value[0]
    else:
        return NOBODY


def get_transitions(status):
    _, transitions = BLOCKERS_AND_TRANSITIONS.get(status)
    return transitions or []


STATUSES_BY_BLOCKERS = {
    blocker: [
        status for status, (b, _) in BLOCKERS_AND_TRANSITIONS.items() if b == blocker
    ]
    for blocker in BLOCKERS
}


DESCRIPTIONS = {
    INITIAL_IDEA: "Initial Idea",
    IN_DEVELOPMENT: "In Development",
    AWAITING_ANSWER: "Waiting for Answer",
    WRITING: "Writing / Revising (Answer Assigned)",
    WRITING_FLEXIBLE: "Writing / Revising (Answer Flexible)",
    TESTSOLVING: "In Testsolving",
    NEEDS_SOLUTION: "Finalizing Solution",
    AWAITING_ANSWER_FLEXIBLE: "Puzzle Written, Waiting for Round",
    NEEDS_POSTPROD: "Ready for Postprodding",
    AWAITING_POSTPROD_APPROVAL: "Awaiting Approval After Postprod",
    NEEDS_FACTCHECK: "Factchecking",
    NEEDS_FINAL_REVISIONS: "Final Revisions",
    NEEDS_FINAL_DAY_FACTCHECK: "Needs Final Day Factcheck",
    DONE: "Done",
    DEFERRED: "Deferred",
    DEAD: "Dead",
}


EMOJIS = {
    INITIAL_IDEA: "🥚",
    AWAITING_ANSWER: "🤷🏽‍♀️",
    IN_DEVELOPMENT: "👒",
    WRITING: "✏️",
    WRITING_FLEXIBLE: "✏️",
    TESTSOLVING: "💡",
    AWAITING_ANSWER_FLEXIBLE: "⏳",
    NEEDS_POSTPROD: "🪵",
    AWAITING_POSTPROD_APPROVAL: "🧐",
    NEEDS_FINAL_DAY_FACTCHECK: "📆",
    NEEDS_FACTCHECK: "📋",
    NEEDS_FINAL_REVISIONS: "🔬",
    DONE: "🏁",
    DEFERRED: "💤",
    DEAD: "💀",
}

MAX_LENGTH = 2


def get_display(status):
    return DESCRIPTIONS.get(status, status)


def get_emoji(status):
    return EMOJIS.get(status, "")


ALL_STATUSES = [
    {
        "value": status,
        "display": description,
        "emoji": get_emoji(status),
    }
    for status, description in DESCRIPTIONS.items()
]


def get_message_for_status(status, puzzle):
    additional_msg = ""
    if status == AWAITING_POSTPROD_APPROVAL:
        postprod_url = puzzle.postprod_url
        if postprod_url:
            additional_msg = f"\nView the postprod at {postprod_url}"

    return f"This puzzle is now **{get_display(status)}**." + additional_msg
