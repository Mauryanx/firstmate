import fcntl
import os
from pathlib import Path
import pwd
import subprocess
import sys
import time
root = Path.cwd()
home = root / '.test-tmp/boundary/home'
courier = root / '.test-tmp/boundary/courier'
(home / 'state/.lock').write_text(sys.argv[1] + '\n')
(courier / 'etc/courier/policy.toml').chmod(0o600)
env = {k:v for k,v in os.environ.items() if not k.startswith('FM_') and k != 'STATE'}
env.update(FM_HOME=str(home), FM_NOTIFY_COURIER='1', FM_COURIER_ROOT=str(courier),
           FM_COURIER_USER=pwd.getpwuid(os.getuid()).pw_name, PYTHONDONTWRITEBYTECODE='1',
           TMPDIR=str(root / '.test-tmp/boundary/tmp'))
cli = [sys.executable, str(root / 'bin/fm-courier-pickup.py')]
running = subprocess.Popen(cli + ['run'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    until = time.monotonic() + 5
    with (home / 'state/courier-pickup/lock').open() as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
            except BlockingIOError:
                break
            assert time.monotonic() < until, 'running pickup did not acquire its lock'
            time.sleep(0.01)
    second = subprocess.run(cli + ['once'], env=env, capture_output=True, text=True, timeout=30)
    print('SECOND PICKUP EXIT:', second.returncode)
    print('SECOND PICKUP STDERR:', second.stderr.strip())
    assert second.returncode == 1 and 'another pickup holds this home' in second.stderr
    print('OBSERVED: a concurrent once process is refused while the real run process holds the home lock.')
finally:
    running.terminate()
    stdout, stderr = running.communicate(timeout=10)
    print('RUN PROCESS STDERR:', stderr.strip())
