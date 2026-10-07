#!/usr/bin/env python3
"""Publish an untrusted courier proposal or read immutable courier receipts.

Usage: fm-courier.py submit < request.json
       fm-courier.py results ID DIGEST
       fm-courier.py notify TO < notification.txt
       fm-courier.py delivery ID DIGEST
Opt in with FM_COURIER_ENABLED=1; absent/off exits 3 without I/O.
FM_COURIER_OUTBOX defaults to /srv/brain/courier/outbox.
FM_COURIER_INBOX defaults to /srv/brain/courier/inbox.
FM_COURIER_RESULT_USER defaults to courier (local fixture identity seam).
Submit takes the existing courier request schema; attachment paths name local
regular files. It snapshots bytes into flat 0640 outbox files before atomically
publishing ID.json, then prints only ID/digest/idempotency metadata (not sent).
An existing ID.json refuses; retry/edit after claim uses the same ID, while a
new intentional message needs a new ID. Attachment snapshots remain in outbox.
Results prints ALL matching immutable snapshots, not a guessed latest status.
No receipt exits 4 with an empty list. Refusal exits 1; usage exits 2.
Only courier can approve, enforce policy/limits, or contact a provider.
Notify is the Firstmate active-alert adapter: it assigns a fresh ID, publishes
the text-only imessage proposal and checks matching receipts. Delivery checks
that same ID/digest later. Both print submission identity, all receipts and a
delivered flag; only a matching sent receipt exits 0. Unconfirmed delivery
(including waiting, approved, unknown or denied snapshots) exits 4. Receipt
refusal exits 1 while preserving the published identity on stdout for recovery.
Never retry notify to poll or resolve an unknown send; use delivery instead.
"""

import argparse
import hashlib
import os
from pathlib import Path
import pwd
import re
import secrets
import sys

from fm_zone_io import Refused, canonical, directory, fields, protected, publish, read_at, strict_json

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
RECEIPT = re.compile(r"result-[0-9a-f]{64}\.json\Z")
JSON_LIMIT = 131072
BYTE_LIMIT = 104857600
RESULTS = {"new", "waiting", "approved", "expired", "closed", "pending", "unknown",
           "sent", "denied", "denied-limit"}


def text(value, limit, empty=False):
    if (not isinstance(value, str) or len(value) > limit or (not empty and not value.strip())
            or any(ord(c) < 32 and c not in "\n\r\t" for c in value)):
        raise Refused()
    value.encode("utf-8")
    return value


