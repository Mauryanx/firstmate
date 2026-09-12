# Composing an answer for the ear

Loaded from [`../SKILL.md`](../SKILL.md).
Everything in `../../../../AGENTS.md` section 9 applies unchanged: outcomes and consequences in the captain's own nouns, never internal mechanics.
Speech tightens that, because a listener cannot re-read the sentence and cannot skim past a detail that does not matter.

## Shape

Say the outcome first, then what it means, then the decision if there is one.
Prefer short sentences over clauses stacked with commas.
Answer the question that was asked before adding anything adjacent to it.

A spoken answer is finished when the listener could act on it.
Detail that only proves the work was done belongs in a record, not in the air.

## When the turn does not say enough

A dispatched turn carries an order only when it says what to do, and either says which project or target it is for or lets `../../../../AGENTS.md` section 7 intake resolve that to one genuinely confident match.
What to do is never inferred, at any confidence: a turn that does not say it is answered with a `question` portion naming that as the missing part, and no work is started on it.
Whether a missing project or target may be inferred is governed by `../../../../AGENTS.md` section 7 intake, unchanged: proceed only on one genuinely confident match against the registry, work under way, and project code, and ask when multiple or no projects plausibly match.
The spoken channel adds to that rule rather than replacing it, so section 7 decides whether the part may be inferred and this section decides how the turn is answered once it has.
A project or target that was inferred is said back in the reply, so the caller hears the assumption and can correct it; on one genuinely confident match the work proceeds while the assumption is spoken.
Anything short of one clear confident match is answered with a `question` portion naming exactly the part that is missing, and no work is started on it: a guess that lands wrong is indistinguishable from an order the captain never gave, and it is acted on as if he had given it.
Ask for the missing part alone rather than reading the fragment back in full, and leave that question open so the caller can say it again as the next turn, per [`publication.md`](publication.md).

## Never read aloud

- Identifiers, file paths, task ids, branch names, and URLs.
  Name the project and the work instead, and say a link is waiting rather than spelling one out.
- Numbers whose precision does nothing for the listener.
  "Two of the three checks are still running" carries more than a timestamp does.
- Internal vocabulary of any kind.
  The translation table in `../../../../AGENTS.md` section 9 is the owner; speech does not get an exception because it feels shorter.

## The text is the utterance

What is published is spoken verbatim, with nothing added around it.
There is no second chance to clarify inside a portion, so read it back once as a sentence someone will hear before publishing it.

Publication is also durable and attributed: each portion records its author, timestamp, destination, and a digest of its exact content, and it is disclosed to a third-party speech provider.
That makes it written speech, accountable exactly as a written reply is.
Never publish a credential, a secret, or private material that is not the answer to this turn, and never treat the accountability record as evidence that any content was checked for safety - it records who said what, nothing more.

## Examples

Answering from records, one portion:

> The migration branch is green and waiting on your review. Nothing else is blocked behind it.

A slow turn, two portions:

> Still reading the records on that one.

then

> Both open branches passed. The older one has been waiting three days for your call on the schema change.

An instruction that did not say enough:

> I have that as a test of the new model, but not which project it is for. Which one?

Declining, honestly:

> I can prepare that change, but merging it is your call and I will not do it from a phone call.
