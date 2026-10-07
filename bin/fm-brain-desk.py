#!/usr/bin/env python3
"""Launch the installed tool-less brain desk, or read its validated result.

Usage: fm-brain-desk.py ask TASK < question.txt
       fm-brain-desk.py result TASK
Opt in with FM_BRAIN_DESK_ENABLED=1; absent/off exits 3 without I/O.
FM_BRAIN_DESK_REQUESTS defaults to /srv/brain/brainreader/requests.
FM_BRAIN_DESK_OUT defaults to /srv/brain/brainreader/out.
FM_BRAIN_DESK_RESULT_USER defaults to brainreader (local fixture identity seam).
The launcher argv is fixed: sudo -n -u brainreader
/usr/local/bin/fm-brainreader-shell TASK. No shell, model, or executable flag.
Question is stdin only, 1-2000 characters; a task name can be launched once.
Stdout contains only validated facts JSON; stderr is fixed, content-free errors.
Ordinary workers continue to use fm-spawn/fm-send/fm-control unchanged.
"""

import argparse
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import subprocess
import sys
import time

from fm_zone_io import Refused, canonical, directory, fields, protected, publish, read_at, strict_json

TASK = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
PAGE = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\Z")
# This is the receiver's conservative language check, matching brain-desk's
# candidate language. A schema-correct instruction still must not enter context.
UNSAFE = re.compile(r"https?://|www\.|\b(?:you|your|ignore|disregard|instructions?|prompt|system|assistant|"
                    r"execute|run|sudo|curl|wget|send|delete|click|visit|download|upload|install|transfer|reveal|copy|forward|"
                    r"must|should|please|shall|need to|do not|remember to|drop|publish|revoke|print|exfiltrate|disclose|email|notify|write|command|shell|ssh|chmod|chown|forget|tell|give)\b|[<>`{}\\]|\[|\]", re.I)
FACT = re.compile(r"^[A-Z][\w ,.'’()-]{0,100}\b(?:is|are|was|were|has|have|had|owns|leads|works|"
                  r"joined|started|ended|shipped|costs|uses|lives|reports|approved|decided)\b")


def checked(data):
    row = fields(strict_json(data), {"facts"})
    if not isinstance(row["facts"], list) or len(row["facts"]) > 5:
        raise Refused()
    for fact in row["facts"]:
        fields(fact, {"text", "citations"})
        text = fact["text"]
        if (not isinstance(text, str) or not 10 <= len(text) <= 300 or not FACT.search(text)
                or UNSAFE.search(text) or any(ord(c) < 32 for c in text)
                or any(c in text for c in ":;!?") or "." in text.rstrip(".")):
            raise Refused()
        citations = fact["citations"]
        if not isinstance(citations, list) or len(citations) != 1:
            raise Refused()
        citation = fields(citations[0], {"page", "view_line"})
        if (not isinstance(citation["page"], str) or len(citation["page"]) > 120
                or not PAGE.fullmatch(citation["page"]) or type(citation["view_line"]) is not int
                or citation["view_line"] < 1):
            raise Refused()
    if len(json.dumps(row, ensure_ascii=False)) > 1800:
        raise Refused()
    return row


def launch(task):
    # Resolve sudo normally so portable tests can stub the privilege boundary.
    # The privileged executable and identity are never caller-selected.
    process = subprocess.Popen(["sudo", "-n", "-u", "brainreader",
                                "/usr/local/bin/fm-brainreader-shell", task],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    data = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 210
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise Refused()
                chunk = os.read(process.stdout.fileno(), 8193 - len(data))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > 8192:
                    raise Refused()
        if process.wait(timeout=max(0.01, deadline - time.monotonic())):
            raise Refused()
        return checked(bytes(data))
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def ask(task):
    question = sys.stdin.buffer.read(8001).decode("utf-8")
    if not question.strip() or len(question) > 2000 or "\x00" in question:
        raise Refused()
    parent = directory(os.environ.get("FM_BRAIN_DESK_REQUESTS", "/srv/brain/brainreader/requests"))
    try:
        os.mkdir(task, mode=0o750, dir_fd=parent)
        child = os.open(task, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            os.fchmod(child, 0o2750)
            publish(child, "prompt.txt", question.encode("utf-8"))
            os.fsync(parent)
        finally:
            os.close(child)
    finally:
        os.close(parent)
    return launch(task)


def result(task):
    uid = pwd.getpwnam(os.environ.get("FM_BRAIN_DESK_RESULT_USER", "brainreader")).pw_uid
    parent = directory(Path(os.environ.get("FM_BRAIN_DESK_OUT", "/srv/brain/brainreader/out")) / task)
    try:
        protected(parent, uid)
        return checked(read_at(parent, "answer.json", 8192, uid=uid, mode=0o640))
    finally:
        os.close(parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("ask", "result"))
    parser.add_argument("task")
    args = parser.parse_args()
    if os.environ.get("FM_BRAIN_DESK_ENABLED") != "1":
        print("brain desk unavailable (opt-in required)", file=sys.stderr)
        return 3
    try:
        if not TASK.fullmatch(args.task):
            raise Refused()
        row = ask(args.task) if args.operation == "ask" else result(args.task)
        sys.stdout.buffer.write(canonical(row) + b"\n")
        return 0
    except (OSError, ValueError, KeyError, RecursionError, UnicodeError, subprocess.TimeoutExpired):
        print("brain desk refused request or result", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
