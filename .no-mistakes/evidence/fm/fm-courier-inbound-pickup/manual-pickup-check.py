"""Drive shipped CLIs; trace file operations against an isolated courier fixture."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import time

os.umask(0o077)
root, temp, evidence, owner = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
home, courier = temp / 'home', temp / 'courier'
env = {k: v for k, v in os.environ.items() if not k.startswith('FM_') and k != 'STATE'}
env.update(FM_HOME=str(home), FM_NOTIFY_COURIER='1', FM_COURIER_ROOT=str(courier),
           FM_COURIER_USER=pwd.getpwuid(os.getuid()).pw_name, PYTHONDONTWRITEBYTECODE='1')
cli, pickup = root / 'bin/fm-inbox.sh', root / 'bin/fm-courier-pickup.py'

def call(command, payload):
    r = subprocess.run([str(cli), 'conversation', command], input=json.dumps(payload),
                       text=True, capture_output=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)

def traced(label):
    path = evidence / ('pickup-' + label + '.strace')
    r = subprocess.run(['strace', '-f', '-qq', '-yy', '-e', 'trace=%file,%creds', '-o', str(path),
                        str(pickup), 'once'], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    print(label + ': ' + r.stderr.strip(), flush=True)
    trace = path.read_text()
    for forbidden in ('credential.json', 'ledger.json', '/srv/courier/inbox/media/fixture.txt'):
        assert forbidden not in trace, 'pickup accessed private courier data: ' + forbidden
    return path.name

def snapshot(paths):
    return {str(p.relative_to(courier)): {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
              'mode': oct(stat.S_IMODE(p.stat().st_mode)), 'uid': p.stat().st_uid,
              'inode': p.stat().st_ino, 'mtime_ns': p.stat().st_mtime_ns} for p in paths}

try:
    (home / 'state').mkdir(parents=True)
    (home / 'state/.lock').write_text(owner + '\n')
    call('pilot-init', {'publication_policy': 'owner-authored-v2', 'destinations': ['imessage']})
    binding = call('bind', {'conversation_id': 'text', 'authenticated_principal': 'captain', 'destination': 'imessage'})
    (home / 'state/imessage').mkdir(mode=0o700)
    (home / 'state/imessage/binding.json').write_text(json.dumps(dict(binding, destination='imessage')))
    spool, outbox, receipts = (courier / 'srv/courier' / n for n in ('inbound', 'outbox', 'inbox'))
    for d in (spool, outbox, receipts, receipts / 'media', courier / 'etc/courier', courier / 'private'):
        d.mkdir(parents=True, exist_ok=True)
    (courier / 'etc/courier/policy.toml').write_text('version = 1\n[captain]\nto = "+12025550101"\n')
    (courier / 'etc/courier/credential.json').write_text('{"token":"disposable-sentinel"}')
    (courier / 'private/ledger.json').write_text('{"private":"disposable-sentinel"}')
    (receipts / 'media/fixture.txt').write_text('disposable attachment')
    attachment = '[attachment: /srv/courier/inbox/media/fixture.txt (text/plain, 21 bytes)]'
    words = 'Please acknowledge the isolated pickup.\n' + attachment
    record = dict(kind='courier-inbound', version=1, seq=1, type='message', published_at='2026-10-09T01:00:00Z',
                  message_id='manual-1', chat_id='manual-chat', created_at='2026-10-09T01:00:00Z',
                  transcript=words, attachments=[{'line': attachment, 'saved': True}], other_parts=0)
    message = spool / '000000000001-message-manual-1.json'
    message.write_text(json.dumps(record))
    message.chmod(0o640)
    spool.chmod(0o550)
    receipts.chmod(0o550)
    fixed = [p for p in courier.rglob('*') if p.is_file()]
    before = snapshot(fixed)
    traces = [traced('capture')]
    accepted = call('accept', {'conversation_id': 'text'})
    assert accepted['input']['committed_transcript'] == words
    call('publish', dict(conversation_id='text', request_id='imsg-req-manual-1', response_id='manual-answer',
                        sequence=1, kind='answer', final=True, destination='imessage',
                        speech_text='The isolated pickup and attachment reference reached Firstmate.'))
    policy = courier / 'etc/courier/policy.toml'
    policy.chmod(0o660)
    traces.append(traced('policy-refusal'))
    assert not [p for p in outbox.glob('*.json') if not p.name.endswith('.stage.json')]
    policy.chmod(0o600)
    traces.append(traced('reply'))
    texts = [json.loads(p.read_text()) for p in outbox.glob('*.json') if not p.name.endswith('.stage.json')]
    assert len(texts) == 1 and texts[0]['text'] == 'The isolated pickup and attachment reference reached Firstmate.'
    assert texts[0]['to'] == '+12025550101' and texts[0]['channel'] == 'imessage'
    request_file = outbox / (texts[0]['id'] + '.json')
    request_mode = oct(stat.S_IMODE(request_file.stat().st_mode))
    assert request_mode == '0o640' and request_file.stat().st_uid == os.getuid()
    receipts.chmod(0o750)
    receipt = receipts / ('result-' + texts[0]['id'] + '.manual.json')
    receipt.write_text(json.dumps(dict(kind='courier-result', id=texts[0]['id'], result='sent')))
    receipt.chmod(0o640)
    receipts.chmod(0o550)
    receipt_before = snapshot([receipt])
    traces.append(traced('settle'))
    stages = [json.loads(p.read_text()) for p in sorted(outbox.glob('*.stage.json'))]
    assert [p['stage'] for p in stages] == ['filed', 'working', 'done'], stages
    assert snapshot(fixed) == before and snapshot([receipt]) == receipt_before
    print('CLI exchange: ' + json.dumps({'transcript': accepted['input']['committed_transcript'],
          'outgoing_reply': texts[0], 'outbox_mode': request_mode, 'stages': stages}, ensure_ascii=False), flush=True)
    print('PASS: file syscall traces show no credential, ledger or media opens; read-only inbound and receipts are unchanged', flush=True)
    running = subprocess.Popen([str(pickup), 'run'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        time.sleep(0.3)
        refused = subprocess.run([str(pickup), 'once'], env=env, text=True, capture_output=True, timeout=30)
        assert refused.returncode == 1 and 'another pickup holds this home' in refused.stderr
        print('second pickup: exit=1; ' + refused.stderr.strip(), flush=True)
    finally:
        running.terminate()
        running.wait(timeout=30)
    nohome = dict(env)
    nohome.pop('FM_HOME')
    refused = subprocess.run([str(pickup), 'once'], env=nohome, text=True, capture_output=True, timeout=30)
    assert refused.returncode == 1 and 'explicit FM_HOME is required' in refused.stderr
    print('missing home: exit=1; ' + refused.stderr.strip(), flush=True)
    (evidence / 'manual-cli-exchange.json').write_text(json.dumps({'accepted_input': accepted['input'],
        'outgoing_reply': texts[0], 'outbox_mode': request_mode, 'stages': stages,
        'courier_files_unchanged': snapshot(fixed), 'receipt_unchanged': snapshot([receipt]),
        'file_syscall_traces': traces}, indent=2) + '\n')
finally:
    for d in (courier / 'srv/courier/inbound', courier / 'srv/courier/inbox'):
        if d.exists():
            d.chmod(0o750)
    shutil.rmtree(temp)
