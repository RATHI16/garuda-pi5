import math
from pose_history import PoseHistory,PC_BOOT_ID

def frame(ns):return dict(ingress_monotonic_ns=ns,clock='pc-monotonic',clock_boot_id=PC_BOOT_ID)
def test_nearest_normalizes_and_reports_signed_skew():
 h=PoseHistory();h.add(1_000_000_000,[1,2,3],[2,0,0,0]);h.add(1_020_000_000,[2,2,3],[1,0,0,0])
 m=h.match(frame(1_018_000_000),1_050_000_000)
 assert m.position==(2,2,3) and m.quaternion==(1,0,0,0) and m.skew_ns==2_000_000
 assert h.match(frame(800_000_000),1_050_000_000) is None

def test_mismatched_clock_future_and_missing_stamp_rejected():
 h=PoseHistory();h.add(1_000_000_000,[0,0,0],[1,0,0,0])
 for f in [{},frame(1_100_000_000),dict(frame(1_000_000_000),clock_boot_id='other'),dict(frame(1_000_000_000),clock='sim')]:
  assert h.match(f,1_010_000_000) is None

def test_reset_clears_old_history_and_bad_pose_rejected():
 h=PoseHistory();h.add(1_000_000_000,[0,0,0],[1,0,0,0],100)
 h.add(2_000_000_000,[1,0,0],[1,0,0,0],1)
 assert h.match(frame(1_000_000_000),2_010_000_000) is None
 assert not h.add(2_100_000_000,[math.nan,0,0],[1,0,0,0])
 assert not h.add(2_100_000_000,[0,0,0],[0,0,0,0])

def test_history_bounded():
 h=PoseHistory(horizon_ns=100)
 for i in range(1000):h.add(i,[0,0,0],[1,0,0,0])
 assert len(h.samples)==101
