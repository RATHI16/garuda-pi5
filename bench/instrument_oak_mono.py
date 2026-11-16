#!/usr/bin/env python3
"""Create a separate instrumented copy of the deployed mono; refuse unknown anchors."""
from pathlib import Path
import argparse

def instrument(text):
    if 'from mono_timing import Progress' in text:return text
    replacements=[
        ('from hailo_platform import VDevice, FormatType','from mono_timing import Progress\n_progress = Progress()\nfrom hailo_platform import VDevice, FormatType'),
        ('_frame.update(img=img, seq=_frame["seq"]+1, t=meta.t_capture_ns/1e9,','_frame.update(img=img, received_ns=time.monotonic_ns(), seq=_frame["seq"]+1, t=meta.t_capture_ns/1e9,'),
        ('capture_t = _frame["t"]; capture_source = _frame["source"]; capture_epoch = _frame["epoch"]','capture_t = _frame["t"]; capture_source = _frame["source"]; capture_epoch = _frame["epoch"]\n            received_ns = _frame.get("received_ns")'),
        ('        last_seq = seq\n','        last_seq = seq\n        inference_start_ns = time.monotonic_ns()\n'),
        ('        _stats_tick(tracks_full)\n','        _stats_tick(tracks_full)\n        _progress.record(seq,capture_epoch,int(capture_t*1e9),received_ns,inference_start_ns)\n'),
        ('"stats": {"fps":','"stats": {"timing": _progress.snapshot(), "fps":')]
    for old,new in replacements:
        if text.count(old)!=1:raise ValueError('Unexpected source structure: '+old)
        text=text.replace(old,new,1)
    compile(text,'instrumented-mono','exec')
    return text

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('output');a=p.parse_args()
    src,dst=Path(a.source),Path(a.output)
    if src.resolve()==dst.resolve():raise SystemExit('refusing in-place modification')
    dst.write_text(instrument(src.read_text()))
