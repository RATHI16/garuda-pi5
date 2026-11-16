"""Exercise the same selection action invoked by Demo.action, without ROS."""
import pytest
from sitl_demo import select_command
from sitl_demo_core import DemoControl


def observation(seq=1,epoch='camera',coasting=False):
 return dict(kind='gazebo',age_ms=0,frame=dict(seq=seq,epoch=epoch,width=1280,height=720),
             tracks=[dict(id=7,x=600,y=250,w=80,h=200,conf=.9,coasting=coasting)])

def selected():
 c=DemoControl();c.receive(observation(),0)
 select_command(c,dict(id=7,epoch='camera'),0);c.mode='follow'
 return c

def test_same_person_click_clears_controller_gap_latch():
 c=selected();assert c.track(1) is None
 c.receive(observation(2),1.1)
 assert c.track(1.1) is None and c.requires_reselection
 select_command(c,dict(id=7,epoch='camera'),1.1)
 assert not c.requires_reselection and c.track(1.1)['id']==7
 assert c.mode=='hold'  # reselect is not an implicit follow command

def test_duplicate_click_without_gap_preserves_follow():
 c=selected();select_command(c,dict(id=7,epoch='camera'),.1)
 assert c.mode=='follow' and not c.requires_reselection

@pytest.mark.parametrize('action', [dict(id=8,epoch='camera'),dict(id=7,epoch='old')])
def test_bad_click_cannot_clear_latch(action):
 c=selected();c.track(1);c.receive(observation(2),1.1)
 with pytest.raises(ValueError):select_command(c,action,1.1)
 assert c.requires_reselection

def test_coasting_click_cannot_clear_latch():
 c=selected();c.track(1);c.receive(observation(2,coasting=True),1.1)
 with pytest.raises(ValueError):select_command(c,dict(id=7,epoch='camera'),1.1)
 assert c.requires_reselection

def test_click_on_stale_snapshot_cannot_restore_authority():
 c=selected();c.track(1)
 select_command(c,dict(id=7,epoch='camera'),1.1)
 assert c.requires_reselection and c.mode=='hold'
