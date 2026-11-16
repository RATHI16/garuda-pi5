import pytest
from sim_detector_server import validate_request,SIZE

def test_exact_envelope():validate_request(SIZE,'request-1')
@pytest.mark.parametrize('size,rid',[(0,'id'),(SIZE-1,'id'),(SIZE+1,'id'),(SIZE,''),(SIZE,'x'*65)])
def test_bad_envelopes(size,rid):
 with pytest.raises(ValueError):validate_request(size,rid)
