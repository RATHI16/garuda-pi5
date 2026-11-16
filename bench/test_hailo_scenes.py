import copy,sys
from pathlib import Path
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'hosts/pc'))
from bench_scenes import build_scene
from hailo_scene_checks import score_scene

def rows():
 out=[]
 for i in range(201):
  y=3*i/200;x=6+3*i/200
  out.append(dict(test_stage='motion',phase='FLYING',intent=[.2,0],lock_state='LOCKED',target_id=1,frame={'epoch':'e'},requires_reselection=False,truth={'age_s':0,'sim_ns':int((120+i*.225)*1e9),'walker':{'position':[x,y,1]},'garuda_x500':{'position':[x-3,y,1.5]}},armed=True,landed=False))
 out.append(dict(out[-1],test_stage='land',armed=False,landed=True))
 return out

def test_moving_success_and_failures():
 r=rows();assert score_scene(r,[],'walk-turn')['passed']
 r[70]['target_id']=2;assert not score_scene(r,[],'walk-turn')['passed']
 r=rows();r[-1]['armed']=True;assert not score_scene(r,[],'walk-turn')['passed']
 r=rows();r[20]['truth']=None;assert not score_scene(r,[],'walk-turn')['passed']

def test_incomplete_or_stationary_not_success():
 r=rows()
 for row in r:row['truth']['walker']['position']=[6,0,1]
 assert not score_scene(r,[],'walk-turn')['passed']
 assert not score_scene([],[],'walk-turn')['passed']
 assert not score_scene(rows(),['failure'],'walk-turn')['passed']

def test_screen_requires_loss_and_no_resume():
 r=rows();assert not score_scene(r,[],'screen-crossing')['passed']
 for row in r[100:]:row.update(requires_reselection=True,intent=[0,0],lock_state='LOST')
 assert score_scene(r,[],'screen-crossing')['passed']
 r[150]['intent']=[.1,0];assert not score_scene(r,[],'screen-crossing')['passed']

def test_screen_clearance():
 r=rows()
 for row in r[100:]:row.update(requires_reselection=True,intent=[0,0],lock_state='LOST')
 r[150]['truth']['garuda_x500']['position']=[8,2,1.5]
 assert not score_scene(r,[],'screen-crossing')['checks']['screen_clearance']

def test_generated_scenes_have_fixed_schedule_and_screen():
 template=(Path(__file__).resolve().parents[1]/'hosts/pc/gazebo_lab.sdf').read_text()
 for name in ['walk-turn','screen-crossing']:
  tree=ET.fromstring(build_scene(template,name));points=tree.findall('.//actor/script/trajectory/waypoint')
  assert [p.findtext('time') for p in points]==['0','120','140','160','600']
  assert tree.findtext('.//actor/script/loop')=='true'
  if name=='screen-crossing':assert tree.findtext(".//model[@name='occluder']/pose")=='8 2 1.5 0 0 0'

