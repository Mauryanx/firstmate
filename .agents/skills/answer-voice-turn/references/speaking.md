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
What to do is never inferred, at any confidence: an unbound turn that does not say it is answered with a `question` portion that names it among the missing parts, per the rule below, and no work is started on it.
Whether a missing project or target may be inferred is governed by `../../../../AGENTS.md` section 7 intake, unchanged: proceed only on one genuinely confident match against the registry, work under way, and project code, and ask when multiple or no projects plausibly match.
The spoken channel adds to that rule rather than replacing it, so section 7 decides whether the part may be inferred and this section decides how the turn is answered once it has.
A project or target that was inferred is said back in the reply, so the caller hears the assumption and can correct it; on one genuinely confident match the work proceeds while the assumption is spoken.
Anything short of one clear confident match is answered with a `question` portion naming every part that is missing, which project and what it should do alike, and no work is started on it: a guess that lands wrong is indistinguishable from an order the captain never gave, and it is acted on as if he had given it.
One question is asked per order and it names all of those parts at once, never a chain of questions one part at a time.
Ask for the missing parts alone rather than reading the fragment back in full, and leave that question open so the caller can supply them as the next turn, per [`publication.md`](publication.md).

A turn captured bound to an open question is read against the question it answers, and there are exactly three of those.

The first is the one missing-part question this section asked about an order, and its reply is read for one thing: whether it supplies the parts that question named.
When it does, it completes that order, the pair is the order, and the work starts as soon as the pair is an order as defined above.
When anything is still missing, the order was not understood: it is abandoned rather than filed or guessed at, and the captain is asked for it again, whole, as the second case below - never a second question chained to the first.
When the reply is a correction, a change of subject, a refusal, or a never mind, it is answered as its own turn on its own merits, and the earlier incomplete order is abandoned rather than filed, guessed at, or asked about again.
The order such a reply completes is named by its own `question_binding`, which was derived from that order's `request_id`, and the order's words are read from the record rather than from memory so the pair survives a session restart: `audit` lists that request with its `note_id`, and the note of a request already accepted is under `state/inbox/handled/`, read as step 2 of [`../SKILL.md`](../SKILL.md) describes.

The second is a question that asked for an order to be said again: the `-say-again` question the reconcile route in [`publication.md`](publication.md) publishes in any of the three `saved` states it serves, and the one this section publishes after abandoning an order that was still not understood.
Its reply is the order said again, whole and on its own, so nothing is paired into it and it is dispatched as soon as it is an order as defined above.

The third is any other published question, an ordinary clarifying question about work Firstmate proposed above all: its reply is an answer, not an order, and never becomes one however much it settles.
It is never authority either, so it licenses no work, no merge, no discard, and nothing else destructive, irreversible, or security-sensitive; the channel grants no authority, as [`../SKILL.md`](../SKILL.md) says.
When such an answer settles a worker's open decision, close that decision record the ordinary way rather than treating the answer as a dispatch: pass `fm-send`'s `--resolve-key` so the answer closes it at answer time, which `../../../../AGENTS.md` section 7 owns and `../../../../bin/fm-send.sh` contracts.

The first two hold within one call only.
A question the captain never answered stays open after his call ends, and the bridge binds the next call's opening turn to it, so the `call_id` that `audit` reports for the question's own request must be the live call of step 1 of [`../SKILL.md`](../SKILL.md) before anything is paired or re-issued.
A question opened on a call that ended is stale: its bound turn is answered on its own merits, and the order that call left incomplete is not completed.

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
