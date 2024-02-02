# Changelog

Every year, a new team inherits this code and adapts it to their needs for writing Mystery Hunt. Here's an attempt to capture what we changed from the previous year.

## 2024-2025 (Death and Mayhem)

Relative to other writing teams, we expect to have more contributions from people who are less plugged into the overall writing process. Most of our changes focused on making PuzzUp safer for that category of people, mostly by introducing more permissions and limiting access to users without an additional role:

* Hide puzzle titles in favor of codenames anywhere they're displayed to non-spoiled users
* Restrict pages with lists of all puzzles to users with the `puzzle_editing.list_puzzle` permission.
* Remove flows that allowed a user (other than the EIC) to unspoil themselves, either from a puzzle or from a testsolve session
* Restrict changing the set of "privileged" roles on a puzzle (editor, postprodder, factchecker) to users in those groups
* Add a `SiteSetting` that disables testsolving (primarily to ease initial onboarding)

We also made some quality of life improvements:

* Allow logging in with Discord via OAuth instead of username and password (stolen from TTBNL's 2023-2024 fork). Additionally, if no `SITE_PASSWORD` is set, **require** logging in with Discord.
* Introduced stronger linting and typechecking both in pre-commit hooks and GitHub Actions.
* Since we hosted on AWS instead of Heroku, added some configuration for pulling config and secrets out of AWS Systems Manager Parameter Store.