def test_recorder_termination_commands_landing(monkeypatch,tmp_path):
 import signal,types
 import hailo_scene_smoke as runner
 template=(Path(__file__).resolve().parents[1]/'hosts/pc/gazebo_lab.sdf').read_text()
 (tmp_path/'hosts/pc').mkdir(parents=True);(tmp_path/'hosts/pc/gazebo_lab.sdf').write_text(template);(tmp_path/'.runtime').mkdir()
 monkeypatch.setattr(runner,'ROOT',tmp_path)
 monkeypatch.setenv('GARUDA_SIM','1');monkeypatch.setenv('GZ_PARTITION','garuda_isolated')
 monkeypatch.setattr(sys,'argv',['runner','walk-turn','--actor','/tmp/test-actor'])
 monkeypatch.setattr(runner,'check_world',lambda *a:'testhash')
 monkeypatch.setattr(runner.time,'sleep',lambda _:None)
 monkeypatch.setitem(sys.modules,'sitl_truth',types.SimpleNamespace(Truth=lambda:types.SimpleNamespace(snapshot=lambda:{'age_s':0,'sim_ns':10e9,'walker':{'position':[6,0,1]},'garuda_x500':{'position':[0,0,0]}})))
 commands=[];mode={'armed':False,'id':None,'follow':False,'seq':0,'interrupted':False}
 def command(**kw):
  commands.append(kw['command'])
  if kw['command']=='takeoff':mode['armed']=True
  if kw['command']=='select':mode['id']=1
  if kw['command']=='follow':mode['follow']=True
  if kw['command']=='land':mode['armed']=False
 def state():
  if mode['follow'] and not mode['interrupted']:
   mode['interrupted']=True;signal.raise_signal(signal.SIGTERM)
  mode['seq']+=1
  return dict(phase='FLYING' if mode['armed'] else 'GROUND',armed=mode['armed'],landed=not mode['armed'],altitude=1.5,telemetry_fresh=True,frame=dict(epoch='e',seq=mode['seq'],sim_ns=10e9,width=1280,height=720),camera={'pitch':.3},detector={'engine':'hailo10h-rpc'},perception_age_ms=10,target_id=mode['id'],requires_reselection=False,behavior='follow' if mode['follow'] else 'hold',lock_state='LOCKED',intent=[0,0],tracks=[dict(id=1,label='person',conf=.9,range_m=6,x=500,y=100,w=100,h=400)])
 monkeypatch.setattr(runner,'state',state);monkeypatch.setattr(runner,'command',command);monkeypatch.setattr(runner,'pause',lambda _:None)
 previous=signal.getsignal(signal.SIGTERM)
 import pytest,json
 try:
  with pytest.raises(SystemExit):runner.main()
 finally:signal.signal(signal.SIGTERM,previous)
 assert commands[-1]=='land' and not mode['armed']
 result=json.loads(next((tmp_path/'.runtime').glob('*.summary.json')).read_text())
 assert not result['passed'] and 'signal 15' in result['errors'][0]

def test_walls_clearance_and_follow():
 r=rows()
 for v in r:
  v['truth']['walker']['position'][1]/=2;v['truth']['garuda_x500']['position'][1]/=2
 result=score_scene(r,[],'walls-follow');assert result['passed']
 r[100]['truth']['garuda_x500']['position']=[7,3.1,1.5]
 assert not score_scene(r,[],'walls-follow')['checks']['wall_clearance']

def test_imported_walls_are_rotated_without_changing_dimensions(tmp_path):
 source=tmp_path/'walls.sdf'
 geometries=[('box1','5 0 7.5 0 0 0','1 20 15'),('box2','-3 5 7.5 0 0 0','10 1 15'),('box3','13 -10 7.5 0 0 0','17 1 15'),('box4','12 0 7.5 0 0 0','1 20 15')]
 source.write_text('<sdf><world name="walls"><scene/>'+''.join(f'<model name="{name}"><pose>{pose}</pose><link><visual><geometry><box><size>{size}</size></box></geometry></visual><collision><geometry><box><size>{size}</size></box></geometry></collision></link></model>' for name,pose,size in geometries)+'</world></sdf>')
 template=(Path(__file__).resolve().parents[1]/'hosts/pc/gazebo_lab.sdf').read_text()
 tree=ET.fromstring(build_scene(template,'walls-follow',source))
 wall=tree.find(".//model[@name='box1']")
 assert [float(x) for x in wall.findtext('pose').split()][:3]==[7,-3.5,7.5]
 assert wall.findtext('.//box/size')=='1 20 15'
 assert tree.find(".//model[@name='occluder']") is None
 assert tree.find(".//actor/script/trajectory/waypoint[time='160']/pose").text=='9 1.5 1 0 0 0'

 source.write_text(source.read_text().replace('1 20 15','2 20 15'))
 import pytest
 with pytest.raises(ValueError,match='clearance scorer'):build_scene(template,'walls-follow',source)
