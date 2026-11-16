import pytest
from sim_restoration import score,prior_mode,write_evidence

def row(seq=1,pid='12',restarts='0',age=10):return dict(trial=dict(ActiveState='inactive',MainPID='0'),unit=dict(ActiveState='active',MainPID=pid,NRestarts=restarts),state=dict(frame=dict(seq=seq,epoch='a'),age_ms=age,stats=dict(instance='process-a',inferences=seq)))
def test_restoration_requires_progress_and_stable_process():
 assert score('active',[row(1),row(2)])['restored']
 for rows in [[row(),row()],[row(),row(2,pid='13')],[row(),row(2,restarts='1')],[row(),row(2,age=2000)],[row(),{'error':'HTTP down'}]]:
  assert not score('active',rows)['restored']
def test_prior_inactive_never_needs_viewer_or_inference():
 assert score('inactive',[{'trial':{'ActiveState':'inactive','MainPID':'0'},'unit':{'ActiveState':'inactive'}}])['restored']
 assert not score('inactive',[row()])['restored']
 assert not score('active',[])['restored']
@pytest.mark.parametrize('state',['activating','deactivating',None,'unknown'])
def test_transitional_prior_state_refused(state):
 with pytest.raises(ValueError):prior_mode({'ActiveState':state})

def test_missing_service_evidence_fails_closed():
 for key in ['MainPID','NRestarts']:
  rows=[row(1),row(2)]
  for r in rows:del r['unit'][key]
  assert not score('active',rows)['restored']
 assert not score('unknown',[{'trial':{'ActiveState':'inactive','MainPID':'0'},'unit':{'ActiveState':'inactive'}}])['restored']

@pytest.mark.parametrize('prior',['active','inactive','failed'])
@pytest.mark.parametrize('state',['active','activating','deactivating',None])
def test_trial_must_have_ended(prior,state):
 rows=[row(1),row(2)]
 for r in rows:r['trial']={'ActiveState':state,'MainPID':'0'}
 assert not score(prior,rows)['restored']

def test_inference_counter_and_instance_required():
 for bad in [{'instance':'process-a','inferences':1},{'instance':'other','inferences':2},{}]:
  rows=[row(1),row(2)];rows[1]['state']['stats']=bad
  assert not score('active',rows)['restored']

def test_output_never_overwrites_prior_attempt(tmp_path):
 p=tmp_path/'result.json';write_evidence(p,{'restored':False})
 with pytest.raises(FileExistsError):write_evidence(p,{'restored':True})
 assert 'false' in p.read_text()

def test_collected_trial_and_failed_systemctl_are_distinct(monkeypatch):
 import sim_restoration as m
 from types import SimpleNamespace
 monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout='LoadState=not-found\nActiveState=inactive\nMainPID=0\n',stderr=''))
 assert m.unit_state(m.TRIAL)['ActiveState']=='inactive'
 with pytest.raises(RuntimeError):m.unit_state()
 monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout='',stderr='bus unavailable'))
 with pytest.raises(RuntimeError):m.unit_state(m.TRIAL)
