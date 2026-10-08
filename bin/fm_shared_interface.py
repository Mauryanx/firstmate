#!/usr/bin/env python3
"""Copy staged, artifact-specific access onto opened shared-interface inodes.

Opt-in: an administrator stages the real directory .fm-voice-shared-interface
in a dedicated transport FM_HOME, never the operational home; absence keeps
every artifact private. Home and staging directory must be non-peer-writable
and owned by the home owner or root. Its regular templates, with that owner,
exactly these modes and unused contents, are: conversation (0660 journal and
flock), note (0640 vc notes), wake (0660 queue, sequence, recovery marker and
lock pid) and wake-lock (0770 queue/recovery lock owner dirs). The template's
group and Linux access ACL are copied to the opened new inode before it is
published; no other artifact gains shared access and no default ACL is used.
The dedicated state parent needs write+traverse only (0300); inbox, handled
and voice-conversation need list/read/traverse/create/flock/rename/fsync.
Staging owns the reviewed group and peer grants, mounts, and read-only
exposure of policy and session-lock names; protect templates from peers.
"""

import errno
import os
from pathlib import Path
import re
import stat
import sys


MODES = {'conversation': 0o660, 'note': 0o640, 'wake': 0o660, 'wake-lock': 0o770}
ACL = 'system.posix_acl_access'
NO_ACL = (errno.ENODATA, errno.ENOTSUP)


def access_acl(fd):
    if not hasattr(os, 'getxattr'):
        return None
    try:
        return os.getxattr(fd, ACL)
    except OSError as exc:
        if exc.errno not in NO_ACL:
            raise
        return None


class SharedInterface:
    def __init__(self, home):
        # Resolve once: a symlinked FM_HOME names the same protected directory.
        self.home = Path(home).resolve()
        self.root = self.home / '.fm-voice-shared-interface'
        self.enabled = self.root.exists() or self.root.is_symlink()
        if self.enabled:
            owner = self.home.lstat()
            staging = self.root.lstat()
            if (not stat.S_ISDIR(owner.st_mode) or owner.st_mode & 0o022
                    or not stat.S_ISDIR(staging.st_mode) or staging.st_mode & 0o022
                    or staging.st_uid not in (0, owner.st_uid)):
                raise ValueError('shared interface requires protected owner-staged home and templates')
            self.owner = owner.st_uid

    def apply(self, fd, artifact):
        if not self.enabled:
            return
        mode = MODES[artifact]
        source = os.open(self.root / artifact, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(source)
            if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != mode
                    or metadata.st_uid not in (0, self.owner)):
                raise ValueError('invalid shared-interface metadata template: ' + artifact)
            target = os.fstat(fd)
            acl = access_acl(source)
            if (target.st_gid == metadata.st_gid and stat.S_IMODE(target.st_mode) == mode
                    and access_acl(fd) == acl):
                # A correctly staged lock can belong to the other writer.
                # Reuse must not require chmod/chgrp authority over its inode.
                return
            # Apply to the descriptor, never chmod a published pathname. fchmod
            # alone would mask inherited named ACLs instead of preserving them.
            if os.fstat(fd).st_gid != metadata.st_gid:
                os.fchown(fd, -1, metadata.st_gid)
            os.fchmod(fd, mode)
            if hasattr(os, 'getxattr'):
                if acl is None:
                    try:
                        os.removexattr(fd, ACL)
                    except OSError as remove:
                        if remove.errno not in NO_ACL:
                            raise
                else:
                    os.setxattr(fd, ACL, acl)
        finally:
            os.close(source)

    def wake_artifact(self, target):
        target = Path(os.path.realpath(os.path.dirname(target)), os.path.basename(target))
        try:
            relative = target.relative_to(self.home / 'state')
        except ValueError:
            return None
        name = str(relative)
        if name in ('.wake-queue', '.wake-queue.seq', '.watcher-down'):
            return 'wake'
        if re.fullmatch(r'\.(?:wake-queue|watcher-down)\.lock(?:\.steal)?\.owner\.[A-Za-z0-9]+', name):
            return 'wake-lock'
        if re.fullmatch(r'\.(?:wake-queue|watcher-down)\.lock(?:\.steal)?\.owner\.[A-Za-z0-9]+/pid', name):
            return 'wake'
        return None


def main():
    if len(sys.argv) == 2 and sys.argv[1] == '--help':
        print(__doc__)
        return 0
    try:
        interface = SharedInterface(os.environ['FM_HOME'])
        if not interface.enabled:
            return 0
        artifact = interface.wake_artifact(sys.argv[1])
        if artifact is None:
            return 0
        # argv[1] is the logical publication path; argv[2] may be its temporary.
        fd = os.open(sys.argv[2], os.O_RDONLY | os.O_NOFOLLOW)
        try:
            interface.apply(fd, artifact)
            os.fsync(fd)
        finally:
            os.close(fd)
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print('fm shared interface: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
