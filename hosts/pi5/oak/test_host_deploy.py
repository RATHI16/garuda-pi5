"""Run installer under filesystem/systemctl stubs; never contacts the Pi."""
from pathlib import Path
import os,subprocess
import pytest

@pytest.mark.parametrize('mode',['', '--capture-only', '--disable'])
@pytest.mark.parametrize('interrupt',[False,True])
def test_installer_success_or_hup_restores(tmp_path,interrupt,mode):
 source=Path(__file__).with_name('enable-host-stereo.sh').read_text()
 conf=tmp_path/'service/override.conf';conf.parent.mkdir();conf.write_text('old-config\n')
 log=tmp_path/'commands';bin=tmp_path/'bin';bin.mkdir()
 for name in ['preview','pairing','geometry','imu_stream','video_link','host_capture','host_stereo','hailo_host_stereo','host_flow']:(tmp_path/(name+'.py')).write_text('')
 script=tmp_path/'install.sh'
 source=source.replace('/etc/systemd/system/garuda-oak-preview.service.d/30-garuda-host-stereo.conf',str(conf)).replace('/home/garuda/garuda-host-stereo',str(tmp_path/'releases'))
 source=source.replace("PYTHONPATH=/usr/lib/python3/dist-packages /home/garuda/.venvs/oak-bringup-3.12.1/bin/python -c 'import cv2,depthai,numpy'",':').replace('sleep 3',':')
 script.write_text(source)
 (bin/'sudo').write_text('#!/bin/bash\nexec "$@"\n')
 (bin/'systemctl').write_text('#!/bin/bash\necho "$*" >> "$TEST_LOG"\nexit 0\n')
 (bin/'python3').write_text('#!/bin/bash\n'+('kill -HUP "$PPID"\nsleep .1\n' if interrupt else 'exit 0\n'))
 for p in bin.iterdir():p.chmod(0o755)
 r=subprocess.run(['bash',str(script)]+([mode] if mode else []),env=dict(os.environ,PATH=str(bin)+':'+os.environ['PATH'],TEST_LOG=str(log)),capture_output=True,text=True)
 if interrupt:
  assert r.returncode!=0 and conf.read_text()=='old-config\n'
  assert 'restart garuda-oak-preview garuda-mono' in log.read_text()
 else:
  assert r.returncode==0,r.stderr
  assert ('--host-stereo' in conf.read_text()) == (mode=='')
  assert '--record-pairs' not in conf.read_text()
  assert ' --stereo' not in conf.read_text()
