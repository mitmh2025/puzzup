# Changelog

Every year, a new team inherits this code and adapts it to their needs for writing Mystery Hunt. Here's an attempt to capture major changes from the previous year.

## 2024-2025 (Death and Mayhem)

Relative to other writing teams, we expect to have more contributions from people who are less plugged into the overall writing process. Most of our changes focused on making PuzzUp safer for that category of people, mostly by introducing more permissions and limiting access to users without an additional role:

* Hide puzzle titles in favor of codenames anywhere they're displayed to non-spoiled users
* Restrict pages with lists of all puzzles to users with the `puzzle_editing.list_puzzle` permission.
* Remove flows that allowed a user (other than the EIC) to unspoil themselves, either from a puzzle or from a testsolve session
* Restrict changing the set of "privileged" roles on a puzzle (editor, postprodder, factchecker) to users in those groups
* Add a `SiteSetting` that disables testsolving (primarily to ease initial onboarding)
* Created a concept of "closed testsolving" for puzzles which require special coordination. These puzzles show up in a special list for the testsolve coordinator, but are not listed publicly as needing testsolving.

We doubled-down on PuzzUp's integration with Discord in a handful of ways:

* Allow logging in with Discord via OAuth instead of username and password (stolen from TTBNL's 2023-2024 fork). Additionally, if no `SITE_PASSWORD` is set, **require** logging in with Discord.
* Avoid exhausting Discord's per-guild channel limit. Instead of creating a new channel for every puzzle, only create one for puzzles that have more than one author and/or editor. Additionally, garbage-collect channels for dead puzzles.
* Post messages whenever a puzzle makes a significant transition through the pipeline. We had these post to a "#hype" channel.

We also made some quality of life improvements and other adjustments:

* Introduced stronger linting and typechecking both in pre-commit hooks and GitHub Actions.
* Since we hosted on AWS instead of Heroku, added some configuration for pulling config and secrets out of AWS Systems Manager Parameter Store.
* Significantly cut back on the number of statuses that puzzles can be in - we found the original status graph to be overwhelming, and so pared it back to match our flow.
* Instead of tracking puzzle and solution content as a Markdown field within the puzzle record, instead automatically create canonical Google Docs for both. This gives us change tracking for free and matches how most puzzles are written. (Additionally, for testsolving purposes, we create a read-only copy of the puzzle document so that we capture the version of the puzzle that was tested against.)
* Modified the EIC role so that holders are treated as auto-spoiled on every puzzle, rather than needing to manually spoil themselves on each puzzle they look at.
* Revived a feature from PuzzleTron that allowed uploading a ZIP file containing an HTML version of a puzzle. The ZIP file must have an index.html, and the contents are unpacked and uploaded into S3 where they can be accessed.
* Swapped out the adjective and noun lists for generating puzzle codenames to a larger set to reduce the likelihood of collisions (and also tried to make the adjective and noun independently unique, so you don't end up with a bunch of "colorless-" puzzles).

And finally there are a handful of changes that you may find yourself wanting to back out:

* The statistics page has some hard-coded logic for the structure of our hunt.
* We took an axe to PuzzUp's auto-postprodding feature, because we prefer to do postprodding by hand rather than automatically attempt to convert from a Google Doc. A lot of that code is still present but in a heavily neutered state.
