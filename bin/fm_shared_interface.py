#!/usr/bin/env python3
"""Copy staged, artifact-specific access onto opened shared-interface inodes.

An administrator may stage .fm-voice-shared-interface in a dedicated FM_HOME,
never the operational home. Its regular metadata templates are conversation
(0660 journal/flock), note (0640 immutable vc notes), wake (0660 queue, sequence
and recovery marker), and wake-lock (0770 dynamic queue/recovery owner dirs).
The directory and home must be non-peer-writable, owned by the home owner or
root; templates must have that ownership and exactly those modes. Template
contents are unused. Group and Linux access ACLs are copied from the opened
template to the opened new inode, before publication; no default ACL is needed.
Staging owns the reviewed group/named-peer grants, directory/mount restrictions
and read-only policy/session-lock exposure. Absence keeps private operation.
No other conversation/state/config/data artifact acquires shared access.

The dedicated state parent requires write+traverse for dynamic owner dirs,
lock/steal symlinks, queue/sequence creation and recovery temp/rename/removal;
listing is unnecessary (0300 for the writer suffices). Inbox, handled and
voice-conversation separately need list/read/traverse and create/flock/rename/
directory-fsync access. Protect policy and session-lock names from peer edits
with separately owned read-only mounts/directories; never expose the complete
operational state or use blanket default ACLs. Seed templates from the reviewed
group/access ACLs, including both intended writer identities on mutable files
and owner-read access on transport-created notes. Replacement reuses templates,
not incidental permissions on the old target. Templates must be protected from
peer metadata changes. Only the two reviewed lock families gain shared access.
This is writer-side support, not installation or OS/VM acceptance: verify the
distinct service identities, dedicated mounts, owner authority and private PID
namespace against the final zone/courier revisions before enabling it.
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
        self.home = Path(home)
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
        try:
            relative = Path(target).relative_to(self.home / 'state')
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
