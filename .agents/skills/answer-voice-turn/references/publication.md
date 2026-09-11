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
One call claims one turn, and turns are returned oldest first, so an earlier question from the same call is answered before a later one.

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

## Reject

A turn that should not be run is declined explicitly, which keeps its transcript and reason without pretending the work was taken up:

```sh
FM_HOME="$FM_HOME" bin/fm-inbox.sh conversation reject <<<'{"conversation_id":"<cid>","request_id":"<rid>","reason":"..."}'
```

A rejected request may still be answered with `kind: error` or `kind: question`, and never with a receipt, progress, or answer that would imply its work was accepted.

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
 "sequence":1,"kind":"question","final":false,"question_binding":"<unique>",
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
- a sequence or closed-stream refusal means the portion you are publishing does not follow the one already published;
- an identity refusal means a `request_id` or `response_id` is being reused with different content, and the durable record wins.
