import json, os, pathlib, shutil, subprocess, tempfile
root=pathlib.Path.cwd()
l=pathlib.Path(tempfile.mkdtemp(prefix='ack-repro-',dir=root/'.test-phase-tmp'))
for d in ('data','state','config'): (l/d).mkdir()
env=os.environ.copy()
for k in list(env):
    if k.startswith('FM_'): env.pop(k)
env['FM_HOME']=str(l)
transcript=[]
def run(*args):
    p=subprocess.run([str(root/'bin/fm-inbox.sh'),*args],env=env,capture_output=True,text=True,check=True)
    transcript.append('$ fm-inbox.sh '+' '.join(args)+'\n'+p.stdout+p.stderr)
    return p.stdout
try:
    initial=json.loads(run('note','--json','--request-id','sequential-ack','Handle this once.'))
    run('drain','--ack',initial['id'])
    replay=json.loads(run('note','--json','--request-id','sequential-ack','Handle this once.'))
    run('note','--request-id','sequential-ack','Handle this once.')
    announced=json.loads(run('announce','--json',initial['id']))
    assert announced['acknowledged'] is False
    receipts=json.loads(run('receipts','--all-pending','--all-handled'))
    assert receipts['pending']==[] and receipts['handled'][0]['acknowledged'] is True
    (l/'state/inbox/.announced'/initial['id']).unlink()
    counterfactual=json.loads(run('note','--json','--request-id','sequential-ack','Handle this once.'))
    assert counterfactual['acknowledged'] is True
    transcript.append('Counterfactual: removing only the fixture announcement marker changes the replay to acknowledged=true. The handled note and request ID are unchanged.\n')
    if replay['acknowledged'] is not True:
        transcript.append('FAIL: sequential replay reports acknowledged=false and promises another pickup, although receipts show acknowledged=true and no pending note. No concurrent callers or publication race were used.\n')
        rc=1
    else:
        transcript.append('PASS: sequential replay reports the durable acknowledgement.\n'); rc=0
finally:
    pathlib.Path('/home/firstmate/.no-mistakes/evidence/01M4G9DCM2DEDN0RDNFCWWW0YJ/round3-ack-reproduction.log').write_text('\n'.join(transcript))
    shutil.rmtree(l)
raise SystemExit(rc)
