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
Late answers still land, because the conversation names the earlier question a reply belongs to before speaking it; write the answer so it makes sense after that framing rather than assuming the question is still fresh.

A published reply whose delivery state is `waiting` has not been spoken yet.
An `interrupted` or `unknown` receipt is left visible on purpose and is never resolved by publishing the same words again.

## What a refusal is telling you

Refusals are the transport declining to do something unsafe, not transient errors to retry around:

- an ownership refusal means this process is not the session that owns the conversation, so nothing about the reply is wrong - the wrong actor is asking;
- a sequence or closed-stream refusal means the portion you are publishing does not follow the one already published;
- an identity refusal means a `request_id` or `response_id` is being reused with different content, and the durable record wins.
