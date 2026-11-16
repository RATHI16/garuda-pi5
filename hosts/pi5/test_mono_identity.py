"""Identity owner: one selected person across track-ID changes, failing closed."""
import numpy as np
from mono_identity import IdentityOwner

rng=np.random.default_rng(3)
def person(seed):
    # A view of one person: the same direction plus pose/lighting noise (cosine about 0.96 between views).
    base=np.random.default_rng(seed).normal(size=512);return lambda:base+0.3*rng.normal(size=512)
ME,OTHER,TWIN=person(1),person(2),None
def p(tid,emb,pos=None):return {'id':tid,'emb':emb,'pos':pos}
def locked(owner=None,frames=40):
    o=owner or IdentityOwner();o.select(7,0.)
    for n in range(frames):o.update(n/15,[p(7,ME())])
    return o

def test_gallery_collects_different_views_only_while_the_person_is_visible():
    o=locked();assert 1<=len(o.gallery)<=o.gallery_cap and o.summary(3.)['state']=='tracking' and o.summary(3.)['target']==7
    n=len(o.gallery);o.update(3.,[p(9,ME())]);assert len(o.gallery)==n                    # nothing is learned from a candidate

def test_same_person_under_a_new_id_is_bound_after_confirmation_and_announced():
    o=locked();t=40/15;out=[]
    for n in range(8):out.append(o.update(t+n/15,[p(9,ME())]))
    assert [d['state'] for d in out[:3]]==['confirming']*3 and out[2]['target']==7       # not on the first frames
    done=next(d for d in out if d['target']==9)
    assert done['rebound_from']==7 and done['state']=='tracking' and done['rebound_score']>=.8 and o.binds==1
    assert o.update(t+4.,[p(9,ME())])['rebound_from'] is None                               # the announcement expires

def test_a_different_person_is_never_bound():
    o=locked()
    for n in range(60):d=o.update(40/15+n/15,[p(9,OTHER())])
    assert d['target']==7 and d['state']=='searching' and o.binds==0

def test_someone_visible_with_the_selected_person_can_never_become_them():
    o=IdentityOwner();o.select(7,0.)
    for n in range(40):o.update(n/15,[p(7,ME()),p(8,ME())])                                 # 8 even looks identical
    for n in range(60):d=o.update(40/15+n/15,[p(8,ME())])
    assert d['target']==7 and o.binds==0

def test_two_similar_candidates_mean_hold():
    o=locked()
    for n in range(60):d=o.update(40/15+n/15,[p(9,ME()),p(10,ME())])
    assert d['target']==7 and d['state']=='ambiguous' and o.binds==0

def test_missing_embedding_refuses_and_memory_expires():
    o=locked()
    for n in range(30):d=o.update(40/15+n/15,[p(9,None)])
    assert d['target']==7 and d['state']=='searching' and o.binds==0
    d=o.update(40/15+31.,[p(9,ME())]);assert d['state']=='expired' and d['target']==7
    fresh=IdentityOwner();fresh.select(7,0.);assert fresh.update(.1,[p(9,ME())])['state'] in ('expired','no_reference')

def test_position_gate_rejects_a_look_alike_too_far_away_and_unknown_position_does_not_block():
    o=IdentityOwner();o.select(7,0.)
    for n in range(40):o.update(n/15,[p(7,ME(),pos=(0.,2.))])
    for n in range(20):d=o.update(40/15+n/15,[p(9,ME(),pos=(0.,9.))])                       # 7 m away within a second
    assert d['target']==7 and 'too far' in d['reason']
    for n in range(20):d=o.update(4.+n/15,[p(9,ME(),pos=None)])
    assert d['target']==9

def test_a_new_selection_starts_from_nothing_and_release_clears():
    o=locked();o.select(11,5.);assert o.gallery==[] and o.excluded==set() and o.tid==11
    o.release();assert o.update(6.,[p(11,ME())])=={'target':None,'confirmed':False,'state':'idle','reason':'','gallery':0,'binds':0,'candidate':None,'rebound_from':None,'rebound_score':None}

