#!/usr/bin/env python3
"""Firstmate's opt-in client for the brain rooms.

Usage: fm-brain-desk.py ask TASK < question.txt
       fm-brain-desk.py questions | nudges
       fm-brain-desk.py answer ID < answer.txt
       fm-brain-desk.py loop-add < loop.txt
       fm-brain-desk.py journal-export
Opt in with FM_BRAIN_DESK_ENABLED=1; absent/off exits 3 without I/O, so a
caller keeps its existing direct brain path until the rooms are activated.

ask publishes the question (stdin, 1-2000 characters, once per task name) as a
0640 prompt.txt under FM_BRAIN_DESK_REQUESTS (default
/srv/brain/brainreader/requests), then runs the fixed
`sudo -n -u brainreader /usr/local/bin/fm-brainreader-shell TASK`; nothing
selects a shell, model or executable. Stdout is {"facts":[...],"withheld":N}.
Each fact is one cited line that must pass the receiver's language check; a
failing fact is withheld without discarding the others, and a structurally
malformed result refuses whole.

questions, nudges, answer and loop-add run the fixed brainctl verbs
`questions digest`, `loops nudges`, `answer ID --text-file -` and
`loops add --text-file -` through `sudo -n -u brain /usr/local/bin/brainctl`,
passing stdin text through; brainctl owns their JSON, which is re-emitted
canonically with brainctl's exit status.

journal-export copies data/backlog.md, data/done-archive.md, data/captain.md,
data/learnings.md and every data/*/report.md, including remote second mates',
from FM_HOME into FM_BRAIN_JOURNAL_INPUT (default /srv/brain/journal-input) as
atomically replaced 0640 files that keep the source mtime the journal dates
reports by. Nothing else from the home is copied.

FM_BRAIN_DESK_TIMEOUT bounds each privileged call (default 210 seconds); on
expiry it receives SIGTERM, which sudo relays to the room, then SIGKILL 5
seconds later. Paths are opened without following symlinks. Refusals print a
fixed, content-free error and exit 1; usage exits 2.
"""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import subprocess
import sys
import time

TASK = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
ITEM = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_-]{0,79}\Z")
PAGE = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\Z")
# The receiver's conservative language check, matching brain-desk's candidate
# language: a schema-correct instruction still must not enter context.
UNSAFE = re.compile(r"https?://|www\.|\b(?:you|your|ignore|disregard|instructions?|prompt|system|assistant|"
                    r"execute|run|sudo|curl|wget|send|delete|click|visit|download|upload|install|transfer|reveal|copy|forward|"
                    r"must|should|please|shall|need to|do not|remember to|drop|publish|revoke|print|exfiltrate|disclose|email|notify|write|command|shell|ssh|chmod|chown|forget|tell|give)\b|[<>`{}\\;!?]|\[|\]", re.I)
FACT = re.compile(r"^[A-Z][\w ,.'’()-]{0,100}\b(?:is|are|was|were|has|have|had|owns|leads|works|"
                  r"joined|started|ended|shipped|costs|uses|lives|reports|approved|decided)\b")
CONTROL = {"questions": ["questions", "digest"], "nudges": ["loops", "nudges"],
           "answer": ["answer", None, "--text-file", "-"], "loop-add": ["loops", "add", "--text-file", "-"]}
JOURNAL = ("data/backlog.md", "data/done-archive.md", "data/captain.md", "data/learnings.md")


class Refused(ValueError):
    """An interface refusal whose input must never be printed."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def strict_json(data):
    def pairs(items):
        row = {}
        for key, value in items:
            if key in row:
                raise Refused()
            row[key] = value
        return row

    def invalid(_):
        raise Refused()

    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)


def fields(row, required):
    if not isinstance(row, dict) or set(row) != set(required):
        raise Refused()
    return row


def directory(path):
    """Open an absolute directory without following any symlinked component."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).absolute().parts[1:]:
            if part in (".", ".."):
                raise Refused()
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def subdirectory(parent, name):
    try:
        os.mkdir(name, 0o750, dir_fd=parent)
        created = True
    except FileExistsError:
        created = False
    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    if created:
        os.fchmod(child, 0o2750)  # The reading group traverses whatever the umask.
    return child


def readable_fact(text):
    return (10 <= len(text) <= 300 and FACT.search(text) and not UNSAFE.search(text)
            and not any(ord(c) < 32 for c in text))


def checked(data):
    row = fields(strict_json(data), {"facts"})
    if not isinstance(row["facts"], list) or len(row["facts"]) > 5 or len(data) > 1800:
        raise Refused()
    facts = []
    for fact in row["facts"]:
        fields(fact, {"text", "citations"})
        citations = fact["citations"]
        if not isinstance(fact["text"], str) or not isinstance(citations, list) or len(citations) != 1:
            raise Refused()
        citation = fields(citations[0], {"page", "view_line"})
        if (not isinstance(citation["page"], str) or len(citation["page"]) > 120
                or not PAGE.fullmatch(citation["page"]) or type(citation["view_line"]) is not int
                or citation["view_line"] < 1):
            raise Refused()
        if readable_fact(fact["text"]):
            facts.append(fact)
    return {"facts": facts, "withheld": len(row["facts"]) - len(facts)}


