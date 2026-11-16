from pathlib import Path
import pytest
from bench.instrument_oak_mono import instrument

def test_instrumentation_idempotent_and_compile():
 p=Path(__file__).resolve().parents[1]/'hosts/pi5/garuda_mono.py'
 s=p.read_text();assert instrument(s)==s
 compile(s,str(p),'exec')

def test_unknown_source_refused():
 with pytest.raises(ValueError):instrument('print("unknown")')
