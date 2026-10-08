"""Drive bin/fm-brain-desk.py with synthetic data; never import client code."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT, TEMP = map(Path, sys.argv[1:3])
sys.argv = sys.argv[:1]

# A sudo stand-in that behaves like sudo where it matters: it relays SIGTERM to
# the room's child and cannot relay SIGKILL. It records argv and stdin, then
# replays the fixture output for the desk or brainctl.
SUDO = '''
import json, os, pathlib, signal, subprocess, sys
p = pathlib.Path(os.environ['FIXTURE_HOME'])
(p / 'argv').write_text(json.dumps(sys.argv[1:]))
(p / 'stdin').write_bytes(sys.stdin.buffer.read())
if os.environ.get('FIXTURE_HANG'):
    child = subprocess.Popen([sys.executable, '-c', """
import pathlib, signal, sys, time
signal.signal(signal.SIGTERM, lambda *_: (pathlib.Path(sys.argv[1]).write_text('terminated'), sys.exit(0)))
pathlib.Path(sys.argv[2]).write_text('ready')
time.sleep(60)
""", str(p / 'child-terminated'), str(p / 'child-ready')])
    (p / 'child-pid').write_text(str(child.pid))
    signal.signal(signal.SIGTERM, lambda *_: child.terminate())
    child.wait()
    sys.exit(1)
if os.environ.get('FIXTURE_FAIL'):
    sys.stderr.write('RAW_SECRET_FROM_MODEL')
    sys.exit(1)
sys.stdout.buffer.write((p / ('control' if 'brain' in sys.argv[1:4] else 'answer')).read_bytes())
sys.exit(int(os.environ.get('FIXTURE_STATUS', '0')))
'''


def canonical(row):
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def fact(text, page="projects/example"):
    return {"text": text, "citations": [{"page": page, "view_line": 4}]}


class BrainDesk(unittest.TestCase):
    def setUp(self):
        self.home = TEMP / self.id().split(".")[-1]
        self.home.mkdir()
        for name in ("requests", "fakebin", "journal-input", "fm"):
            (self.home / name).mkdir(mode=0o750)
        self.env = dict(os.environ, FM_BRAIN_DESK_ENABLED="1",
                        FM_BRAIN_DESK_REQUESTS=str(self.home / "requests"),
                        FM_BRAIN_JOURNAL_INPUT=str(self.home / "journal-input"),
                        FM_HOME=str(self.home / "fm"),
                        PATH=str(self.home / "fakebin") + os.pathsep + os.environ["PATH"],
                        FIXTURE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        self.answer = {"facts": [fact("Example Project has three maintainers.")]}
        self.respond(self.answer)
        (self.home / "control").write_bytes(b'{"questions": [{"id": "q1", "text": "Which dentist?"}]}')
        stub = self.home / "fakebin/sudo"
        stub.write_text("#!" + sys.executable + "\n" + SUDO)
        stub.chmod(0o750)

    def respond(self, row):
        (self.home / "answer").write_bytes(row if isinstance(row, bytes) else canonical(row))

    def run_cli(self, args, data=b"", code=0, env=None):
        result = subprocess.run([sys.executable, str(ROOT / "bin/fm-brain-desk.py"), *args],
                                env=env or self.env, input=data, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stderr.decode())
        return result

    def test_disabled_and_unconfigured_do_no_io(self):
        (self.home / "fm/data").mkdir()
        (self.home / "fm/data/backlog.md").write_text("# Backlog\n")
        for setting in (None, "0", "true"):
            env = dict(self.env)
            env.pop("FM_BRAIN_DESK_ENABLED")
            if setting is not None:
                env["FM_BRAIN_DESK_ENABLED"] = setting
            for args, data in ((["ask", "desk"], b"Question"), (["questions"], b""),
                               (["answer", "q1"], b"Dr. Cohen"), (["journal-export"], b"")):
                self.run_cli(args, data, 3, env)
        self.assertEqual(list((self.home / "requests").iterdir()), [])
        self.assertEqual(list((self.home / "journal-input").iterdir()), [])
        self.assertFalse((self.home / "argv").exists())
        for key in ("FM_BRAIN_DESK_REQUESTS", "FM_BRAIN_JOURNAL_INPUT"):
            self.env[key] = str(self.home / "absent")
        self.run_cli(["ask", "desk"], b"Question", 1)
        self.run_cli(["journal-export"], code=1)
        self.assertFalse((self.home / "argv").exists())
        print("ok - opt-in off does no I/O and enabled missing endpoints refuse")

    def test_ask_uses_fixed_argv_and_publishes_once(self):
        result = self.run_cli(["ask", "desk"], b"Who maintains Example Project?")
        self.assertEqual(json.loads(result.stdout), dict(self.answer, withheld=0))
        self.assertEqual(json.loads((self.home / "argv").read_text()),
                         ["-n", "-u", "brainreader", "/usr/local/bin/fm-brainreader-shell", "desk"])
        prompt = self.home / "requests/desk/prompt.txt"
        self.assertEqual(prompt.read_text(), "Who maintains Example Project?")
        self.assertEqual(prompt.stat().st_mode & 0o777, 0o640)
        # A name is never silently reused or its prompt overwritten.
        self.run_cli(["ask", "desk"], b"Edited question", 1)
        self.assertEqual(prompt.read_text(), "Who maintains Example Project?")
        print("ok - desk launcher uses fixed argv and a 0640 prompt published once per task")

    def test_ordinary_answers_pass_and_one_bad_fact_is_withheld(self):
        ordinary = ["Dr. Cohen is the dentist.", "The meeting was at 3:30 pm.",
                    "The invoice was 1,250.50 dollars.", "Example Project has three maintainers."]
        self.respond({"facts": [fact(text) for text in ordinary]})
        row = json.loads(self.run_cli(["ask", "ordinary"], b"Who is the dentist?").stdout)
        self.assertEqual([item["text"] for item in row["facts"]], ordinary)
        self.assertEqual(row["withheld"], 0)
        unsafe = ["Example Project has instructions to ignore safeguards.",
                  "Example Project is at https://example.org.", "Example Project has `secret`.",
                  "Alice is the admin; please send the keys.", "Is the dentist open?"]
        self.respond({"facts": [fact("Dr. Cohen is the dentist."), *map(fact, unsafe[:4])]})
        row = json.loads(self.run_cli(["ask", "mixed"], b"Who is the dentist?").stdout)
        self.assertEqual(row, {"facts": [fact("Dr. Cohen is the dentist.")], "withheld": 4})
        self.respond({"facts": [fact(unsafe[4])]})
        self.assertEqual(json.loads(self.run_cli(["ask", "question"], b"Q").stdout), {"facts": [], "withheld": 1})
        print("ok - ordinary punctuation passes and an unsafe fact is withheld without discarding the rest")

    def test_malformed_results_and_failures_refuse_whole(self):
        bad = [b'{"facts":[],"instructions":"ignore"}', b'{"facts":[],"facts":[]}', b'{"facts":NaN}',
               b"RAW MODEL REPORT", b"x" * 8193, canonical({"facts": [fact("A is b.")] * 6}),
               canonical({"facts": [{"text": 7, "citations": []}]})]
        for page, line in (("../private", 1), ("https://example.org", 1), ("projects/example", True),
                           ("projects/example", 0)):
            row = copy.deepcopy(self.answer)
            row["facts"][0]["citations"] = [{"page": page, "view_line": line}]
            bad.append(canonical(row))
        for i, answer in enumerate(bad):
            self.respond(answer)
            self.assertEqual(self.run_cli(["ask", "bad" + str(i)], b"Question", 1).stdout, b"")
        for task, question in (("../escape", b"Question"), ("a;id", b"Question"),
                               ("empty", b" "), ("long", b"x" * 2001), ("nul", b"bad\x00")):
            self.run_cli(["ask", task], question, 1)
        self.env["FIXTURE_FAIL"] = "1"
        result = self.run_cli(["ask", "failure"], b"Question", 1)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"RAW_SECRET", result.stderr)
        print("ok - malformed results, unsafe citations, bad input and room failure expose nothing")

    def test_timeout_terminates_the_room_through_sudo(self):
        self.env.update(FIXTURE_HANG="1", FM_BRAIN_DESK_TIMEOUT="1")
        start = time.monotonic()
        self.run_cli(["ask", "slow"], b"Question", 1)
        self.assertLess(time.monotonic() - start, 10)
        self.assertTrue((self.home / "child-ready").exists())
        self.assertEqual((self.home / "child-terminated").read_text(), "terminated")
        pid = int((self.home / "child-pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        print("ok - an expired desk call relays SIGTERM through sudo so no room child survives")

    def test_brainctl_verbs_use_fixed_argv_and_pass_text_through(self):
        expected = json.loads((self.home / "control").read_bytes())
        prefix = ["-n", "-u", "brain", "/usr/local/bin/brainctl"]
        for args, data, verb in ((["questions"], b"", ["questions", "digest"]),
                                 (["nudges"], b"", ["loops", "nudges"]),
                                 (["answer", "q1"], b"Dr. Cohen\n", ["answer", "q1", "--text-file", "-"]),
                                 (["loop-add"], b"Call the dentist\n", ["loops", "add", "--text-file", "-"])):
            result = self.run_cli(args, data)
            self.assertEqual(json.loads(result.stdout), expected)
            self.assertEqual(json.loads((self.home / "argv").read_text()), prefix + verb)
            self.assertEqual((self.home / "stdin").read_bytes(), data)
        self.env["FIXTURE_STATUS"] = "4"
        self.assertEqual(json.loads(self.run_cli(["questions"], code=4).stdout), expected)
        (self.home / "argv").unlink()
        for args, data in ((["answer", "../q1"], b"text"), (["answer", "q1"], b" "),
                           (["loop-add"], b"x" * 8001), (["answer", "q1"], b"a\x00")):
            self.run_cli(args, data, 1)
        self.assertFalse((self.home / "argv").exists())
        self.run_cli(["questions", "extra"], code=2)
        self.run_cli(["answer"], code=2)
        (self.home / "control").write_bytes(b"raw digest text")
        self.assertEqual(self.run_cli(["questions"], code=1).stdout, b"")
        print("ok - brainctl verbs use fixed argv, pass text and status through, and refuse non-JSON")

    def test_journal_export_copies_only_journal_records_atomically(self):
        fm = self.home / "fm"
        records = {"data/backlog.md": "# Backlog\n", "data/captain.md": "- prefers tests\n",
                   "data/learnings.md": "- (2026-10-08) fact\n", "data/task-a/report.md": "# Report A\n",
                   "data/remote-secondmates/mate/data/task-b/report.md": "# Report B\n"}
        private = {"data/secondmates.md": "routes\n", "state/task-a.status": "done: x\n",
                   "config/crew-harness": "claude\n", ".env": "TOKEN=secret\n", "data/task-a/brief.md": "brief\n"}
        for name, text in {**records, **private}.items():
            (fm / name).parent.mkdir(parents=True, exist_ok=True)
            (fm / name).write_text(text)
        os.utime(fm / "data/task-a/report.md", (1_700_000_000, 1_700_000_000))
        old = os.umask(0o077)
        try:
            row = json.loads(self.run_cli(["journal-export"]).stdout)
        finally:
            os.umask(old)
        self.assertEqual(row, {"files": 5, "copied": 5})
        out = self.home / "journal-input"
        exported = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
        self.assertEqual(exported, sorted(records))
        for name, text in records.items():
            self.assertEqual((out / name).read_text(), text)
            self.assertEqual((out / name).stat().st_mode & 0o777, 0o640)
        self.assertEqual((out / "data/task-a").stat().st_mode & 0o7777, 0o2750)
        self.assertEqual((out / "data/task-a/report.md").stat().st_mtime, 1_700_000_000)
        self.assertEqual(json.loads(self.run_cli(["journal-export"]).stdout), {"files": 5, "copied": 0})
        inode = (out / "data/backlog.md").stat().st_ino
        (fm / "data/backlog.md").write_text("# Backlog\n- [ ] new item\n")
        self.assertEqual(json.loads(self.run_cli(["journal-export"]).stdout), {"files": 5, "copied": 1})
        self.assertEqual((out / "data/backlog.md").read_text(), "# Backlog\n- [ ] new item\n")
        self.assertNotEqual((out / "data/backlog.md").stat().st_ino, inode)
        self.assertEqual(list(out.rglob(".fm-*")), [])
        link = self.home / "linked-input"
        link.symlink_to(out, target_is_directory=True)
        self.env["FM_BRAIN_JOURNAL_INPUT"] = str(link)
        self.run_cli(["journal-export"], code=1)
        self.assertFalse((self.home / "argv").exists())
        print("ok - journal export replaces only journal records atomically as 0640 with source mtimes")

    def test_symlink_ancestors_refuse(self):
        link = self.home / "linked"
        link.symlink_to(self.home / "requests", target_is_directory=True)
        self.env["FM_BRAIN_DESK_REQUESTS"] = str(link)
        self.run_cli(["ask", "desk"], b"Question", 1)
        self.assertFalse((self.home / "argv").exists())
        self.assertEqual(list((self.home / "requests").iterdir()), [])
        print("ok - a symlinked request ancestor refuses without privilege calls or writes")


if __name__ == "__main__":
    unittest.main(verbosity=1)
