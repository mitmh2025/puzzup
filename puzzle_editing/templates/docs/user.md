This page tries to give a broad overview of PuzzUp and all the features accessible by every user (which is most of them).

There are a few permission-gated features and pages for editors and superusers covered separately, but editors and admins should read this guide first too.

## Overview

PuzzUp helps guide puzzles through the full puzzle production process:

- writing
- revising
- testsolving
- post-production
- copy-editing
- etc…

The main way this is tracked is the **puzzle status**. Each puzzle has a status. PuzzUp has a long list of the available statuses, and it knows to recommend particular transitions between specific statuses, though EICs can always change to any status at any time.

However, note that most puzzle statuses don't have much inherent meaning to PuzzUp. PuzzUp doesn't place any hard restrictions on which status changes can actually happen or on who can change statuses. So usually, if you find that a status doesn't make sense to you or doesn't fit in your workflow, you can skip it or repurpose it as desired.

The most visible feature that all puzzle statuses have is that each status is _blocked_ on a group. The majority of statuses are either blocked on the authors or on the editors of the puzzle; a few statuses are blocked on testsolvers, postprodders, factcheckers, and "nobody" The hope is that, for each puzzle, the status will always unambiguously specify whose responsibility it is to take the next steps, to try to avoid situations where people are waiting on each other.

## Specific Statuses {: #specificstatuses}

### Testsolving, Needs Postprod, Needs Factcheck, Needs Copy Edits

In particular, the "Testsolving" status allows testsolving admin to create testsolving sessions. This is covered in more detail later. Testsolving is unusual because it's the only PuzzUp activity you do on a puzzle where you must be unspoiled and must avoid the main puzzle page.

### Dead or Deferred

Puzzles that are "Dead" or "Deferred" are hidden from most puzzle lists by default, and they're also either omitted or treated separately in most statistics. You can still view them in lists by unchecking "Hide dead puzzles" and "Hide deferred puzzles".

### Initial Idea

Puzzles in "Initial Idea" are also sometimes omitted or separated out, since people often submit hundreds more half-baked puzzle ideas than they can work on or that can go into the hunt, and many of those ideas stay in "Initial Idea" forever.

## Authoring a Puzzle {: #authoringapuzzle}

To submit a puzzle idea, click "Add puzzle" and follow instructions. "Notes" are optional and are only shown to _spoiled_ teammates; use them as you wish.

PuzzUp does not dictate the exact workflow after that, but you and other people can:

- leave comments on the puzzle
- edit the puzzle
- edit the solution
- change the puzzle status.

The next step is to change the status to "Awaiting Approval from EICs", at which point somebody else will assign an editor or editors to your puzzle.

EICs will then decide whether this idea is ready to write, or that it needs additional development. The puzzle can enter the statuses "Idea in Development", "Awaiting Answer", or "Writing (Answer Assigned)." The biggest qualitative change comes when the puzzle enters the Testsolving status.

## Testsolving {: #testsolving}

Testsolving occurs on the [Testsolve page](/testsolve). Testsolving is organized into "testsolving sessions". A session represents a person or a group of people attempting to solve a particular puzzle.

Only testsolving admins can start sessions under the [testsolving admin dashboard](/testsolve_admin).

On a testsolve session's page, you can leave feedback and submit guesses.

When you want to give feedback (intermediate or final), click "Give feedback" to be taken to a page where you can leave more detailed feedback.
This feedback will be visible to other participants viewing the session as well as to the puzzle author or anybody else viewing the spoilery puzzle page itself.

Also note that you can fill out this form multiple times after you've finished; the fun/difficulty/hours spent ratings from your most recent submission will be taken.
The feedback will be concatenated.

As an author or editor on a puzzle, basically you should just look at the incoming testsolves and decide what edits you want to make, or when to send it to EICs for approval to leave testsolving.

## Hint-Writing

Hints were a fairly late addition to PuzzUp in 2021. The numbers and keywords don't have any inherent meaning in PuzzUp; the intent is for them to be exported into other systems, such as spoilr.

## Discord {: #discord}

Every new puzzle that's created on PuzzUp gets a channel created on our Discord. As the puzzle moves through statuses, it moves to a new category on Discord for that status. Most importantly, the only people who can see (or add themselves to) a channel are those who are spoiled on the puzzle here on PuzzUp.

If you've already created a channel in discord, you (or anyone else) may type the `/up create` command in that channel, and it will create a PuzzUp record for that channel.
