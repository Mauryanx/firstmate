import configparser
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import time

root = Path.cwd()
base = root / '.test-phase-tmp' / 'manual'
evidence = Path('/home/firstmate/.no-mistakes/evidence/01M4FWK9GMKEGGQ4PYBSNRW5CQ')
home = base / 'home'
config = base / 'config'
for directory in (home, config, base / 'runtime', base / 'bin'):
    directory.mkdir(parents=True, exist_ok=True)
(base / 'runtime').chmod(0o700)
env = {key: value for key, value in os.environ.items() if not key.startswith('FM_')}
env.update(HOME=str(base), FM_HOME=str(home), XDG_CONFIG_HOME=str(config),
           XDG_CONFIG_DIRS=str(base / 'config-dirs'), XDG_DATA_HOME=str(base / 'data'),
           XDG_DATA_DIRS=str(base / 'data-dirs'), XDG_RUNTIME_DIR=str(base / 'runtime'),
           DBUS_SESSION_BUS_ADDRESS='unix:abstract=no-mistakes-private-no-bus', SYSTEMD_OFFLINE='1')
service = root / 'bin/fm-courier-pickup-service.sh'
unit = config / 'systemd/user/fm-courier-pickup.service'
systemctl = shutil.which('systemctl')

def run(command, use_env=None, code=0):
    result = subprocess.run([str(item) for item in command], env=use_env or env,
                            capture_output=True, text=True, timeout=30)
    print('$ ' + ' '.join(str(item) for item in command), flush=True)
    print(result.stdout + result.stderr, end='', flush=True)
    print('exit=' + str(result.returncode), flush=True)
    assert result.returncode == code, result
    return result

# Audit only the service-control boundary; execute the real installer.
shim = base / 'bin/systemctl'
marker = base / 'systemctl-called'
shim.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "' + str(marker) + '"\nexit 99\n')
shim.chmod(0o755)
install_env = dict(env, PATH=str(shim.parent) + ':' + env['PATH'])
run([service, 'install'], install_env)
assert not marker.exists(), 'install attempted service activation'
assert stat.S_IMODE(unit.stat().st_mode) == 0o644
assert not (home / 'state/courier-pickup').exists()
assert not list(config.rglob('*.wants'))
print('Installed mode=0644; no service-control calls, no target enablement, no pickup state.', flush=True)
print(unit.read_text(), flush=True)
(evidence / 'installed-courier-pickup.service').write_bytes(unit.read_bytes())
run(['systemd-analyze', 'verify', unit])

# Use the real consumer offline: the missing private bus prevents touching the
# operator manager, and --no-reload makes enable/disable file-only operations.
run([systemctl, '--user', '--no-reload', 'enable', unit])
link = config / 'systemd/user/default.target.wants/fm-courier-pickup.service'
assert link.is_symlink() and link.resolve() == unit
print('Persistent default.target link: ' + str(link) + ' -> ' + os.readlink(link), flush=True)
run([systemctl, '--user', '--no-reload', 'is-enabled', unit.name])
run([systemctl, '--user', '--no-reload', 'disable', unit.name])
assert not link.exists()

# Replacement is deterministic, supports a changed home, and refuses bad homes
# before touching a previously installed unit.
new_home = base / 'new-home'
new_home.mkdir()
run([service, 'install'], dict(install_env, FM_HOME=str(new_home)))
updated = unit.read_bytes()
parser = configparser.ConfigParser(interpolation=None)
# Environment repeats; the other scalar values are sufficient for this check.
parser.read_string('\n'.join(line for line in updated.decode().splitlines() if not line.startswith('Environment=')))
assert parser['Service']['WorkingDirectory'] == str(new_home)
for bad in ('relative/home', str(base / 'has space'), str(base / 'missing'), str(base / 'bad%specifier')):
    if 'has space' in bad or '%specifier' in bad:
        Path(bad).mkdir(exist_ok=True)
    run([service, 'install'], dict(install_env, FM_HOME=bad), code=1)
    assert unit.read_bytes() == updated
assert not list(unit.parent.glob('.fm-courier-pickup.service.*'))
run([service, 'install'], install_env)
print('Reinstall selected the new home; invalid paths preserved the installed unit; no temporary unit files remain.', flush=True)

