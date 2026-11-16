"""Prepare a telemetry-only upgrade for the known deployed RGB demo. No installer."""
import argparse,hashlib
from pathlib import Path
BASE_SHA='555bae1d210249d7ea3a156e8a9dae41947a8a91e388df94ed58a88935b46f1c'

def patch(data):
 if hashlib.sha256(data).hexdigest()!=BASE_SHA:raise ValueError('unexpected deployed source; refuse blind patch')
 text=data.decode()
 replacements=[
  ('_stats = {"ids": set(),', '_stats = {"instance": uuid.uuid4().hex, "inferences": 0, "ids": set(),'),
  ('                dets = active.infer(img)                # detector EVERY frame -> feeds the tracker', '                dets = active.infer(img)                # detector EVERY frame -> feeds the tracker\n                _stats["inferences"] += 1'),
  ('"stats": {"fps": round(_stats["fps"], 1),', '"stats": {"instance": _stats["instance"], "inferences": _stats["inferences"], "fps": round(_stats["fps"], 1),')]
 for old,new in replacements:
  if text.count(old)!=1:raise ValueError('expected telemetry anchor missing or repeated')
  text=text.replace(old,new)
 compile(text,'garuda_mono.py','exec')
 return text.encode()

def main():
 p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
 data=patch(a.source.read_bytes())
 with a.output.open('xb') as f:f.write(data)
 print(hashlib.sha256(data).hexdigest())
if __name__=='__main__':main()
