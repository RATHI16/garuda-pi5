import pytest
from geometry import from_transform,validate_geometry
class Transform:
    def getSize(self): return (1280,720)
    def getIntrinsicMatrix(self): return [[975.,0.,642.],[0.,974.,351.],[0.,0.,1.]]
    def getDistortionCoefficients(self): return [0.]*8

def test_delivered_not_source_matrix():
    g=from_transform(Transform(),'epoch','cal')
    assert (g['fx'],g['fy'],g['cx'],g['cy'])==(975.,974.,642.,351.)
    assert not g['verified']

@pytest.mark.parametrize('key,value',[('epoch','old'),('fx',float('nan')),('width',640),('cx',-1),('verified',True)])
def test_reject_mismatched_geometry(key,value):
    g=from_transform(Transform(),'epoch','cal');g[key]=value
    with pytest.raises(ValueError):validate_geometry(g,epoch='epoch',calibration_id='cal',width=1280,height=720)