def identity(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Refused()
    return value


def key(identity_value, digest):
    return "courier-" + hashlib.sha256(canonical([identity_value, digest])).hexdigest()


def submit():
    data = sys.stdin.buffer.read(JSON_LIMIT + 1)
    if len(data) > JSON_LIMIT:
        raise Refused()
    return publish_request(strict_json(data))


def publish_request(row):
    row = fields(row,
                 {"id", "channel", "to", "text", "purpose", "attachments"}, {"approval_ref"})
    identity(row["id"])
    if row["channel"] != "imessage":
        raise Refused()  # Other adapters are intentionally unreviewed/denied.
    if not isinstance(row["to"], str) or not re.fullmatch(r"\+[1-9][0-9]{1,14}", row["to"]):
        raise Refused()
    text(row["text"], 10000, empty=True)
    text(row["purpose"], 500)
    if "approval_ref" in row:
        identity(row["approval_ref"])  # Opaque correlation, NEVER approval.
    if (not isinstance(row["attachments"], list) or len(row["attachments"]) > 16
            or (not row["text"].strip() and not row["attachments"])):
        raise Refused()
    snapshots = []
    total = 0
    for attachment in row["attachments"]:
        fields(attachment, {"path", "name", "content_type"})
        name = text(attachment["name"], 255)
        if "/" in name or "\\" in name or any(ord(c) < 32 for c in name):
            raise Refused()
        content_type = text(attachment["content_type"], 255)
        # Courier owns type/extraction policy, recipient policy and leak checks.
        path = Path(text(attachment["path"], 4096))
        parent = directory(path.parent)
        try:
            data = read_at(parent, path.name, BYTE_LIMIT - total)
        finally:
            os.close(parent)
        if not data:
            raise Refused()
        total += len(data)
        digest = hashlib.sha256(data).hexdigest()
        snapshots.append((data, {"name": name, "content_type": content_type, "sha256": digest}))
    payload = {k: row[k] for k in ("channel", "to", "text", "purpose")}
    payload["attachments"] = [record for _, record in snapshots]
    digest = hashlib.sha256(canonical(payload)).hexdigest()
    wire = dict(row, attachments=[{"path": "blob-" + record["sha256"],
                                   "name": record["name"], "content_type": record["content_type"]}
                                  for _, record in snapshots])
    encoded = canonical(wire)
    if len(encoded) > JSON_LIMIT:
        raise Refused()
    parent = directory(os.environ.get("FM_COURIER_OUTBOX", "/srv/brain/courier/outbox"))
    try:
        for (data, _), item in zip(snapshots, wire["attachments"]):
            try:
                publish(parent, item["path"], data)
            except Refused:
                if read_at(parent, item["path"], BYTE_LIMIT, uid=os.getuid(), mode=0o640) != data:
                    raise
        publish(parent, row["id"] + ".json", encoded)
    finally:
        os.close(parent)
    return {"id": row["id"], "digest": digest, "idempotency_key": key(row["id"], digest),
            "result": "submitted"}


def results(identity_value, digest):
    identity(identity_value)
    if not DIGEST.fullmatch(digest):
        raise Refused()
    uid = pwd.getpwnam(os.environ.get("FM_COURIER_RESULT_USER", "courier")).pw_uid
    parent = directory(os.environ.get("FM_COURIER_INBOX", "/srv/brain/courier/inbox"))
    try:
        protected(parent, uid)
        found = []
        for name in sorted(os.listdir(parent)):
            if not RECEIPT.fullmatch(name):
                continue  # Never ingest ordinary inbound messages as receipts.
            row = fields(strict_json(read_at(parent, name, JSON_LIMIT, uid=uid, mode=0o640)),
                         {"kind", "id", "digest", "result", "approval_ref", "idempotency_key"})
            identity(row["id"])
            if (row["kind"] != "courier-result" or not isinstance(row["digest"], str)
                    or not DIGEST.fullmatch(row["digest"]) or not isinstance(row["result"], str)
                    or row["result"] not in RESULTS
                    or row["idempotency_key"] != key(row["id"], row["digest"])
                    or name != "result-" + hashlib.sha256(canonical(row)).hexdigest() + ".json"):
                raise Refused()
            if row["approval_ref"] is not None:
                identity(row["approval_ref"])
            if row["id"] == identity_value and row["digest"] == digest:
                found.append(row)
        return found
    finally:
        os.close(parent)


def delivery(identity_value, digest):
    row = {"id": identity_value, "digest": digest, "delivered": False, "receipts": None}
    try:
        row["receipts"] = results(identity_value, digest)
    except (OSError, ValueError, KeyError, TypeError, RecursionError, UnicodeError):
        print("courier refused notification receipts", file=sys.stderr)
        return row, 1
    row["delivered"] = any(receipt["result"] == "sent" for receipt in row["receipts"])
    return row, 0 if row["delivered"] else 4


def notify(recipient):
    message = sys.stdin.buffer.read(40001).decode("utf-8")
    submitted = publish_request({"id": "notify-" + secrets.token_hex(16), "channel": "imessage",
                                 "to": recipient, "text": message,
                                 "purpose": "Firstmate active notification", "attachments": []})
    row, status = delivery(submitted["id"], submitted["digest"])
    row["submission"] = submitted
    return row, status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("submit")
    reader = commands.add_parser("results")
    reader.add_argument("id")
    reader.add_argument("digest")
    notifier = commands.add_parser("notify")
    notifier.add_argument("to")
    checker = commands.add_parser("delivery")
    checker.add_argument("id")
    checker.add_argument("digest")
    args = parser.parse_args()
    if os.environ.get("FM_COURIER_ENABLED") != "1":
        print("courier unavailable (opt-in required)", file=sys.stderr)
        return 3
    try:
        if args.operation == "notify":
            result, status = notify(args.to)
        elif args.operation == "delivery":
            identity(args.id)
            if not DIGEST.fullmatch(args.digest):
                raise Refused()
            result, status = delivery(args.id, args.digest)
        else:
            result = submit() if args.operation == "submit" else results(args.id, args.digest)
            status = 4 if result == [] else 0
        sys.stdout.buffer.write(canonical(result) + b"\n")
        return status
    except (OSError, ValueError, KeyError, TypeError, RecursionError, UnicodeError):
        print("courier refused request or result", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
