"""fixed_stereo source: real bench-camera stereo observation driving the SIMULATED vehicle.
Design brief: astra-0061. Depth is an unvalidated preview and the camera pose is assumed;
both need the explicit simulation switch and nothing here relaxes age, identity or
missing-data rules."""
import io,json,math,pathlib,sys
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[3]/'hosts/pc'))
from sitl_demo_core import DemoControl
import observation_client as client
import sitl_demo
from fixed_stereo_observation import decode

def pi_snapshot(seq=1,z=2.0,tid=7,roi=(772,212,868,408),status='ok',reason='ok',coasting=False,age=100):
    return {'v':2,'age_ms':age,'stats':{'instance':'proc','fps':15.0},
        'frame':{'capture_host_ns':5_000_000_000+seq,'width':1280,'height':720,'seq':seq,'source':'oak-rgb','epoch':'oak-rgb:E:0','capture_pair':{'epoch':'E','rgb_seq':900+seq}},
        'lock':{'id':tid,'class':'person','coasting':coasting},
        'tracks':[{'id':tid,'label':'person','conf':.9,'x':700.,'y':100.,'w':240.,'h':560.,'coasting':coasting}],
        'stereo':{'status':status,'reason':reason,'z_m':z if status=='ok' else None,'roi':list(roi),'pair':{'epoch':'E','rgb_seq':900+seq},
            'calibration_id':'cal','geometry':dict(epoch='E',calibration_id='cal',width=1280,height=720,fx=800.,fy=800.,cx=640.,cy=360.,distortion='none'),
            'measurement_qualified':False,'preview_only':True}}
def observed(**k):
    s=pi_snapshot(**k);s['fixed_stereo']=decode(s);s['kind']='fixed_stereo';return s
def flying(accept=True):
    c=DemoControl();c.accept_preview=accept;c.home=(10.,20.);c.receive(observed(),0.);c.select(7);c.mode='follow';return c
def step(c,snap,now,position=(10.,20.,-1.5)):
    c.receive(snap,now);return c.velocity(list(position),now,.05,[1.,0.,0.,0.])

def test_default_off_an_unvalidated_preview_never_moves_the_vehicle():
    c=flying(accept=False);v=step(c,observed(seq=2),.05)
    assert v[:2]==(0.,0.) and c.target is None and 'not accepted' in c.reason and '--accept-preview-stereo-in-simulation' in c.reason
    assert DemoControl().accept_preview is False

def test_position_uses_the_stereo_point_and_the_assumed_camera_pose():
    c=flying();assert c.camera['assumed'] is True
    # Region centre (820,310) at 2 m with fx=800: 0.45 m right, 2 m forward. Camera 0.8 m behind home, facing north.
    step(c,observed(seq=2),.05)
    assert c.target==pytest.approx((10-.8+2.0,20+.45)) and 'unvalidated depth, assumed camera pose' in c.reason
    step(c,observed(seq=3,z=4.0),.1);assert c.target==pytest.approx((10-.8+4.0,20+.9))

def test_requested_gap_is_honoured_even_when_the_person_is_close_to_the_camera():
    c=flying();c.gap=5.;c.limit=15.;c.speed=2.;p=[10.,20.,-1.5]
    for n in range(2,900):
        vn,ve,_=step(c,observed(seq=n,z=1.2,roi=(592,212,688,408)),n*.05,p);p[0]+=vn*.05;p[1]+=ve*.05
    assert c.target==pytest.approx((10-.8+1.2,20.))
    assert math.hypot(p[0]-c.target[0],p[1]-c.target[1])==pytest.approx(5.,abs=.2) and p[0]<10-.8   # stands off behind the camera

@pytest.mark.parametrize('kw,text',[(dict(status='invalid',reason='cluster_too_spread'),'cluster_too_spread'),(dict(status='ambiguous',reason='competing_depth_clusters'),'competing_depth_clusters'),
    (dict(status='unavailable',reason='frame_not_in_history'),'frame_not_in_history')])
def test_no_distance_means_hold_with_the_reason_and_no_remembered_position(kw,text):
    c=flying();step(c,observed(seq=2),.05);assert c.target is not None
    v=step(c,observed(seq=3,**kw),.1)
    assert v[:2]==(0.,0.) and c.target is None and text in c.reason

