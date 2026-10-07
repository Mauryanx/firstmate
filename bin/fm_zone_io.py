"""Descriptor I/O for the optional brain-desk and courier clients.

The clients handle untrusted boundary files, never credentials or approvals.
OS ownership/ACL enforcement is supplied by the separately installed zones.
"""

import fcntl
import json
import os
from pathlib import Path
import secrets
import stat


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


def fields(row, required, optional=()):
    if not isinstance(row, dict) or not set(required) <= set(row) <= set(required) | set(optional):
        raise Refused()
    return row


def directory(path):
    path = Path(path).absolute()
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if part in (".", ".."):
                raise Refused()
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_at(parent, name, limit, uid=None, mode=None):
    if not name or "/" in name or name in (".", ".."):
        raise Refused()
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit
                or (uid is not None and before.st_uid != uid)
                or (mode is not None and stat.S_IMODE(before.st_mode) != mode)):
            raise Refused()
        chunks = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise Refused()
        after = os.fstat(descriptor)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns) or size != after.st_size:
            raise Refused()
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def protected(parent, uid):
    info = os.fstat(parent)
    if info.st_uid != uid or stat.S_IMODE(info.st_mode) & 0o022:
        raise Refused()


def publish(parent, name, data):
    """Serialize client publications and rename only complete, fsynced files.

    Existing names refuse: request edits/retries must wait for courier to claim
    the old input. Courier still treats all producer-owned inputs as untrusted.
    """
    lock = os.open(".fm-publish.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                   0o600, dir_fd=parent)
    info = os.fstat(lock)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        os.close(lock)
        raise Refused()
    fcntl.flock(lock, fcntl.LOCK_EX)
    temporary = ".fm-" + secrets.token_hex(16)
    try:
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise Refused()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o640, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o640)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(lock)
