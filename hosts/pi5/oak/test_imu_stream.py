from datetime import timedelta
from imu_stream import IMUHistory
class Report:
    sequence=10;x=1.;y=2.;z=3.
    def getTimestamp(self):return timedelta(seconds=5)
    def getTimestampDevice(self):return timedelta(seconds=1)

def test_bounded_cursor_restart_and_overrun():
    h=IMUHistory('e',capacity=2)
    for _ in range(4):h.add('accel',Report(),5_100_000_000)
    assert h.snapshot(0,'e')['cursor_overrun']
    assert [r['id'] for r in h.snapshot(2,'e')['samples']]==[3]
    assert h.snapshot(500,'old')['epoch_changed']
    assert len(h.snapshot(500,'old')['samples'])==2
    assert h.health(7_000_000_000)['accel']['stale']
    assert not h.health(5_100_000_000)['accel']['stale']