def test_observation_for_another_person_frame_or_schema_is_not_used():
    c=flying();s=observed(seq=2);s['fixed_stereo']['target_id']=8
    assert step(c,s,.05)[:2]==(0.,0.) and 'another person' in c.reason
    s=observed(seq=3);s['fixed_stereo']['frame_seq']=2
    assert step(c,s,.1)[:2]==(0.,0.) and 'another frame' in c.reason
    s=observed(seq=4);s['fixed_stereo']['schema']='garuda.fixed-stereo-observation.2'
    assert step(c,s,.15)[:2]==(0.,0.) and 'missing' in c.reason
    s=observed(seq=5);del s['fixed_stereo']
    assert step(c,s,.2)[:2]==(0.,0.) and 'missing' in c.reason
    s=observed(seq=6);s['fixed_stereo']['optical_point_m']=[0.,0.,float('nan')]
    assert step(c,s,.25)[:2]==(0.,0.)

def test_gap_longer_than_half_a_second_requires_reselection_and_never_resumes_by_itself():
    c=flying();step(c,observed(seq=2),.05)
    assert step(c,observed(seq=3,coasting=True),.1)[:2]==(0.,0.) and c.lock_state=='LOST' and not c.requires_reselection
    step(c,observed(seq=4),.2);assert c.lock_state=='LOCKED'           # a short miss inside the limit continues
    step(c,observed(seq=5,coasting=True),.3)
    step(c,observed(seq=6,coasting=True),.75)                          # unseen for more than 0.5 s
    assert c.requires_reselection and c.lock_state=='RESELECT'
    v=step(c,observed(seq=7),.8)                                       # the person is back: still held
    assert v[:2]==(0.,0.) and c.requires_reselection and c.target is None
    c.select(7);c.mode='follow';step(c,observed(seq=8),.85);assert c.target is not None

def test_stale_perception_and_camera_restart_require_reselection():
    c=flying();step(c,observed(seq=2),.05)
    assert step(c,observed(seq=3,age=800),.1)[:2]==(0.,0.) and c.requires_reselection
    c=flying();s=observed(seq=2);s['frame']['epoch']='oak-rgb:F:0';s['fixed_stereo']=decode({k:v for k,v in s.items() if k not in('fixed_stereo','kind')})
    assert step(c,s,.05)[:2]==(0.,0.) and c.requires_reselection

def test_other_sources_are_unchanged_by_the_switch():
    c=DemoControl();c.accept_preview=True;s=pi_snapshot();s['kind']='pi';c.home=(0.,0.);c.receive(s,0.);c.select(7);c.mode='follow'
    c.velocity([0.,0.,-1.5],0.,.05,[1.,0.,0.,0.]);assert c.reason.startswith('Following live camera target')   # schematic, not stereo

# ---- reader ---------------------------------------------------------------------------
def response(data,status=200):
    r=io.BytesIO(json.dumps(data).encode());r.status=status;r.__enter__=lambda:r;r.__exit__=lambda *a:False;return r
def test_reader_waits_on_the_camera_decodes_and_keeps_age_conservative(monkeypatch):
    clock=iter([100.,100.20,200.,200.02,300.,300.2,400.,400.2]);urls=[]
    monkeypatch.setattr(client,'time',SimpleNamespace(monotonic=lambda:next(clock)))
    snaps=[dict(pi_snapshot(seq=5),waited_ms=150),dict(pi_snapshot(seq=6)),dict(pi_snapshot(seq=7),waited_ms=999),dict(pi_snapshot(seq=8),waited_ms=float('nan'))]
    def opener(url,timeout):urls.append((url,timeout));return response(json.loads(json.dumps(snaps[len(urls)-1]).replace('NaN','null')))
    monkeypatch.setattr(client,'urlopen',opener)
    r=client.ObservationClient();sel=dict(kind='fixed_stereo',generation=1,url='http://pi')
    d=r.read(sel)
    assert urls[0]==('http://pi/api/state?after=-1',1.5) and d['kind']=='fixed_stereo' and 'waited_ms' not in d
    assert d['age_ms']==pytest.approx(100+200-150)                    # 200 ms request, 150 ms of it held by the camera
    assert d['fixed_stereo']['status']=='ok' and d['fixed_stereo']['age_ms']==100 and d['fixed_stereo']['frame_seq']==5
    d=r.read(sel);assert urls[1][0]=='http://pi/api/state?after=5' and d['age_ms']==pytest.approx(100+20)   # no hold reported
    d=r.read(sel);assert d['age_ms']==pytest.approx(100+200)          # implausible hold: counted as none
    d=r.read(sel);assert d['age_ms']==pytest.approx(100+200)