def run(argv, stdin, limit):
    """Run a fixed privileged argv under one deadline; return (status, stdout)."""
    timeout = float(os.environ.get("FM_BRAIN_DESK_TIMEOUT", "210"))
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    data = bytearray()
    try:
        process.stdin.write(stdin)
        process.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise Refused()
                chunk = os.read(process.stdout.fileno(), limit + 1 - len(data))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > limit:
                    raise Refused()
        return process.wait(timeout=max(0.01, deadline - time.monotonic())), bytes(data)
    finally:
        if process.poll() is None:
            # sudo cannot relay SIGKILL, which would orphan the room's child.
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait()
        process.stdout.close()


def ask(task):
    question = sys.stdin.buffer.read(8001).decode("utf-8")
    if not question.strip() or len(question) > 2000 or "\x00" in question:
        raise Refused()
    parent = directory(os.environ.get("FM_BRAIN_DESK_REQUESTS", "/srv/brain/brainreader/requests"))
    try:
        # Exclusive creation is the no-overwrite guarantee: a task name is used once.
        os.mkdir(task, mode=0o750, dir_fd=parent)
        child = os.open(task, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            os.fchmod(child, 0o2750)
            prompt = os.open("prompt.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o640, dir_fd=child)
            with os.fdopen(prompt, "wb") as handle:
                os.fchmod(handle.fileno(), 0o640)
                handle.write(question.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.fsync(child)
        finally:
            os.close(child)
        os.fsync(parent)
    finally:
        os.close(parent)
    status, data = run(["sudo", "-n", "-u", "brainreader", "/usr/local/bin/fm-brainreader-shell", task],
                       b"", 8192)
    if status:
        raise Refused()
    return checked(data), 0


def control(operation, item):
    argv = [item if part is None else part for part in CONTROL[operation]]
    if operation == "answer" and not (item and ITEM.fullmatch(item)):
        raise Refused()
    text = b""
    if "--text-file" in argv:
        text = sys.stdin.buffer.read(8001)
        if not text.strip() or len(text) > 8000 or b"\x00" in text:
            raise Refused()
    status, data = run(["sudo", "-n", "-u", "brain", "/usr/local/bin/brainctl", *argv], text, 65536)
    return strict_json(data), status


def journal_export():
    home = Path(os.environ.get("FM_HOME") or Path(__file__).resolve().parent.parent)
    names = [name for name in JOURNAL if (home / name).is_file()]
    for pattern in ("data/*/report.md", "data/remote-secondmates/*/data/*/report.md"):
        names += sorted(path.relative_to(home).as_posix() for path in home.glob(pattern))
    root = directory(os.environ.get("FM_BRAIN_JOURNAL_INPUT", "/srv/brain/journal-input"))
    copied = 0
    try:
        for name in names:
            source = os.open(home / name, os.O_RDONLY | os.O_NOFOLLOW)
            parent = root
            try:
                for part in name.split("/")[:-1]:
                    child = subdirectory(parent, part)
                    if parent != root:
                        os.close(parent)
                    parent = child
                info = os.fstat(source)
                base = name.rsplit("/", 1)[-1]
                try:
                    current = os.stat(base, dir_fd=parent, follow_symlinks=False)
                    if (current.st_size, current.st_mtime_ns) == (info.st_size, info.st_mtime_ns):
                        continue
                except FileNotFoundError:
                    pass
                temporary = ".fm-" + secrets.token_hex(16)
                target = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o640, dir_fd=parent)
                try:
                    with os.fdopen(target, "wb") as handle:
                        os.fchmod(handle.fileno(), 0o640)
                        while chunk := os.read(source, 65536):
                            handle.write(chunk)
                        handle.flush()
                        os.utime(handle.fileno(), ns=(info.st_atime_ns, info.st_mtime_ns))
                        os.fsync(handle.fileno())
                    os.rename(temporary, base, src_dir_fd=parent, dst_dir_fd=parent)
                    os.fsync(parent)
                    copied += 1
                finally:
                    try:
                        os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError:
                        pass
            finally:
                os.close(source)
                if parent != root:
                    os.close(parent)
    finally:
        os.close(root)
    return {"files": len(names), "copied": copied}, 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("operation", choices=("ask", "journal-export", *CONTROL))
    parser.add_argument("name", nargs="?", help="TASK for ask, ID for answer")
    args = parser.parse_args()
    if (args.name is None) != (args.operation not in ("ask", "answer")):
        parser.error("ask needs TASK, answer needs ID, other operations take no argument")
    if os.environ.get("FM_BRAIN_DESK_ENABLED") != "1":
        print("brain desk unavailable (opt-in required)", file=sys.stderr)
        return 3
    try:
        if args.operation == "ask":
            if not TASK.fullmatch(args.name):
                raise Refused()
            row, status = ask(args.name)
        elif args.operation == "journal-export":
            row, status = journal_export()
        else:
            row, status = control(args.operation, args.name)
        sys.stdout.buffer.write(canonical(row) + b"\n")
        return status
    except (OSError, ValueError, KeyError, RecursionError, UnicodeError, subprocess.TimeoutExpired):
        print("brain desk refused request or result", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
