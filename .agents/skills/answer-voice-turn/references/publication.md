# Publishing an answer to a spoken turn

Loaded from [`../SKILL.md`](../SKILL.md), which owns the sequence these commands sit inside.
`../../../../bin/fm-inbox.sh conversation --help` remains the owner of every field, default, and refusal; the shapes below are a starting point so a live call is not spent reading help.
Every command takes one JSON object on stdin, prints one JSON object, and needs `FM_HOME` set to this home's path.
An empty `FM_HOME` is refused rather than defaulted.

## Accept

```sh
FM_HOME="$FM_HOME" bin/fm-inbox.sh conversation accept <<<'{"conversation_id":"<cid>"}'
```

`dispatch: true` carries `input`, including the `request_id` to answer and the committed transcript.
Acceptance is committed before the command returns, so the same turn is never dispatched twice; a crash at that boundary leaves an accepted request whose work state is unknown, which is why an interrupted claim is reconciled rather than re-run.
`dispatch: false` means nothing is waiting.

Repeat the call until `dispatch: false`.
One `accept` claims one turn, and turns are returned oldest first, so an earlier question from the same call is answered before a later one - and so a turn an earlier call left behind is answered before the live one, which is what the supersession below prevents.

## Publish

```sh
FM_HOME="$FM_HOME" bin/fm-inbox.sh conversation publish <<'JSON'
{"conversation_id":"<cid>","request_id":"<rid>","response_id":"<unique>",
 "sequence":1,"kind":"answer","final":true,
 "destination":"elevenlabs","speech_text":"..."}
JSON
```

`kind` is one of `receipt`, `progress`, `question`, `answer`, or `error`.
`sequence` starts at 1 for each request and increments by one for each further portion.
Each portion is at most 1200 characters, so a long answer is published as several portions rather than truncated.

A `response_id` is immutable.
Repeating a publication identically is a safe retry; the same id with different text is refused rather than quietly replacing what was already said.

## Ordered portions keep a slow turn audible

Anything slower than reading records publishes a first portion before the work starts: `kind: progress` with `final: false`.
Publish further progress portions in sequence while the work runs, and close with the answer at `final: true`.

The stream closes at that final portion, so publish nothing further for that request.
A later development belongs to a new turn, not to a closed stream.

Say what is happening and what it means for the ask, never the machinery.
"Still reading the records" is a progress portion; "waking a worker" is not.

## Ask for what is missing

A turn that does not say enough to be an order, as [`speaking.md`](speaking.md) defines it, is accepted like any other turn and answered with a `kind: question` portion, with no receipt, progress, or answer published for it and no work started on it:

```sh
FM_HOME="$FM_HOME" bin/fm-inbox.sh conversation publish <<'JSON'
{"conversation_id":"<cid>","request_id":"<rid>","response_id":"<unique>",
 "sequence":1,"kind":"question","final":false,"question_binding":"<rid>-missing-part",
 "destination":"elevenlabs","speech_text":"..."}
JSON
```

A question carries a `question_binding` that must be unique for the life of the conversation rather than only for this call, so derive it from the `request_id` being asked about instead of from the topic: a binding named for what was missing collides the next time the same part is missing, and `publish` refuses it.
The question stays open until the caller's next turn arrives bound to it and consumes it at acceptance; `final` governs only whether more portions may follow on this request, not whether the question is open.
The completed order is answered as that later turn, read together with this one as [`speaking.md`](speaking.md) defines, rather than by publishing more against this one.
Such a turn is not rejected, because rejection is for a turn that should not be run at all, and an incomplete one is waiting to be completed.

## Reject

A turn that should not be run is declined explicitly, which keeps its transcript and reason without pretending the work was taken up:

```sh
FM_HOME="$FM_HOME" bin/fm-inbox.sh conversation reject <<<'{"conversation_id":"<cid>","request_id":"<rid>","reason":"..."}'
```

A rejected request may still be answered with `kind: error` or `kind: question`, and never with a receipt, progress, or answer that would imply its work was accepted.

## A new call supersedes what the last one left saved

Step 1 of [`../SKILL.md`](../SKILL.md) owns the rule for which call is live and which `saved` requests it supersedes.
`audit` reports each request's `call_id` beside its state, in capture order:

