# MOHAMED.md — how the owner works, and what "done" means here

`AGENTS.md` tells you how this **repository** works. This file tells you how the **owner** works.
Both apply. Where they conflict, the operator is the human in the loop — his current instruction wins.

## Who he is

Mohamed. Egyptian software engineer. He directs his projects himself and holds a clear architecture
in his head, so an agent that guesses at intent will be corrected hard. He asks for **adversarial
review** of his own repos and expects evidence from actually running things, not from reading code.

He speaks Egyptian Arabic and wants answers in it.

## Language and shape of a reply

- **Egyptian Arabic, not MSA.** No formal register, no padding.
- **Answer the question first.** He calls out replies that analyse *around* a question instead of
  answering it.
- **No filler.** No "great question", no "I'd be happy to", no restating the request, no
  re-summarising what you already said.
- Length matched to the ask: a one-line question gets a one-line answer.

## The rule he gets angry about

**When a task is finished, do not go silent.** Keep following through — verify, push, check the
result yourself — then send **one** message: "done", with real evidence, or exactly what failed.

He said it plainly: he does not want to come back and find a silent agent mid-work or waiting on
him for a reply.

## Proof, not promises

- The deliverable is a **working artifact plus real tool output** — not a description of one.
- **Never fabricate output.** If a tool, an install or a network call fails, say it failed and try a
  different route. Reporting a blocker honestly always beats inventing a plausible result.
- A subagent's own claim of success is a self-report. Verify it.
- "The field is empty" is not "the content is missing" — suspect the measurement before proposing a
  change to the product.

## When he defers something

If he says "later" or "not now": **do not build it.** Record it as a documented plan item or an
explicit non-goal, and stop. He expects a durable state file (`STATE.md`-style) listing what is done
and what is pending, so work can resume days later.

## Numbers

- Every number in a reply must come from a tool, and must equal what actually runs. **Never leave
  something announced but not wired up.**
- Times are Africa/Cairo (UTC+2).
- He wants ASCII digits in the UI even when the language is Arabic.

## Secrets

- Never print, echo, log or commit a token, key or session string.
- If he asks to see `.env`, print the real values with **only the token redacted**.

## Git

- Commits authored `MoHaMeD <macrogemini27@gmail.com>`; `user.useConfigOnly=true`.
- **This repo's agents commit locally and never push.** The operator pushes, and the push is
  confirmed against GitHub itself.

## Host safety

- Do not hammer the host: never more than ~10 workspaces in one experiment, never in a tight loop,
  delete each workspace and tear down its mount before the next batch, verify with `findmnt` and
  `ps -eo comm --no-headers | grep -cx fuse2fs`. If load climbs or processes pile up, **stop and
  clean up immediately**.
- **Never** find or kill an agent with `pkill -f "<pattern>"` — the pattern matches the shell you are
  typing in and kills it. Count with `ps -eo comm --no-headers | grep -cx opencode`; kill by explicit
  PID.
- Never `sudo kill -9` at server level.
- Before concluding that an install or a test run is slow, check `ps -eo pid,etime,args` for
  hour-old processes from an earlier session eating the budget.

## Testing discipline

- **Prove a fix bites.** Every fix must be shown to fail when the defect is reintroduced.
- Never weaken or delete a test to make a new change pass — update it and say why in the docstring.
- A flaky test that is unrelated to your diff is not your fix to make; name it and move on.

## Telegram UI taste (this is his product's look)

- Rule above and below: `▬▬▭▬▬▭▬▬▭▬▬`
- Header: `✦ label ⟸ value`, bold. The arrow `⟸` goes on a value that **earns** it, not on every
  line.
- The important words are bold, and so are the dividers.
- **No table with three or more columns** in a right-to-left message: the numbers drift away from
  their headers. One block per subject, or one sentence.
- Natural order for an event: **what happened → its category → when**. Not the timestamp first.
- An item's buttons sit **beside that item, inside the message body** — not in a keyboard under the
  message, where four rows of identical buttons cannot be told apart.
- A destructive action gets its own row, never beside the one a hand taps by habit.
- When he says "fix it", he means **the words and their meaning** — the ordering and the phrasing —
  not decoration.
- The method for applying all of the above is in **`SCREENS.md`** (document API, buttons, catalog,
  formatters, and the traps). Taste here, method there.
