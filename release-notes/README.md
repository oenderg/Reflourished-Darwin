# Release notes

One file per release, `<version>.md`, with its **What's new**: a few short lines in players' words
(what they will notice, not how it was done). Write it before running the release workflow; the run
stops without it. `scripts/release-notes.py` puts the release's page together from three parts:

1. What's new: this release's file.
2. All changes, folded: the feat, fix and perf commits since the previous tag, from their
   Conventional Commits titles, and the compare link. Left out for the first release.
3. `footer.md`: the downloads (a table of the files the run built, with their sizes) and the install
   steps, the same every release.

The same What's new text is the AltStore / SideStore changelog and the Mac update window's notes.

**Which apps it updates.** A release that changes only some apps names them in one line,
`Updates: Windows` (or `iPhone and iPad`, `Mac`, `Windows`, comma-separated; no line means all).
The publish run moves only those apps' update feeds (the AltStore / SideStore source for iPhone and
iPad, the Mac and Windows update feeds) to the release; the others stay on the release before, so
nobody is told about an update that changes nothing for them. The line is not shown anywhere. A
publishing run refuses a line that leaves out an app whose own files (the Windows folder, the Mac or
iPhone app's sources) changed since the previous release; shared code is for you to judge.