```sh
FM_HOME="$FM_HOME" bin/fm-inbox.sh conversation audit <<<'{"conversation_id":"<cid>"}'
```

A superseded request is rejected with this exact reason:

```sh
FM_HOME="$FM_HOME" bin/fm-inbox.sh conversation reject <<<'{"conversation_id":"<cid>","request_id":"<rid>","reason":"prior call ended; superseded"}'
```

Publish nothing against a superseded request.
The reject-then-question route below exists so someone still on the line hears what went wrong; nobody is on the line of a call that ended, and the reason on the record is what `audit` reports afterwards.

## Reconcile a call that went quiet

`audit` (owner) and `poll` (transport) both report request state:

```sh
FM_HOME="$FM_HOME" bin/fm-inbox.sh conversation audit <<<'{"conversation_id":"<cid>"}'
```

A request still `saved` is a question nobody answered, whatever the inbox says.
Answer it if it is still worth answering, or reject it with the reason.
The wake drain holds the row of a still-`saved` request through every acknowledgement and presents it again, so such a request keeps resurfacing until it is accepted or rejected.

Three `saved` states can never be accepted, and each needs the same route out.

The first is a request whose `previous_turn_id` names a turn this conversation never captured, which `audit` shows as a `previous_turn_id` matching no `turn_id` in its list.
Capture admits such a turn, but `accept` waits for the predecessor and returns `dispatch: false` while the predecessor can no longer arrive, and `publish` needs an accepted request.

The second is a correction whose `correction_of` names a request that is not `accepted`, and the third is a `question_binding` that is stale or unknown: it names no open question, or a question a previously accepted turn already consumed, which happens when the caller answers the same question twice.
In these two states `accept` does not skip the request: it refuses for the whole conversation, exiting 2 with `correction target not accepted here` or `stale or unknown question binding`, and because it takes turns oldest first, every later spoken turn sits `saved` behind that request until it is rejected.

The only route the transport allows in all three states is to reject the request with its reason and then publish a `kind: question` or `kind: error` portion against it, so the caller hears what went wrong and can say it again instead of hearing nothing; once it is rejected, `accept` moves on to the turns behind it:

```sh
FM_HOME="$FM_HOME" bin/fm-inbox.sh conversation reject <<<'{"conversation_id":"<cid>","request_id":"<rid>","reason":"previous turn <turn> was never captured"}'
FM_HOME="$FM_HOME" bin/fm-inbox.sh conversation publish <<'JSON'
{"conversation_id":"<cid>","request_id":"<rid>","response_id":"<unique>",
 "sequence":1,"kind":"question","final":false,"question_binding":"<rid>-say-again",
 "destination":"elevenlabs","speech_text":"I lost the turn before this one. Could you say it again?"}
JSON
```

Late answers still land, because the conversation names the earlier question a reply belongs to before speaking it; write the answer so it makes sense after that framing rather than assuming the question is still fresh.

A published reply whose delivery state is `waiting` has not been spoken yet.
An `interrupted` or `unknown` receipt is left visible on purpose and is never resolved by publishing the same words again.

## What a refusal is telling you

Refusals are the transport declining to do something unsafe, not transient errors to retry around:

- an ownership refusal means this process does not hold this home's session lock, whether because `FM_HOME` is not explicit, the lock names another live session, or this is a Pi supervision branch; it never means the conversation belongs to an earlier session, because ownership follows the lock and the session holding it takes over every conversation in the home, including requests a restarted session left `saved`;
- a `correction target not accepted here` or `stale or unknown question binding` refusal from `accept` means the oldest `saved` request is one that can never be accepted, and it blocks every turn behind it until it is rejected; take the reject-then-question route in the reconcile section above;
- a `question binding already used` refusal from `publish` means some earlier reply in this conversation already carries that `question_binding`, which holds for the life of the conversation and not just this call; publish the question with a binding derived from its `request_id`;
- a `not superseded` refusal from `reject` means the request belongs to the most recently captured call, which a redial made live after your `audit`; step 1 of `../SKILL.md` says to re-run `audit` and restart the pass;
- a sequence or closed-stream refusal means the portion you are publishing does not follow the one already published;
- an identity refusal means a `request_id` or `response_id` is being reused with different content, and the durable record wins.