def test_reader_cursor_resets_when_the_source_changes(monkeypatch):
    urls=[];monkeypatch.setattr(client,'urlopen',lambda url,timeout:(urls.append(url),response(pi_snapshot(seq=9)))[1])
    r=client.ObservationClient();r.read(dict(kind='fixed_stereo',generation=1,url='http://pi'));r.read(dict(kind='fixed_stereo',generation=2,url='http://pi'))
    assert urls==['http://pi/api/state?after=-1','http://pi/api/state?after=-1']

# ---- selection on the camera ------------------------------------------------------------
class Reply(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*a):return False
def test_selection_is_made_on_the_camera_by_exact_id_and_only_a_matching_confirmation_counts():
    from urllib.error import HTTPError
    snap=pi_snapshot();calls=[]
    def ok(req,timeout):calls.append((req.full_url,req.get_method()));return Reply(json.dumps(dict(ok=True,id=7,epoch='oak-rgb:E:0',instance='proc')).encode())
    sitl_demo.camera_select('http://pi',snap,7,ok)
    assert calls==[('http://pi/api/select_track?id=7&epoch=oak-rgb%3AE%3A0&instance=proc','POST')]
    for body in [dict(ok=False),dict(ok=True,id=8,epoch='oak-rgb:E:0',instance='proc'),dict(ok=True,id=7,epoch='x',instance='proc'),dict(ok=True,id=7,epoch='oak-rgb:E:0',instance='other'),[]]:
        with pytest.raises(ValueError):sitl_demo.camera_select('http://pi',snap,7,lambda req,timeout:Reply(json.dumps(body).encode()))
    def refuse(req,timeout):raise HTTPError(req.full_url,409,'Conflict',{},io.BytesIO(b'{"ok":false,"reason":"observation_stale"}'))
    with pytest.raises(ValueError,match='observation_stale'):sitl_demo.camera_select('http://pi',snap,7,refuse)
    def down(req,timeout):raise OSError('down')
    with pytest.raises(ValueError,match='unreachable'):sitl_demo.camera_select('http://pi',snap,7,down)
    with pytest.raises(ValueError,match='identity unknown'):sitl_demo.camera_select('http://pi',{'frame':{},'stats':{}},7,ok)


def test_position_is_published_before_takeoff_and_cleared_without_a_measurement():
    # Seen live: on the ground the controller had no target, so the Gazebo figure never appeared.
    c=DemoControl();c.accept_preview=True;c.home=(0.,0.);c.receive(observed(),0.);c.select(7)
    assert c.mode=='hold'
    c.receive(observed(seq=2),.05);assert c.track(.05) is not None
    assert c.target==pytest.approx((-.8+2.0,.45)) and c.lock_state=='LOCKED' and 'Target locked' in c.reason
    c.receive(observed(seq=3,status='ambiguous',reason='competing_depth_clusters'),.1);c.track(.1)
    assert c.target is None and c.lock_state=='LOCKED' and 'competing_depth_clusters' in c.reason
    c.receive(observed(seq=4,coasting=True),.15);c.track(.15);assert c.target is None and c.lock_state=='LOST'
    off=DemoControl();off.receive(observed(),0.);off.select(7);off.receive(observed(seq=2),.05);off.track(.05)
    assert off.target is None and 'not accepted' in off.reason           # default-off applies on the ground too


