"""Own only the experiment's bridge/dashboard processes, with local logs and PID checks."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'.runtime/processes'

def identity(pid):
    try:return Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[19]
    except (FileNotFoundError,ProcessLookupError):return None

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['start','stop','status'])
    p.add_argument('--only',choices=['vision','agent','dashboard','person']);a=p.parse_args()
    RUN.mkdir(parents=True,exist_ok=True)
    commands={
        'vision':[str(ROOT/'.runtime/vision-env/bin/python'),'hosts/pc/vision.py','--data-root',(os.environ.get('GARUDA_DATA_ROOT') or str(ROOT/'datasets'))],
        'agent':[str(ROOT/'.runtime/agent/bin/MicroXRCEAgent'),'udp4','-p','8889','-v','2'],
        # GARUDA_PIXI_MANIFEST lets a worktree reuse an existing ROS environment instead of building its own.
        'dashboard':[str(ROOT/'.runtime/tools/pixi'),'run',*(['--manifest-path',os.environ['GARUDA_PIXI_MANIFEST']] if os.environ.get('GARUDA_PIXI_MANIFEST') else []),'bash',str(ROOT/'hosts/pc/run_ros.sh'),'--source',os.environ.get('GARUDA_PI_URL','http://10.42.0.128:8098'),
            # Explicit and off by default: lets the unvalidated stereo preview move the SIMULATED vehicle.
            *(['--accept-preview-stereo-in-simulation'] if os.environ.get('GARUDA_ACCEPT_PREVIEW_STEREO')=='1' else []),
            *(['--accept-mono-estimate-in-simulation'] if os.environ.get('GARUDA_ACCEPT_MONO_ESTIMATE')=='1' else []),
            *(['--accept-onboard-in-simulation'] if os.environ.get('GARUDA_ACCEPT_ONBOARD')=='1' else []),
            *(['--accept-live-onboard-in-simulation'] if os.environ.get('GARUDA_ACCEPT_LIVE_ONBOARD')=='1' else []),
            *(['--stereo-reacquire'] if os.environ.get('GARUDA_STEREO_REACQUIRE')=='1' else []),
            # Lets the camera's own demo page (served by the Pi) drive this simulator from the browser.
            *(['--allow-origin',os.environ['GARUDA_LAB_ALLOW_ORIGIN']] if os.environ.get('GARUDA_LAB_ALLOW_ORIGIN') else [])],
        # Places the Gazebo figure at the fixed-stereo target (visualisation only).
        'person':['python3','hosts/pc/gazebo_person.py'],
    }
    if a.action=='start' and os.environ.get('GARUDA_SIM')!='1':raise SystemExit('GARUDA_SIM=1 required')
    if a.only:commands={a.only:commands[a.only]}
    if a.action=='stop' and a.only!='person':    # the figure placer is display only; it may be restarted in flight
        try:
            with urllib.request.urlopen('http://127.0.0.1:8099/api/state',timeout=1) as response:state=json.load(response)
        except Exception:state={}
        if state.get('armed'):raise SystemExit('Land the simulated aircraft before stopping lab services')
    for name,cmd in commands.items():
        file=RUN/f'{name}.json';record=json.loads(file.read_text()) if file.exists() else {}
        alive=bool(record) and identity(record['pid'])==record['identity']
        if a.action=='stop' and alive:
            os.killpg(record['pid'],signal.SIGTERM);print(name,'stop requested');continue
        if a.action=='start' and not alive:
            env=dict(os.environ,GARUDA_SIM='1',ROS_DOMAIN_ID='42',ROS_LOCALHOST_ONLY='1',GZ_PARTITION='garuda_isolated',PYTHONNOUSERSITE='1')
            if name=='agent':env['LD_LIBRARY_PATH']=str(ROOT/'.runtime/agent/lib')
            with (RUN/f'{name}.log').open('ab') as log:
                process=subprocess.Popen(cmd,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,
                    stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            record={'pid':process.pid,'identity':identity(process.pid)};file.write_text(json.dumps(record))
            time.sleep(.3)
            if process.poll() is not None:raise SystemExit(f'{name} failed; inspect {RUN/name}.log')
            print(name,'started',process.pid)
        else:print(name,'running' if alive else 'stopped')

if __name__=='__main__':main()