def test_candidate_must_win_consecutively():
    o=locked();t=40/15
    o.update(t,[p(9,ME())]);o.update(t+.07,[p(9,ME())]);o.update(t+.14,[p(9,OTHER())])    # interrupted
    d=o.update(t+.2,[p(9,ME())]);assert d['target']==7


# ---- a track ID is not an identity (astra-0062 finding 3) -----------------------------------
def test_reviewers_reproduction_same_id_with_an_unrelated_appearance_is_not_accepted_or_learned():
    o=IdentityOwner();o.select(7,0.);o.update(0.,[p(7,[1.,0.])]);o.update(.5,[])
    d=o.update(1.,[p(7,[0.,1.])])
    assert d['confirmed'] is False and d['state']!='tracking' and len(o.gallery)==1 and o.score([0.,1.])<.1

def test_a_different_person_reusing_the_same_id_after_time_passes_without_updates_is_held_then_rejected():
    o=locked();n=len(o.gallery);t=40/15
    out=[o.update(t+.4+k/15,[p(7,OTHER())]) for k in range(40)]          # 0.4 s unseen, then the ID returns on someone else
    assert all(d['confirmed'] is False for d in out) and out[0]['state']=='verifying'
    assert out[-1]['state']=='searching' and 7 in o.rejected and len(o.gallery)==n and o.binds==0
    # The real person, appearing under a new ID afterwards, is still recognised.
    more=[o.update(t+4+k/15,[p(7,OTHER()),p(9,ME())]) for k in range(10)]
    assert more[-1]['target']==9 and more[-1]['confirmed'] is True and o.binds==1

def test_the_same_person_returning_under_the_same_id_is_verified_not_assumed():
    o=locked();t=40/15
    out=[o.update(t+.5+k/15,[p(7,ME())]) for k in range(10)]
    assert out[0]['confirmed'] is False and out[0]['state']=='verifying'                 # not on trust
    assert out[-1]['confirmed'] is True and out[-1]['state']=='tracking' and o.binds==0 and out[-1]['rebound_from'] is None

def test_one_skipped_update_does_not_break_a_confirmed_lock():
    o=locked();t=40/15
    d=o.update(t+2/15,[p(7,ME())]);assert d['confirmed'] is True and d['state']=='tracking'

def test_an_id_switch_between_overlapping_people_is_caught_and_never_learned():
    o=locked();n=len(o.gallery);t=40/15
    out=[o.update(t+k/15,[p(7,OTHER())]) for k in range(60)]                              # continuously visible, other person's look
    assert out[0]['confirmed'] is False and out[0]['state']=='tracking'                  # an odd frame is held, the lock not yet given up
    assert all(d['confirmed'] is False for d in out[4:]) and len(o.gallery)==n and 7 in o.rejected
    assert o.score(OTHER())<.3

def test_missing_embedding_on_return_never_confirms():
    o=locked();t=40/15
    out=[o.update(t+.5+k/15,[p(7,None)]) for k in range(60)]
    assert all(d['confirmed'] is False for d in out) and o.binds==0

def test_an_incompatible_view_is_never_added_even_while_confirmed():
    o=locked();n=len(o.gallery)
    o.update(40/15,[p(7,OTHER())]);assert len(o.gallery)==n

def test_learning_needs_a_compatible_view_not_merely_a_different_one():
    o=IdentityOwner();o.select(7,0.);o.update(0.,[p(7,[1.,0.,0.])])
    o.update(.07,[p(7,[.6,.8,0.])])                                   # cosine 0.60: not contradictory, but below learn_min
    assert len(o.gallery)==1 and o.summary(.07)['confirmed'] is True
    o.update(.14,[p(7,[.7,.7141,0.])])                                # cosine 0.70: compatible and new -> kept
    assert len(o.gallery)==2
    o.update(.21,[p(7,[.99,.14,0.])])                                 # cosine 0.99: nothing new -> not kept
    assert len(o.gallery)==2