def test_browser_access_is_only_for_an_explicitly_allowed_origin():
    allowed=['http://10.42.0.128:8098']
    h=sitl_demo.cors_headers('http://10.42.0.128:8098',allowed)
    assert h['Access-Control-Allow-Origin']=='http://10.42.0.128:8098' and h['Access-Control-Allow-Private-Network']=='true' and 'Content-Type' in h['Access-Control-Allow-Headers']
    for origin in (None,'','http://evil.example','http://10.42.0.128:8099','null','*'):assert sitl_demo.cors_headers(origin,allowed)=={}
    assert sitl_demo.cors_headers('http://10.42.0.128:8098',[])=={}          # nothing is allowed by default

# ---- optional appearance return match (off by default) -------------------------------------
def look(v=1.0):return [v]+[0.5]*127
def moving(seq,tid,now_lock,obs_for=None,appearance=None,z=2.0):
    s=pi_snapshot(seq=seq,tid=tid,z=z);s['tracks'][0]['appearance']=appearance or look()
    s['lock']['id']=now_lock
    s['fixed_stereo']=decode(s) if obs_for is None else dict(decode(dict(s,lock=dict(s['lock'],id=tid))),target_id=obs_for)
    s['kind']='fixed_stereo';return s

def test_reacquire_is_off_by_default_and_a_new_track_id_needs_a_click():
    c=flying();assert c.reacquire is False
    for n in range(2,8):step(c,moving(n,7,7),n*.05)
    for n in range(8,40):v=step(c,moving(n,9,9),n*.05)                 # same clothes, new ID, for 1.6 s
    assert c.requires_reselection and c.target_id==7 and v[:2]==(0.,0.)

def test_with_reacquire_a_clear_appearance_match_continues_under_the_new_id_once_the_camera_measures_it():
    c=flying();c.reacquire=True
    for n in range(2,8):step(c,moving(n,7,7),n*.05)
    assert c.profiles                                                     # appearance of the selected person was remembered
    # New ID 9 appears; the camera is still locked on 7 for a while.
    held=[step(c,moving(n,9,7,obs_for=7),n*.05) for n in range(8,30)]
    assert c.target_id==9 and c.reacquired and not c.requires_reselection
    assert held[-1][:2]==(0.,0.) and 'another person' in c.reason        # no position until the camera measures ID 9
    assert all(h[:2]==(0.,0.) for h in held[:10])                         # nothing moved during the confirmation time either
    v=step(c,moving(30,9,9),1.5)
    assert c.target is not None and 'appearance return matched' in c.reason and c.mode=='follow'

def test_with_reacquire_a_different_looking_person_is_not_followed():
    c=flying();c.reacquire=True
    for n in range(2,8):step(c,moving(n,7,7),n*.05)
    other=[0.0]*64+[1.0]*64
    for n in range(8,40):v=step(c,moving(n,9,9,appearance=other),n*.05)
    assert c.target_id==7 and c.lock_state=='LOST' and v[:2]==(0.,0.) and c.target is None

def test_with_reacquire_stale_perception_still_requires_reselection():
    c=flying();c.reacquire=True;step(c,moving(2,7,7),.05)
    s=moving(3,7,7);s['age_ms']=900
    assert step(c,s,.1)[:2]==(0.,0.) and c.requires_reselection

def with_identity(s,**idn):
    s['stats']['identity']={**dict(target=None,confirmed=True,state='tracking',reason='',rebound_from=None,instance=s['stats']['instance'],frame_seq=s['frame']['seq'],frame_epoch=s['frame']['epoch']),**idn};return s

def test_camera_identity_decision_is_adopted_only_when_reacquire_is_allowed():
    c=flying();c.reacquire=True
    for n in range(2,6):step(c,with_identity(moving(n,7,7),target=7),n*.05)
    v=step(c,with_identity(moving(6,9,9),target=9,rebound_from=7),.3)
    assert c.target_id==9 and c.reacquired and c.target is not None and c.lock_state=='LOCKED' and c.mode=='follow'
    strict=flying()
    for n in range(2,6):step(strict,with_identity(moving(n,7,7),target=7),n*.05)
    for n in range(6,30):step(strict,with_identity(moving(n,9,9),target=9,rebound_from=7),n*.05)
    assert strict.target_id==7 and strict.requires_reselection

