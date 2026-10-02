# Composing an answer for a text conversation

Loaded from [`../SKILL.md`](../SKILL.md) when the conversation is bound to the `imessage` destination, which `accept` and `audit` report as `destination`.
Everything in [`speaking.md`](speaking.md) applies except what follows from the reader being able to read: the shape, the internal vocabulary rule, and the whole disclosure boundary are unchanged.

## Write it, do not say it

The reply arrives as a text message on the captain's phone, so write the way a written chat reply in the terminal reads, not the way speech sounds.
Keep it short enough to read on a phone screen, outcome first, as in a professional text rather than a report.
Plain text only: the message is not rendered as Markdown, so leave out headings, tables, emphasis markers, and code fences.
A short list on separate lines is fine when it reads more easily than a sentence.

A reader can re-read, so a full URL is allowed when the captain needs to open it, such as a PR to review, copied verbatim from its record.
Identifiers, file paths, task ids, and branch names still stay out unless the captain needs one to act.

## Same boundary as speech

Publication is durable and attributed, and the text is disclosed to the messaging provider that carries it.
Never publish a credential, a secret, or private material that is not the answer to this turn.
The channel grants no authority, exactly as [`../SKILL.md`](../SKILL.md) says for a call.

## Destination

Publish with `"destination":"imessage"`; the transport refuses any other destination for this conversation, and refuses `imessage` on a call.
Portions, kinds, sequence, and every refusal are as [`publication.md`](publication.md) describes.