# ---- frames that are processed with nobody in them (astra-0063) ----------------------------
def test_reviewers_sequence_a_wrong_person_on_the_old_id_does_not_block_the_right_one_for_ever():
    o=IdentityOwner();o.select(7,0.);o.update(0.,[p(7,[1.,0.])])
    assert o.update(.1,[])['confirmed'] is False                               # a real processed frame with nobody in it
    out=[o.update(.1+.1*k,[p(7,[0.,1.]),p(9,[1.,0.])]) for k in range(1,51)]
    assert all(d['target']==7 and d['confirmed'] is False for d in out[:10])   # held while the old ID is in doubt
    assert 7 in o.rejected and out[-1]['target']==9 and out[-1]['confirmed'] is True and o.binds==1
    assert len(o.gallery)>=1 and o.score([0.,1.])<.1                           # the wrong look was never learned

def test_the_same_person_returning_on_the_same_id_after_empty_frames_is_verified_then_confirmed():
    o=locked();t=40/15
    for k in range(8):assert o.update(t+k/15,[])['confirmed'] is False         # 0.5 s of processed frames without them
    out=[o.update(t+8/15+k/15,[p(7,ME())]) for k in range(10)]
    assert out[0]['confirmed'] is False and out[0]['state']=='verifying'
    assert out[-1]['confirmed'] is True and out[-1]['state']=='tracking' and o.binds==0 and 7 not in o.rejected

def test_one_processed_frame_with_nobody_in_it_does_not_break_a_confirmed_lock():
    o=locked();t=40/15
    assert o.update(t,[])['confirmed'] is False
    d=o.update(t+1/15,[p(7,ME())]);assert d['confirmed'] is True and d['state']=='tracking'

def test_verification_clock_runs_only_while_the_track_is_seen_and_is_not_restarted_by_gaps():
    o=locked();t=40/15+1.0
    # A wrong person flickers on the old ID: seen for 3 frames, gone for 3, again and again.
    seen=0.;k=0;d=None
    while 7 not in o.rejected and k<400:
        present=(k//3)%2==0
        d=o.update(t+k/15,[p(7,OTHER())] if present else []);k+=1
    assert 7 in o.rejected and d['confirmed'] is False and k/15<8             # rejected despite the gaps, in bounded time
    fresh=locked();out=[fresh.update(t+k/15,[p(7,None)]) for k in range(60)]   # no evidence at all: hold, never confirm
    assert all(x['confirmed'] is False for x in out)

def test_long_absence_then_genuine_return_is_not_rejected_by_an_old_clock():
    o=locked();t=40/15
    o.update(t+.5,[p(7,None)])                                                  # one unverifiable sighting
    for k in range(60):o.update(t+.6+k/15,[])                                   # gone for 4 s
    out=[o.update(t+5+k/15,[p(7,ME())]) for k in range(10)]
    assert 7 not in o.rejected and out[-1]['confirmed'] is True


def test_one_contradictory_frame_is_held_but_does_not_lose_the_selection():
    o=locked();t=40/15
    d=o.update(t,[p(7,OTHER())]);assert d['confirmed'] is False and d['target']==7
    d=o.update(t+1/15,[p(7,ME())]);assert d['confirmed'] is True and d['state']=='tracking' and o.binds==0


def test_a_doubtful_or_unreadable_frame_does_not_rule_other_people_out():
    o=locked();t=40/15
    o.update(t,[p(7,OTHER()),p(9,ME())]);assert 9 not in o.excluded          # the lock itself looks wrong in this frame
    o.update(t+1/15,[p(7,None),p(10,ME())]);assert 10 not in o.excluded     # no reading for the lock in this frame
    o.update(t+2/15,[p(7,ME()),p(11,OTHER())]);assert 11 in o.excluded      # a trusted frame does rule the bystander out
