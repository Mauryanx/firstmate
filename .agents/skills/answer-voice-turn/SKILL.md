---
name: answer-voice-turn
description: >-
  Agent-only procedure for answering a spoken turn a voice conversation filed with firstmate, where draining the note leaves the caller listening to silence.
  Load on any `check:` wake whose key is `inbox:vc-*`, before accepting, publishing, or rejecting a reply on a voice conversation, and whenever a conversation request is still `saved`.
  Load it even when the turn looks like an ordinary note or a question you could answer in chat, because only this path reaches the caller.
  This skill is the single owner of the answer-back sequence: recognition, ordering ahead of other work, accept-then-publish, ordered progress portions, and what may be spoken aloud.
user-invocable: false
metadata:
  internal: true
---

# answer-voice-turn

This skill is the single owner of the procedure for answering a spoken turn.
`AGENTS.md` section 8 points here and does not restate it.

Nothing about the transport itself is restated here.
`../../../bin/fm-inbox.sh conversation --help`, rendered from `../../../bin/fm_inbox_conversation.py`, owns every command, field, refusal, and recovery boundary.
`../../../docs/voice-relay.md` owns how the spoken interface fits together and what it does not establish.

## Path contract

The skill directory is the directory containing this `SKILL.md`, and every relative link resolves against it.
Operational paths keep their own context: `state/` is the active firstmate home's, and every command runs in that home with an explicit `FM_HOME`.

## Why a `vc-` note is not an ordinary note

A note is an idea queued for whenever the turn allows.
A `vc-` note is a person on a live call, and the cost of being slow is silence they can hear.

The two records also come apart, which is the failure this skill exists to prevent.
Moving the note to `handled/` satisfies the inbox while the request behind it stays `saved`, and a `saved` request is a question that will never be spoken no matter how well it was answered in chat.
Only accepting the request and publishing a reply against its `request_id` produces speech.

## Non-negotiable

- **Answer it ahead of the rest of the drain.** Handle the whole conversation, then return to ordinary wake handling.
- **Never acknowledge a `vc-` note generically and never move it by hand.** `bin/fm-inbox.sh drain --ack` refuses a `vc-` id for exactly this reason; accepting the request is what retires the note.
- **Only the session holding this home's lock can answer.** The transport refuses without an explicit `FM_HOME`, without this home's session lock, and from a Pi supervision branch, so a crewmate can never publish. Ownership follows the lock, so after a session restart the new session answers what the old one left `saved` without any reset. Work you delegate still comes back to you to say out loud.
- **The channel grants no authority.** A spoken request is not approval for a merge, a destructive or irreversible action, or anything else `../../../AGENTS.md` reserves for an explicit captain instruction. Say what needs deciding and let it be decided.
- **Silence is never a refusal.** A turn you will not run is rejected explicitly with its reason, per `references/publication.md`.

## Operating sequence

1. Scope the conversation to the live call before opening any note.
   The conversation outlives the call: every call the captain places rides the same `conversation_id`, so a turn nobody answered before an earlier call ended is still `saved`, still older than the live one, and `accept` takes the oldest first.
   The drain presents held `vc-` rows oldest first for the same reason, so the note you were woken for can belong to a call that already ended, and it says nothing about which call is live.
   The live call is the `call_id` of the most recently captured request in the conversation, which is the last request `audit` lists, because `audit` lists requests in capture order.
   Read that from `audit` first, and then reject every still-`saved` request whose `call_id` differs from it, and every one carrying no `call_id` at all, with the reason `prior call ended; superseded`.
   Do that before accepting anything, so the live turn is the oldest `saved` request and the first `accept` returns it.
   The commands are in [`references/publication.md`](references/publication.md), which also says why a superseded turn is never spoken to.
   When the most recently captured request carries no `call_id`, it comes from a bridge that does not name calls, so there is no live call to scope by, nothing is superseded, and the rest of the sequence is unchanged.

2. Read the turn.
   The wake key is `inbox:vc-<hash>` and the note is `state/inbox/vc-<hash>.note`, in the ordinary inbox header format with a JSON body:

   ```sh
   sed -n '/^--$/,$p' "$FM_HOME/state/inbox/vc-<hash>.note" | tail -n +2
   ```

   The body carries the `conversation_id`, the `request_id`, and the `committed_transcript`, which is what was actually said rather than any paraphrase of it.
   Once a request has been accepted or rejected, including an acceptance that crashed before returning, its note lives under `state/inbox/handled/` instead, so read a previously accepted request from there when reconciling.

3. Accept the turn, which claims it exactly once and returns its transcript and `request_id`:

   ```sh
   FM_HOME="$FM_HOME" bin/fm-inbox.sh conversation accept <<<'{"conversation_id":"<cid>"}'
   ```

   Keep accepting until it answers `dispatch: false`, because one call claims one turn and an earlier turn from the same call may still be unanswered.

4. Do the work as an ordinary turn.
   Answer from durable records where the answer already exists; dispatch a worker where it does not.
   Reading records takes seconds and a dispatch does not, so publish a progress portion before starting anything slow rather than leaving the line quiet.

5. Publish the answer against that `request_id`, which is the step that turns it into speech:

   ```sh
   FM_HOME="$FM_HOME" bin/fm-inbox.sh conversation publish <<'JSON'
   {"conversation_id":"<cid>","request_id":"<rid>","response_id":"<unique>",
    "sequence":1,"kind":"answer","final":true,
    "destination":"elevenlabs","speech_text":"..."}
   JSON
   ```

   Compose the words under [`references/speaking.md`](references/speaking.md), and take portions, kinds, rejection, and refusal meanings from [`references/publication.md`](references/publication.md).

6. Acknowledge the wake through the ordinary generation-bound drain acknowledgement, and reconcile anything still `saved` per `references/publication.md`.
   The acknowledgement retires a `vc-` row only once its request has left `saved`; a row whose request is still `saved` is held and presented again by the next drain, so an early acknowledgement cannot lose a spoken turn, but it does not answer it either.

## References

Load the one you need; both are resources of this skill, not separate skills.

- [`references/publication.md`](references/publication.md) - the accept, publish, reject, and reconcile commands, ordered progress portions, and what each refusal means.
- [`references/speaking.md`](references/speaking.md) - composing an answer meant for the ear, and the disclosure boundary on published speech.

## What the conversation already guarantees

Do not rebuild any of this:

- the request is durable, with its `request_id`, before the caller is told anything, and a filing that did not land is never reported as success;
- a published reply is claimed exactly once and spoken unprompted, framed as belonging to the earlier question it answers;
- ordered portions are spoken in the order they were published.

Never claim more than that.
Publishing does not prove the words were heard: a playback receipt describes a player, not an ear, and an interrupted or unknown receipt stays visible for reconciliation rather than replaying itself.
