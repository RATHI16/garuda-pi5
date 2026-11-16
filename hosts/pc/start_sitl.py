"""Launch only the isolated Gazebo/PX4 instance. No real flight controller endpoints."""
import argparse
import hashlib,json
import os
from pathlib import Path
import subprocess
import signal
from xml.sax.saxutils import escape
from lab_launcher import config

ROOT=Path(__file__).resolve().parents[2]

def terminate_tree(process):
    children = {}
    for stat in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = stat.read_text().split(') ',1)[1].split()
            children.setdefault(int(fields[1]), []).append(int(stat.parent.name))
        except (OSError, ValueError): pass
    def descendants(pid):
        return [child for direct in children.get(pid, []) for child in [*descendants(direct), direct]]
    for pid in [*descendants(process.pid), process.pid]:
        try: os.kill(pid, signal.SIGTERM)
        except ProcessLookupError: pass
    try: process.wait(timeout=5)
    except subprocess.TimeoutExpired: process.kill()

def wait_for_simulation(vehicle, gazebo):
    while vehicle.poll() is None:
        if gazebo is not None and gazebo.poll() is not None:
            print('Gazebo closed; ending its PX4 session.',flush=True)
            return
        try: vehicle.wait(timeout=.5)
        except subprocess.TimeoutExpired: pass

def main():
    def stop(signum, frame): raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop)
    if os.environ.get('GARUDA_SIM')!='1':
        raise SystemExit('Refused: GARUDA_SIM=1 is required')
    p=argparse.ArgumentParser();p.add_argument('--px4',type=Path,default=Path(config()['px4']))
    p.add_argument('--actor',type=Path,default=Path(config()['actor']))
    p.add_argument('--external-person',action='store_true',help='Replace the scripted walker with a figure placed from outside (hosts/pc/gazebo_person.py)')
    p.add_argument('--bench-scene',choices=['walk-turn','screen-crossing','walls-follow']);p.add_argument('--stationary-check',action='store_true');p.add_argument('--attach',action='store_true');p.add_argument('--gazebo-only',action='store_true');p.add_argument('--headless',action='store_true');p.add_argument('--visual',action='store_true');a=p.parse_args()
    if a.stationary_check and a.bench_scene:p.error('choose one bench scenario')
    if a.external_person and (a.stationary_check or a.bench_scene or a.attach or not (a.visual or a.gazebo_only)):p.error('--external-person needs a fresh visual world and no scripted bench scenario')
    if (a.stationary_check or a.bench_scene) and (a.attach or not (a.visual or a.gazebo_only)):p.error('stationary check needs a fresh visual world')
    if a.attach and a.gazebo_only: p.error('--attach and --gazebo-only cannot be combined')
    px4=a.px4.resolve();build=px4/'build/px4_sitl_default'
    if not a.gazebo_only and not (build/'bin/px4').is_file():
        raise SystemExit('PX4 SITL build missing: configure the checkout path in the main demo Settings')
    run=ROOT/'.runtime/sitl';run.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ, PX4_SYS_AUTOSTART='4001',PX4_SIM_MODEL='gz_x500',PX4_GZ_WORLD='default',
             PX4_GZ_MODELS=str(px4/'Tools/simulation/gz/models'),
             PX4_GZ_WORLDS=str(px4/'Tools/simulation/gz/worlds'),
             GZ_SIM_RESOURCE_PATH=str(px4/'Tools/simulation/gz/models'),
             GZ_SIM_SERVER_CONFIG_PATH=str(px4/'Tools/simulation/gz/server.config'),
             GZ_PARTITION='garuda_isolated',ROS_DOMAIN_ID='42',PX4_UXRCE_DDS_PORT='8889',
             PX4_UXRCE_DDS_NS='garuda_sitl',PX4_PARAM_UXRCE_DDS_AG_IP='2130706433')
    if a.stationary_check or a.bench_scene:
        import xml.etree.ElementTree as ET
        plugin=ROOT/'.runtime/sim-truth/libBenchTruth.so'
        if not plugin.is_file():raise SystemExit('Build bench/sim_truth/build.sh first')
        server=ET.parse(env['GZ_SIM_SERVER_CONFIG_PATH'])
        ET.SubElement(server.getroot().find('plugins'),'plugin',entity_name='*',entity_type='world',filename=str(plugin),name='BenchTruth')
        config_file=run/'bench-server.config';server.write(config_file)
        env['GZ_SIM_SERVER_CONFIG_PATH']=str(config_file)
    if a.headless:env['HEADLESS']='1'
    gazebo=None
    if a.visual or a.gazebo_only:
        env.update(PX4_GZ_STANDALONE='1',PX4_GZ_WORLD='garuda_lab',PX4_GZ_MODEL_NAME='garuda_x500',PX4_GZ_NO_FOLLOW='1')
        if not a.actor.is_file(): raise SystemExit('Actor mesh missing: set its path in the main demo Settings')
        world=run/'garuda_lab.sdf'
        text=(ROOT/'hosts/pc/gazebo_lab.sdf').read_text().replace('@ACTOR_MESH@',escape(str(a.actor.resolve())))
        if a.stationary_check:
            import xml.etree.ElementTree as ET
            tree=ET.fromstring(text);tree.find(".//model[@name='garuda_x500']/link[@name='follow_camera']/pose").text='0.15 0 0.18 0 0.3 0'
            trajectory=tree.find('.//actor/script/trajectory')
            for child in list(trajectory):trajectory.remove(child)
            for seconds in (0,600):
                point=ET.SubElement(trajectory,'waypoint');ET.SubElement(point,'time').text=str(seconds);ET.SubElement(point,'pose').text='6 0 1 0 0 0'
            text=ET.tostring(tree,encoding='unicode')
        if a.external_person:
            from gazebo_person import external_person_world
            text=external_person_world(text)
        if a.bench_scene:
            from bench_scenes import build_scene
            text=build_scene(text,a.bench_scene,px4/'Tools/simulation/gz/worlds/walls.sdf')
        world.write_text(text)
        cmd=['gz','sim','-r',str(world)]
        if a.headless:cmd.insert(2,'-s')
        if not a.attach:
            gazebo=subprocess.Popen(cmd,env=env,start_new_session=True)
            start=Path(f'/proc/{gazebo.pid}/stat').read_text().split(') ',1)[1].split()[19]
            (run/'world-instance.json').write_text(json.dumps(dict(pid=gazebo.pid,start_ticks=start,
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                world=str(world),world_sha256=hashlib.sha256(world.read_bytes()).hexdigest(),
                command=cmd,partition=env['GZ_PARTITION'])))
    if a.gazebo_only:
        try: gazebo.wait()
        finally:
            if gazebo.poll() is None: terminate_tree(gazebo)
        return
    # Instance 3 isolates PX4 sockets; all writable simulator files live under .runtime.
    cmd=[str(build/'bin/px4'),str(build/'etc'),'-d','-i','3','-w',str(run)]
    vehicle=subprocess.Popen(cmd,cwd=run,env=env)
    try: wait_for_simulation(vehicle,gazebo)
    finally:
        if vehicle.poll() is None:
            vehicle.terminate()
            try: vehicle.wait(timeout=5)
            except subprocess.TimeoutExpired: vehicle.kill()
        if gazebo and gazebo.poll() is None:
            terminate_tree(gazebo)

if __name__=='__main__':main()
