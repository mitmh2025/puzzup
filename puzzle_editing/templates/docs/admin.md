There are a few features of PuzzUp that are only accessible to admins/superusers.

## Making yourself a superuser.

```
python manage.py createsuperuser
```

Now you have access to Django's `/admin/` site. You can edit most aspects of the data freely. Wield your power wisely.

## Assigning editors

To make a user an **editor**, find them under "Users" and them to the "Editor" group. There's also an "EIC" role, which is basically a superuser on the main site (though not the admin site). In addition to everything an editor, postprodder, factchecker, and testsolve coordinator can do, they also have a short set of dashboards that are restricted to them.

Additionally, there are groups for "Postprodder", "Factchecker", and the various support organizations (art, accessibility, and tech).

(There is no UI to do this outside of /admin, but you have to do this so few times that it's not high priority.)

We generally try to avoid using group names for authorization checks, and instead use specific permission checks. Mostly, we use the auto-generated Django permissions, even though the actual action is not connected to what is allowed in the admin site:

* `puzzle_editing.change_round`: This is generally the editor permission, and allows viewing the list of rounds and answers and choosing to spoil yourself on both (ordinary users can spoil themselves on individual puzzles, but not rounds)
* `puzzle_editing.list_puzzle`: By default, we don't surface lists of all puzzles, but users with this permission can see that list.
* `puzzle_editing.change_testsolvesession`: Users with this permission can access testsolving, even if testsolving has been turned off for the site with a site setting.
* `puzzle_editing.unspoil_puzzle`: By default, once you've been spoiled on a puzzle, you're stuck that way, but a few privileged users can un-spoil someone.
* `puzzle_editing.change_puzzlepostprod` and `puzzle_editing.change_puzzlefactcheck`: These privileges correspond to postprodders and factcheckers. Some elements of those steps are visible to users without these permissions, but generally only users with these permissions can make forward progress on those steps.

## Defining statuses

This can be done in `status.py`.

## Status subscriptions

Each status subscription sends an email to a specific user whenever *any* puzzle enters a *specific* puzzle status.

(These are also completely invisible on the main website, which is maybe something we should fix.)

Some ways to use status subscriptions:

- Editors-in-chief who are in charge of assigning editors to puzzles when they need one can subscribe to the "Awaiting Editor" status. Then puzzle authors can set their puzzle to that status when they wanted an editor, and EICs will be notified.
- Similarly, the head factchecker and head copy editor can subscribe to the Needs Factcheck and Needs Copy Edits statuses
- For people who really wanted to testsolve puzzles, we gave them subscriptions to the Testsolving status so they would get an email whenever a puzzle entered testsolving.

## Site settings

Finally, there are a few "Site settings" that just look at the values associated with specific hardcoded keys in the codebase, so that you can change them without changing the code.

## Postprodding

PuzzUp allows you to export puzzle and hint metadata in the form of JSON or YAML files that can then be imported into your hunt repo.

We've also added the ability to automatically generate a React file based on a template. This hooks into the tph-template repo and automatically makes a commit in a new branch. To do so, you will need to set up the `HUNT_REPO` and `HUNT_REPO_URL` env variables to point to your local and remote Git repos, respectively.