# Drive the effective installed command continuously through real inbox and
# pickup entry points, as the fixture shell keeps the public session lock alive.
settings = {}
for line in unit.read_text().splitlines():
    if line.startswith('Environment='):
        key, value = line[len('Environment='):].split('=', 1)
        settings[key] = value
    elif line.startswith('ExecStart='):
        command = line[len('ExecStart='):].split()
    elif line.startswith('WorkingDirectory='):
        cwd = line[len('WorkingDirectory='):]
courier = base / 'courier'
settings.update(FM_COURIER_ROOT=str(courier), FM_COURIER_USER=pwd.getpwuid(os.getuid()).pw_name)
pickup_env = dict(env, **settings)
(home / 'state').mkdir(exist_ok=True)
(home / 'state/.lock').write_text(sys.argv[1] + '\n')
cli = root / 'bin/fm-inbox.sh'

def conversation(action, payload):
    result = subprocess.run([str(cli), 'conversation', action], input=json.dumps(payload),
                            env=pickup_env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result
    return json.loads(result.stdout)

conversation('pilot-init', {'publication_policy': 'owner-authored-v2', 'destinations': ['imessage']})
binding = conversation('bind', {'conversation_id': 'text', 'authenticated_principal': 'captain', 'destination': 'imessage'})
(home / 'state/imessage').mkdir(mode=0o700)
(home / 'state/imessage/binding.json').write_text(json.dumps(dict(binding, destination='imessage')))
for directory in ('srv/courier/inbound', 'srv/courier/outbox', 'srv/courier/inbox', 'etc/courier'):
    (courier / directory).mkdir(parents=True)
(courier / 'etc/courier/policy.toml').write_text('version = 1\ndefault = "deny"\nteam = []\n[captain]\nname = "Captain"\nto = "+12025550101"\nchannels = ["imessage"]\n')

def publish(seq):
    body = dict(kind='courier-inbound', version=1, seq=seq, type='message',
                published_at='2026-10-09T01:00:00Z', message_id='manual' + str(seq),
                chat_id='chat-1', created_at='2026-10-09T01:00:00Z',
                transcript='Message ' + str(seq) + ' from disposable courier spool', attachments=[], other_parts=0)
    path = courier / ('srv/courier/inbound/%012d-message-manual%d.json' % (seq, seq))
    path.write_text(json.dumps(body))
    path.chmod(0o640)
    return path.read_bytes()

def wait_notes(count, process):
    deadline = time.monotonic() + 10
    while len(list((home / 'state/inbox').glob('vc-*.note'))) != count:
        assert process.poll() is None, 'pickup exited'
        assert time.monotonic() < deadline, 'pickup did not file the spool record'
        time.sleep(0.05)

log = (evidence / 'effective-pickup-stderr.log').open('w')
process = None
try:
    print('Starting effective ExecStart with emitted Environment and WorkingDirectory: ' + ' '.join(command), flush=True)
    process = subprocess.Popen(command, env=pickup_env, cwd=cwd, stdout=log, stderr=log)
    first = publish(1)
    wait_notes(1, process)
    print('Running pickup created one conversation note and queued wake from message manual1.', flush=True)
    process.kill()
    process.wait(timeout=10)
    print('Killed pickup; explicitly relaunching its command to check cursor recovery (not systemd restart).', flush=True)
    process = subprocess.Popen(command, env=pickup_env, cwd=cwd, stdout=log, stderr=log)
    publish(2)
    wait_notes(2, process)
    time.sleep(0.3)
    requests = conversation('audit', {'conversation_id': 'text'})['requests']
    assert [row['turn_id'] for row in requests] == ['imsg-manual1', 'imsg-manual2'], requests
    assert (courier / 'srv/courier/inbound/000000000001-message-manual1.json').read_bytes() == first
    state = json.loads((home / 'state/courier-pickup/state.json').read_text())
    assert state['cursor'] == 2
    print('After explicit relaunch: turn IDs=' + json.dumps([row['turn_id'] for row in requests]) + '; cursor=2; original spool bytes unchanged.', flush=True)
    wakes = (home / 'state/.wake-queue').read_text()
    print('Queued wake records:\n' + wakes, flush=True)
    (evidence / 'pickup-recovered-state.json').write_text(json.dumps(state, indent=2))
finally:
    if process is not None and process.poll() is None:
        process.terminate()
        process.wait(timeout=10)
    log.close()
print('All disposable service and pickup checks completed.', flush=True)
