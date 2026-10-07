"""Drive the optional clients with synthetic data; never import client code."""

import copy
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import unittest

ROOT, TEMP = map(Path, sys.argv[1:3])
sys.argv = sys.argv[:1]
USER = pwd.getpwuid(os.getuid()).pw_name


def canonical(row):
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class Clients(unittest.TestCase):
    def setUp(self):
        self.home = TEMP / self.id().split(".")[-1]
        self.home.mkdir()
        for name in ("requests", "out", "outbox", "inbox", "fakebin", "state", "config"):
            (self.home / name).mkdir(mode=0o750)
        self.env = dict(os.environ, FM_BRAIN_DESK_ENABLED="1", FM_COURIER_ENABLED="1",
                        FM_BRAIN_DESK_REQUESTS=str(self.home / "requests"),
                        FM_BRAIN_DESK_OUT=str(self.home / "out"),
                        FM_BRAIN_DESK_RESULT_USER=USER, FM_COURIER_RESULT_USER=USER,
                        FM_COURIER_OUTBOX=str(self.home / "outbox"),
                        FM_COURIER_INBOX=str(self.home / "inbox"),
                        PATH=str(self.home / "fakebin") + os.pathsep + os.environ["PATH"],
                        FIXTURE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        self.answer = {"facts": [{"text": "Example Project has three maintainers.",
                                  "citations": [{"page": "projects/example", "view_line": 4}]}]}
        (self.home / "answer").write_bytes(canonical(self.answer))
        stub = self.home / "fakebin/sudo"
        stub.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, sys
p = pathlib.Path(os.environ['FIXTURE_HOME'])
(p / 'argv').write_text(json.dumps(sys.argv[1:]))
if os.environ.get('FIXTURE_FAIL'):
    sys.stderr.write('RAW_SECRET_FROM_MODEL')
    sys.exit(1)
sys.stdout.buffer.write((p / 'answer').read_bytes())
''')
        stub.chmod(0o750)
        self.request = {"id": "message", "channel": "imessage", "to": "+12025550102",
                        "text": "The example work is ready.", "purpose": "team report", "attachments": []}

    def run_cli(self, subject, args, data=b"", code=0, env=None):
        result = subprocess.run([sys.executable, str(ROOT / "bin" / subject), *args],
                                env=env or self.env, input=data, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, code, result.stderr.decode())
        return result

    def submit(self, row=None, code=0):
        return self.run_cli("fm-courier.py", ["submit"], canonical(row or self.request), code)

    def receipt(self, submitted, result="waiting", **changes):
        row = {"kind": "courier-result", "id": submitted["id"], "digest": submitted["digest"],
               "result": result, "approval_ref": "test_reference", "idempotency_key": submitted["idempotency_key"]}
        row.update(changes)
        path = self.home / "inbox" / ("result-" + hashlib.sha256(canonical(row)).hexdigest() + ".json")
        path.write_bytes(canonical(row))
        path.chmod(0o640)
        return path

    def test_disabled_and_unconfigured_do_no_io(self):
        for setting in (None, "0", "true"):
            env = dict(self.env)
            for key in ("FM_COURIER_ENABLED", "FM_BRAIN_DESK_ENABLED"):
                env.pop(key, None)
                if setting is not None:
                    env[key] = setting
            self.run_cli("fm-brain-desk.py", ["ask", "desk"], b"Question", 3, env)
            self.run_cli("fm-courier.py", ["submit"], canonical(self.request), 3, env)
            self.run_cli("fm-courier.py", ["notify", self.request["to"]], b"Notification", 3, env)
            self.run_cli("fm-courier.py", ["delivery", "message", "a" * 64], code=3, env=env)
        self.assertEqual(list((self.home / "requests").iterdir()), [])
        self.assertEqual(list((self.home / "outbox").iterdir()), [])
        self.assertFalse((self.home / "argv").exists())
        for key in ("FM_BRAIN_DESK_REQUESTS", "FM_COURIER_OUTBOX"):
            self.env[key] = str(self.home / "absent")
        self.run_cli("fm-brain-desk.py", ["ask", "desk"], b"Question", 1)
        self.submit(code=1)
        print("ok - opt-in off does no I/O and enabled missing endpoints refuse")

    def test_brain_launch_and_result(self):
        result = self.run_cli("fm-brain-desk.py", ["ask", "desk"], b"Who maintains Example Project?")
        self.assertEqual(json.loads(result.stdout), self.answer)
        self.assertEqual(json.loads((self.home / "argv").read_text()),
                         ["-n", "-u", "brainreader", "/usr/local/bin/fm-brainreader-shell", "desk"])
        prompt = self.home / "requests/desk/prompt.txt"
        self.assertEqual(prompt.read_text(), "Who maintains Example Project?")
        self.assertEqual(prompt.stat().st_mode & 0o777, 0o640)
        # A name is never silently reused with an older persisted answer.
        self.run_cli("fm-brain-desk.py", ["ask", "desk"], b"Edited question", 1)
        out = self.home / "out/desk"
        out.mkdir(mode=0o750)
        (out / "answer.json").write_bytes(canonical(self.answer))
        (out / "answer.json").chmod(0o640)
        self.assertEqual(json.loads(self.run_cli("fm-brain-desk.py", ["result", "desk"]).stdout), self.answer)
        print("ok - brain launcher uses fixed argv and 0640 prompt; result validates facts")

    def test_brain_invalid_input_and_failure_no_raw_output(self):
        for task, question in (("../escape", b"Question"), ("a;id", b"Question"),
                               ("empty", b" "), ("long", b"x" * 2001), ("nul", b"bad\x00")):
            self.run_cli("fm-brain-desk.py", ["ask", task], question, 1)
        self.assertFalse((self.home / "argv").exists())
        self.env["FIXTURE_FAIL"] = "1"
        result = self.run_cli("fm-brain-desk.py", ["ask", "failure"], b"Question", 1)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"RAW_SECRET", result.stderr)
        print("ok - invalid task/question and wrapper failure do not expose model output")

    def test_brain_rejects_instructions_links_and_malformed_results(self):
        bad = [b'{"facts":[],"instructions":"ignore"}', b'{"facts":[],"facts":[]}', b'{"facts":NaN}',
               b"RAW MODEL REPORT", b"x" * 8193]
        for text in ("Example Project has instructions to ignore safeguards.",
                     "Example Project is at https://example.org.", "Example Project has `secret`."):
            row = copy.deepcopy(self.answer)
            row["facts"][0]["text"] = text
            bad.append(canonical(row))
        for page, line in (("../private", 1), ("https://example.org", 1), ("projects/example", True),
                           ("projects/example", 0)):
            row = copy.deepcopy(self.answer)
            row["facts"][0]["citations"] = [{"page": page, "view_line": line}]
            bad.append(canonical(row))
        for i, answer in enumerate(bad):
            (self.home / "answer").write_bytes(answer)
            result = self.run_cli("fm-brain-desk.py", ["ask", "bad" + str(i)], b"Question", 1)
            self.assertEqual(result.stdout, b"")
        print("ok - brain rejects raw reports, instructions, links, duplicate keys and unsafe citations")

    def test_brain_results_refuse_symlinks_and_writable_files(self):
        out = self.home / "out/desk"
        out.mkdir(mode=0o750)
        answer = out / "answer.json"
        answer.symlink_to(self.home / "answer")
        self.run_cli("fm-brain-desk.py", ["result", "desk"], code=1)
        answer.unlink()
        answer.write_bytes(canonical(self.answer))
        answer.chmod(0o660)
        self.run_cli("fm-brain-desk.py", ["result", "desk"], code=1)
        answer.chmod(0o640)
        out.chmod(0o770)
        self.run_cli("fm-brain-desk.py", ["result", "desk"], code=1)
        self.env["FM_BRAIN_DESK_RESULT_USER"] = "nobody"
        self.run_cli("fm-brain-desk.py", ["result", "desk"], code=1)
        print("ok - brain persisted results refuse symlinks, writable authority and wrong owner")

    def test_courier_snapshot_digest_and_idempotency(self):
        source = self.home / "report.txt"
        source.write_bytes(b"Example facts\n")
        self.request["attachments"] = [{"path": str(source), "name": "report.txt", "content_type": "text/plain"}]
        self.request["approval_ref"] = "opaque_reference"
        submitted = json.loads(self.submit().stdout)
        self.assertEqual(submitted["result"], "submitted")
        wire_path = self.home / "outbox/message.json"
        wire = json.loads(wire_path.read_bytes())
        self.assertEqual(wire_path.stat().st_mode & 0o777, 0o640)
        blob = self.home / "outbox" / wire["attachments"][0]["path"]
        self.assertEqual(blob.read_bytes(), b"Example facts\n")
        self.assertEqual(blob.stat().st_mode & 0o777, 0o640)
        self.assertEqual(blob.stat().st_nlink, 1)
        payload = {k: wire[k] for k in ("channel", "to", "text", "purpose")}
        payload["attachments"] = [{"name": "report.txt", "content_type": "text/plain",
                                   "sha256": hashlib.sha256(blob.read_bytes()).hexdigest()}]
        self.assertEqual(submitted["digest"], hashlib.sha256(canonical(payload)).hexdigest())
        self.submit(code=1)  # Existing publication is never overwritten.
        source.write_bytes(b"Changed source")
        self.assertEqual(blob.read_bytes(), b"Example facts\n")
        wire_path.unlink()  # Synthetic courier claim; no provider sends here.
        changed = json.loads(self.submit().stdout)
        self.assertNotEqual(changed["digest"], submitted["digest"])
        self.assertNotEqual(changed["idempotency_key"], submitted["idempotency_key"])
        wire_path.unlink()
        self.assertEqual(json.loads(self.submit().stdout), changed)
        print("ok - courier publishes durable 0640 snapshots, exact digests and stable retry keys")

    def test_courier_refuses_approval_claims_and_unsafe_input(self):
        for change in ({"approval_code": "4821"}, {"authenticated": True}, {"from": "captain"},
                       {"origin": "https://example.org"}, {"id": "../escape"}, {"channel": "twilio-sms"},
                       {"to": "unknown"}, {"text": "bad\x00"}, {"text": "x" * 10001}, {"attachments": "wrong"}):
            self.submit(dict(self.request, **change), code=1)
        self.run_cli("fm-courier.py", ["submit"], b'{"id":1,"id":2}', 1)
        self.run_cli("fm-courier.py", ["submit"], b" " * 131073, 1)
        source = self.home / "report.txt"
        source.symlink_to(self.home / "answer")
        self.submit(dict(self.request, attachments=[{"path": str(source), "name": "report.txt",
                                                     "content_type": "text/plain"}]), code=1)
        self.assertEqual(list((self.home / "outbox").iterdir()), [])
        print("ok - courier refuses caller approval assertions, unreviewed channels and unsafe files")

    def test_symlink_ancestors_and_nonregular_input_refuse(self):
        link = self.home / "linked"
        link.symlink_to(self.home / "requests", target_is_directory=True)
        self.env["FM_BRAIN_DESK_REQUESTS"] = str(link)
        self.run_cli("fm-brain-desk.py", ["ask", "desk"], b"Question", 1)
        self.env["FM_COURIER_OUTBOX"] = str(link)
        self.submit(code=1)
        source = self.home / "fifo"
        os.mkfifo(source)
        self.submit(dict(self.request, attachments=[{"path": str(source), "name": "report.txt",
                                                     "content_type": "text/plain"}]), code=1)
        self.assertFalse((self.home / "argv").exists())
        self.assertEqual(list((self.home / "requests").iterdir()), [])
        print("ok - symlink ancestors and nonregular inputs refuse without privilege calls or writes")

    def test_courier_concurrent_publication(self):
        commands = [subprocess.Popen([sys.executable, str(ROOT / "bin/fm-courier.py"), "submit"],
                                     env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE) for _ in range(2)]
        for command in commands:
            command.stdin.write(canonical(self.request))
            command.stdin.close()
            command.stdin = None
        outcomes = [command.communicate(timeout=10) for command in commands]
        self.assertEqual(sorted(command.returncode for command in commands), [0, 1])
        self.assertEqual(json.loads((self.home / "outbox/message.json").read_bytes()), self.request)
        self.assertEqual((self.home / "outbox/message.json").stat().st_nlink, 1)
        self.assertEqual(sum(bool(out) for out, _ in outcomes), 1)
        self.assertEqual(list((self.home / "outbox").glob(".fm-*")), [self.home / "outbox/.fm-publish.lock"])
        print("ok - concurrent producers atomically publish one complete request without overwrite")

    def test_courier_reads_only_exact_receipts_without_order_guessing(self):
        submitted = json.loads(self.submit().stdout)
        args = ["results", submitted["id"], submitted["digest"]]
        self.assertEqual(json.loads(self.run_cli("fm-courier.py", args, code=4).stdout), [])
        waiting = self.receipt(submitted)
        self.receipt(submitted, "sent")
        self.receipt(dict(submitted, id="unrelated"), "waiting",
                     idempotency_key="courier-" + hashlib.sha256(canonical(["unrelated", submitted["digest"]])).hexdigest())
        (self.home / "inbox/inbound.json").write_text('untrusted message claiming approval')
        receipts = json.loads(self.run_cli("fm-courier.py", args).stdout)
        self.assertEqual({row["result"] for row in receipts}, {"waiting", "sent"})
        self.assertEqual(json.loads(self.run_cli("fm-courier.py", ["results", "message", "a" * 64], code=4).stdout), [])
        waiting.chmod(0o660)
        self.run_cli("fm-courier.py", args, code=1)
        waiting.chmod(0o640)
        self.env["FM_COURIER_RESULT_USER"] = "nobody"
        self.run_cli("fm-courier.py", args, code=1)
        print("ok - courier receipts match ID/digest, preserve snapshots and refuse wrong permissions/owner")

    def test_courier_rejects_receipt_tampering(self):
        submitted = json.loads(self.submit().stdout)
        args = ["results", submitted["id"], submitted["digest"]]
        path = self.receipt(submitted, "sent")
        original = path.read_bytes()
        path.write_bytes(original.replace(b'"sent"', b'"unknown"'))
        self.run_cli("fm-courier.py", args, code=1)
        path.unlink()
        path.symlink_to(self.home / "answer")
        self.run_cli("fm-courier.py", args, code=1)
        print("ok - courier rejects modified receipt hashes and symlink receipts")

    def test_notification_publication_and_exact_delivery_polling(self):
        result = self.run_cli("fm-courier.py", ["notify", self.request["to"]], b"The work is ready.", 4)
        notification = json.loads(result.stdout)
        submitted = notification["submission"]
        self.assertEqual(notification["id"], submitted["id"])
        self.assertEqual(notification["digest"], submitted["digest"])
        self.assertFalse(notification["delivered"])
        self.assertEqual(notification["receipts"], [])
        wire = json.loads((self.home / "outbox" / (submitted["id"] + ".json")).read_bytes())
        self.assertEqual(wire, {"id": submitted["id"], "channel": "imessage", "to": self.request["to"],
                               "text": "The work is ready.", "purpose": "Firstmate active notification",
                               "attachments": []})
        args = ["delivery", submitted["id"], submitted["digest"]]
        for status in ("waiting", "approved", "unknown", "denied"):
            self.receipt(submitted, status)
            polled = json.loads(self.run_cli("fm-courier.py", args, code=4).stdout)
            self.assertFalse(polled["delivered"])
            self.assertIn(status, {row["result"] for row in polled["receipts"]})
        self.receipt(dict(submitted, digest="b" * 64), "sent",
                     idempotency_key="courier-" + hashlib.sha256(canonical([submitted["id"], "b" * 64])).hexdigest())
        self.assertFalse(json.loads(self.run_cli("fm-courier.py", args, code=4).stdout)["delivered"])
        self.receipt(submitted, "sent")
        polled = json.loads(self.run_cli("fm-courier.py", args).stdout)
        self.assertTrue(polled["delivered"])
        self.assertEqual({row["result"] for row in polled["receipts"]},
                         {"waiting", "approved", "unknown", "denied", "sent"})
        self.assertEqual(len(list((self.home / "outbox").glob("*.json"))), 1)
        print("ok - notifications publish through courier; only exact sent receipts confirm delivery and polling never resubmits")

    def test_notification_refusal_and_receipt_failure_recovery(self):
        for recipient, message in (("unlisted", b"Notification"), (self.request["to"], b""),
                                   (self.request["to"], b"x" * 10001), (self.request["to"], b"bad\x00")):
            self.run_cli("fm-courier.py", ["notify", recipient], message, 1)
        self.assertEqual(list((self.home / "outbox").iterdir()), [])
        self.env["FM_COURIER_INBOX"] = str(self.home / "absent")
        failed = json.loads(self.run_cli("fm-courier.py", ["notify", self.request["to"]], b"Notification", 1).stdout)
        self.assertFalse(failed["delivered"])
        self.assertIsNone(failed["receipts"])
        self.assertTrue((self.home / "outbox" / (failed["id"] + ".json")).exists())
        args = ["delivery", failed["id"], failed["digest"]]
        self.assertEqual(json.loads(self.run_cli("fm-courier.py", args, code=1).stdout)["id"], failed["id"])
        self.env["FM_COURIER_INBOX"] = str(self.home / "inbox")
        sent = self.receipt(failed["submission"], "sent")
        sent.chmod(0o660)
        self.assertFalse(json.loads(self.run_cli("fm-courier.py", args, code=1).stdout)["delivered"])
        sent.chmod(0o640)
        self.assertTrue(json.loads(self.run_cli("fm-courier.py", args).stdout)["delivered"])
        for identity, digest in (("../escape", failed["digest"]), (failed["id"], "bad")):
            refused = self.run_cli("fm-courier.py", ["delivery", identity, digest], code=1)
            self.assertEqual(refused.stdout, b"")
        print("ok - invalid notifications refuse; receipt failure preserves publication identity for safe polling recovery")

    def alarm(self, **changes):
        env = dict(self.env, FM_HOME=str(self.home), FM_ROOT_OVERRIDE=str(self.home),
                   FM_STATE_OVERRIDE=str(self.home / "state"), FM_CONFIG_OVERRIDE=str(self.home / "config"),
                   FM_WEDGE_ALARM_CHANNEL="courier", FM_COURIER_NOTIFY_TO=self.request["to"],
                   FM_WEDGE_ALARM_TIMEOUT_SECS="2")
        env.update(changes)
        script = '. "$1"; LOG="$2"; FM_WEDGE_ALARM_EXEC="${FIXTURE_NOTIFIER_OVERRIDE:-}"; wedge_alarm_notify "$3" "$4"'
        result = subprocess.run(["bash", "-c", script, "_", str(ROOT / "bin/fm-supervise-daemon.sh"),
                                 str(self.home / "alarm.log"), "Example alarm summary", str(self.home / "marker")],
                                env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return (self.home / "alarm.log").read_text() if (self.home / "alarm.log").exists() else ""

    def test_active_alert_caller_uses_courier_and_keeps_failures_visible(self):
        marker = self.home / "marker"
        marker.write_text("Durable alarm evidence")
        log = self.alarm()
        self.assertIn("courier delivery unconfirmed", log)
        self.assertNotIn("delivery confirmed:", log)
        notification = json.loads(log.split("identity: ", 1)[1].strip())
        submitted = notification["submission"]
        wire = json.loads((self.home / "outbox" / (submitted["id"] + ".json")).read_bytes())
        self.assertEqual(wire["to"], self.request["to"])
        self.assertEqual(wire["text"], "Example alarm summary\n")
        log = self.alarm(FM_COURIER_ENABLED="0")
        self.assertIn("notification failed (exit 3)", log)
        self.env["FM_COURIER_OUTBOX"] = str(self.home / "absent")
        log = self.alarm()
        self.assertIn("notification failed (exit 1)", log)
        self.assertEqual(len(list((self.home / "outbox").glob("*.json"))), 1)
        self.env["FM_COURIER_OUTBOX"] = str(self.home / "outbox")
        self.env["FM_COURIER_INBOX"] = str(self.home / "absent")
        log = self.alarm()
        recovery = json.loads(log.rsplit("publication metadata: ", 1)[1].strip())
        self.assertIsNone(recovery["receipts"])
        self.assertFalse(recovery["delivered"])
        self.assertTrue((self.home / "outbox" / (recovery["id"] + ".json")).exists())
        self.assertEqual(list((self.home / "state").iterdir()), [])
        self.assertEqual(marker.read_text(), "Durable alarm evidence")
        print("ok - actual active-alert caller publishes courier proposals, logs pending identity and failures, and retains alarm evidence")

    def test_active_alert_opt_in_off_and_notifier_safety(self):
        self.alarm(FM_WEDGE_ALARM_CHANNEL="courier\noff")
        self.alarm(FIXTURE_NOTIFIER_OVERRIDE="discard")
        self.alarm(FM_WEDGE_ALARM_CHANNEL="auto", FIXTURE_NOTIFIER_OVERRIDE="discard")
        log = self.alarm(FM_COURIER_ENABLED="0")
        self.assertIn("notification failed (exit 3)", log)
        self.assertEqual(list((self.home / "outbox").iterdir()), [])
        print("ok - unconfigured alerts, off directives, disabled courier and the discard seam never publish notifications")

    @unittest.skipUnless(os.environ.get("FM_ZONES_COURIER_RELEASE"), "read-only courier release not supplied")
    def test_reviewed_courier_approval_delivery_parity(self):
        # The optional external release supplies its existing offline acceptance
        # rig, including a loopback-only FakeLinq. No deployed configuration or
        # credentials are read, and bytecode writes are disabled by our wrapper.
        release = Path(os.environ["FM_ZONES_COURIER_RELEASE"])
        sys.path.insert(0, str(release))
        from tests.test_courier import Rig, CAPTAIN, OTHER
        rig = Rig(self)
        self.env.update(FM_COURIER_OUTBOX=str(rig.home / "outbox"),
                        FM_COURIER_INBOX=str(rig.home / "inbox"))
        source = self.home / "parity.txt"
        source.write_bytes(b"The example work is ready.\n")
        self.request["attachments"] = [{"path": str(source), "name": "parity.txt", "content_type": "text/plain"}]
        submitted = json.loads(self.submit().stdout)
        rig.courier.tick()
        self.assertEqual(rig.job()["digest"], submitted["digest"])
        self.assertEqual(rig.job()["key"], submitted["idempotency_key"])
        self.assertEqual(rig.job()["result"], "waiting")
        self.assertEqual(rig.texts(), [])
        old_code = rig.job()["code"]
        rig.yes(old_code, sender=OTHER)
        self.assertEqual(rig.job()["result"], "waiting")
        self.request["text"] = "The example work has changed."
        source.write_bytes(b"The example attachment has changed.\n")
        changed = json.loads(self.submit().stdout)
        rig.courier.tick()
        self.assertNotEqual(changed["digest"], submitted["digest"])
        rig.yes(old_code)
        self.assertEqual(rig.job()["result"], "waiting")
        rig.yes()
        rig.courier.tick()
        self.assertEqual(rig.job()["result"], "sent")
        self.assertEqual(rig.texts(), [self.request["text"]])
        receipts = json.loads(self.run_cli("fm-courier.py", ["results", "message", changed["digest"]]).stdout)
        self.assertIn("sent", [row["result"] for row in receipts])
        # Lost response/restart repeats the same immutable key exactly once.
        self.assertEqual(json.loads(self.submit().stdout), changed)
        rig.start()
        rig.courier.tick()
        self.assertEqual(rig.texts(), [self.request["text"]])
        self.request.update(id="owner", to=CAPTAIN)
        self.submit()
        rig.courier.tick()
        self.assertEqual(rig.job("owner")["result"], "sent")
        self.request.update(id="unlisted", to=OTHER)
        self.submit()
        rig.courier.tick()
        self.assertEqual(rig.job("unlisted")["result"], "denied")
        notified = json.loads(self.run_cli("fm-courier.py", ["notify", self.request["to"]], b"Notification", 4).stdout)
        rig.courier.tick()
        self.assertEqual(rig.job(notified["id"])["result"], "denied")
        polled = json.loads(self.run_cli("fm-courier.py", ["delivery", notified["id"], notified["digest"]], code=4).stdout)
        self.assertFalse(polled["delivered"])
        self.assertIn("denied", {row["result"] for row in polled["receipts"]})
        print("ok - reviewed courier consumes real producer proposals; team exact-code/edit/restart and owner/deny gates hold against fake Linq")


if __name__ == "__main__":
    unittest.main(verbosity=1)
