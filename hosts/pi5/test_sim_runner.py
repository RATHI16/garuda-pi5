"""Execute the real shell with hermetic command stubs; never contacts systemd/Pi."""
import os,subprocess
from pathlib import Path
import pytest

@pytest.mark.parametrize('mode',['active','inactive','failed','unknown'])
def test_prior_state_controls_shutdown_hook(tmp_path,mode):
 bins=tmp_path/'bin';bins.mkdir();capture=tmp_path/'args'
 scripts={
 'systemctl':'exit 1',
 'install':'exit 0',
 'python3':'''case "$*" in
 *sim_restoration.py*) if [ "$PRIOR" = unknown ]; then exit 1; fi; echo "$PRIOR";;
 *) exit 0;;
esac''',
 'sudo':'printf "%s\\n" "$@" > "$CAPTURE"',
 }
 for name,body in scripts.items():
  p=bins/name;p.write_text('#!/bin/bash\n'+body+'\n');p.chmod(0o755)
 env=dict(os.environ,PATH=str(bins)+':/usr/bin:/bin',GARUDA_SIM='1',PRIOR=mode,CAPTURE=str(capture))
 result=subprocess.run(['bash',str(Path(__file__).with_name('run-sim-detector.sh'))],env=env,capture_output=True,text=True)
 if mode=='unknown':assert result.returncode!=0 and not capture.exists()
 else:
  assert result.returncode==0,result.stderr
  assert ('ExecStopPost=' in capture.read_text())==(mode=='active')
