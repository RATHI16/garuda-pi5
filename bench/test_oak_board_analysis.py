import numpy as np
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from oak_board_analysis import analyse

def test_flat_z_and_radial_synthetic_surfaces_choose_different_hypotheses():
    h=w=160;g=dict(fx=100.,fy=100.,cx=80.,cy=80.)
    flat=np.full((h,w),2000,np.uint16)
    r=analyse(flat,g,[0,0,w,h]);assert r['z_hypothesis']['residual_rms_all_m']<1e-10
    assert r['radial_hypothesis']['residual_rms_all_m']>.01
    v,u=np.mgrid[:h,:w];radial=np.rint(2000*np.sqrt(1+((u-80)/100)**2+((v-80)/100)**2)).astype(np.uint16)
    r=analyse(radial,g,[0,0,w,h]);assert r['radial_hypothesis']['residual_rms_all_m']<.001
    assert r['z_hypothesis']['residual_rms_all_m']>.01