def test_with_camera_identity_the_controller_makes_no_colour_guess_of_its_own():
    c=flying();c.reacquire=True
    for n in range(2,8):step(c,with_identity(moving(n,7,7),target=7),n*.05)
    for n in range(8,60):v=step(c,with_identity(moving(n,9,7,obs_for=7),target=7,state='searching'),n*.05)   # same clothes, camera undecided
    assert c.target_id==7 and c.lock_state=='LOST' and v[:2]==(0.,0.)


# ---- a detector restart is an identity boundary (astra-0062 finding 1) ------------------------
def restarted(seq,instance='new-detector',tid=7):
    s=pi_snapshot(seq=seq,tid=tid);s['stats']['instance']=instance;s['fixed_stereo']=decode(s);s['kind']='fixed_stereo';return s

@pytest.mark.parametrize('reacquire',[False,True])
def test_detector_only_restart_with_the_same_camera_epoch_and_track_id_needs_reselection(reacquire):
    c=flying();c.reacquire=reacquire
    for n in range(2,21):step(c,observed(seq=n),n*.05)
    assert c.target is not None
    v=step(c,restarted(1),1.05)                                           # same epoch, same ID 7, new process, sequence restarted
    assert v[:2]==(0.,0.) and c.requires_reselection and c.target is None and c.lock_state=='RESELECT' and c.profiles==[] and c.reacquired is False
    v=step(c,restarted(2),1.10);assert v[:2]==(0.,0.) and c.requires_reselection
    c.select(7);c.mode='follow';step(c,restarted(3),1.15)
    assert c.instance=='new-detector' and c.target is not None            # an explicit selection in the new process works

def test_a_frame_sequence_going_backwards_in_one_process_is_a_boundary():
    c=flying();c.reacquire=True
    for n in range(2,21):step(c,observed(seq=n),n*.05)
    v=step(c,observed(seq=5),1.05)
    assert v[:2]==(0.,0.) and c.requires_reselection and c.target is None

def test_an_observation_from_another_process_than_the_snapshot_or_selection_is_refused():
    c=flying();s=observed(seq=2);s['fixed_stereo']['source_instance']='other'
    assert step(c,s,.05)[:2]==(0.,0.) and c.target is None and 'another detector process' in c.reason
    s=observed(seq=3);s['fixed_stereo']['source_instance']=None
    assert step(c,s,.1)[:2]==(0.,0.) and c.target is None

def test_identity_decision_from_another_frame_or_process_or_unconfirmed_is_not_adopted():
    for change in (dict(frame_seq=1),dict(instance='other'),dict(frame_epoch='x'),dict(confirmed=False)):
        c=flying();c.reacquire=True
        for n in range(2,6):step(c,with_identity(moving(n,7,7),target=7),n*.05)
        step(c,with_identity(moving(6,9,9),target=9,rebound_from=7,**change),.3)
        assert c.target_id==7,change

def test_unconfirmed_camera_identity_means_hold_even_though_the_track_is_visible():
    c=flying();c.reacquire=True
    for n in range(2,6):step(c,with_identity(moving(n,7,7),target=7),n*.05)
    v=step(c,with_identity(moving(6,7,7),target=7,confirmed=False,state='verifying'),.3)
    assert v[:2]==(0.,0.) and c.target is None and 'checking' in c.reason
    v=step(c,with_identity(moving(7,7,7),target=7),.35);assert c.target is not None

@pytest.mark.parametrize('reacquire',[False,True])
def test_detector_restart_is_caught_even_when_frame_numbers_keep_increasing(reacquire):
    # Isolates the process check: no sequence regression to lean on.
    c=flying();c.reacquire=reacquire
    for n in range(2,21):step(c,observed(seq=n),n*.05)
    v=step(c,restarted(21),1.05)
    assert v[:2]==(0.,0.) and c.requires_reselection and c.lock_state=='RESELECT' and 'Detector restarted' in c.reason


def test_moving_camera_is_refused_even_with_preview_accepted():
    for reacquire in (False, True):
        c=flying();c.reacquire=reacquire;s=observed(seq=2)
        s['stats']['capabilities']={'mount':'vehicle'}
        assert step(c,s,.1)[:2]==(0.,0.)
        assert c.target is None and 'fixed-camera mapping refused' in c.reason
