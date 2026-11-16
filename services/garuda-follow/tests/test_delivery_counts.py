from delivery_counts import DeliveryCounts

def result(seq=1,epoch='a'):return dict(frame=dict(seq=seq,epoch=epoch),metrics={'frames':seq})
def test_distinct_results_not_tick_rate_and_skips_visible():
 c=DeliveryCounts()
 for seq in [1,2,3]:c.frame('reader_frames',0,result(seq))
 for seq in [1,3,3,3]:c.frame('controller_frames',0,result(seq))
 for _ in range(5):c.frame('evaluated_frames',0,result(3))
 c.overwrite();s=c.snapshot()
 assert (s['reader_frames'],s['controller_frames'],s['evaluated_frames'],s['queue_overwrites'])==(3,2,1,1)
 assert s['producer']['processed']==3

def test_invalidations_not_counted_and_restart_is_distinct():
 c=DeliveryCounts();c.frame('reader_frames',0,{})
 c.frame('reader_frames',0,result());c.frame('reader_frames',0,result(epoch='new'))
 c.frame('reader_frames',1,result(epoch='new'))
 assert c.snapshot()['reader_frames']==3
