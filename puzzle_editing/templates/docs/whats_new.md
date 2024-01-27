This is an overview of new features added since Puzzlord.

## UI

The interface is completely overhauled. The core functionality remains the same so if you’re familiar with Puzzlord, most things are in the same place as before.

## Discord

Puzzup is now integrated with Discord!

* Each puzzle entry gets an associated private Discord channel created that you can use to discuss with your coauthors and editor(s).
* Discord channels are grouped under category by status.
* Each testsolve session creates a “private” Discord thread under a custom channel (not actually private - we suggest asking teammates to mute the custom channel to avoid seeing the list of sessions).

## Google Drive Integration

Puzzup is now integrated with Google Drive!

* Creating a new puzzle will auto-create a brainstorming sheet in a specified folder.
* Creating a new testsolve session will auto-create a testsolve sheet. Moving a puzzle to “Needs Factcheck” will auto-create a copy of a template factcheck sheet.

## Autopostprodding

When postprodding a puzzle, Puzzup automatically generates a React file exported from Google Docs. It also copies metadata from Puzzup into the hunt repo and creates a commit.

With a bit of work, this can be generalized to generate a raw HTML file instead, based on an arbitrary template file.

## Other Credits

There is now support to credit roles such as Art and Tech for each puzzle in addition to the puzzle authors themselves.

## Roles for Art, Tech, Accessibility

Users can be assigned into Art, Tech, Accessibility roles and on a puzzle page you can create support tickets to notify users of the relevant role. For example, you might want help with creating custom art for your puzzle. In that case you can create a support ticket and specify what the art requirements are so that the art team is aware.
