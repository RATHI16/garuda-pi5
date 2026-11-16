from oak_mono_capture_bench import summarise,pair_skew,stream_names,thread_cores

def frames(seqs,period_ns=66_666_667,offset=0,size=256000):
    return [(s,s*period_ns+offset,s*period_ns+5_000_000_000,s*period_ns+5_004_000_000,size) for s in seqs]

def test_stream_names():
    assert stream_names('small')==['rgb','left_640x400','right_640x400']
    assert stream_names('both')==['rgb','left_640x400','left_1280x800','right_640x400','right_1280x800']

def test_summary_counts_missing_and_settle():
    rows={'left_640x400':frames([0,1,2,3,5,6])}
    settle=2*66_666_667+5_004_000_000
    s=summarise(rows,settle,1.0)['left_640x400']
    # Frames 0 and 1 fall in the settle period; sequence 4 never arrived.
    assert s['frames']==4 and s['missing']==1 and s['not_increasing']==0
    assert abs(s['host_to_dequeue_ms']['p50']-4)<1e-6 and s['bytes_per_frame']==256000

def test_summary_empty_stream():
    s=summarise({'rgb':[]},0,1.0)['rgb']
    assert s['frames']==0 and s['interval_ms'] is None and s['bytes_per_frame'] is None

def test_pair_skew_matches_by_sequence():
    rows={'l':frames([1,2,3]),'r':frames([2,3,4],offset=2_000_000)}
    k=pair_skew(rows,'l','r',0)
    assert k['n']==2 and abs(k['max']-2)<1e-6
    assert pair_skew(rows,'l','missing',0) is None

def test_thread_cores_groups_by_name_and_drops_idle():
    before={('1','main'):100,('2','xlink'):0}
    after={('1','main'):300,('2','xlink'):50,('3','xlink'):150,('4','idle'):0}
    assert thread_cores(before,after,2.0)=={'main':1.0,'xlink':1.0}
