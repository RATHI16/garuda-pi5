import json
from oak_receiving_check import HANDOUTS, inspect


def test_receiving_check_requires_fresh_rgb_and_byte_matching_handouts():
    owner = dict(epoch='a', raw_seq=1, camera_compute='capture-and-encoding-only',
                 depth_backend='pi-sgbm', stereo_enabled=True, measurement_qualified=False)
    def read(url):
        if url.endswith('/health'):
            owner['raw_seq'] += 1
            return json.dumps(owner).encode()
        if url.endswith('/api/state'):
            return b'{"frame":{"source":"oak-rgb"},"stats":{"inferences":123}}'
        return (HANDOUTS/url.rsplit('/',1)[1]).read_bytes()
    assert inspect('pi', read, lambda _: None)['shipping_profile_observed']
    def changed(url):
        if url.endswith('/health'):
            owner['epoch'] += 'x'
        return b'different' if url.endswith('.pdf') else read(url)
    failed = inspect('pi', changed, lambda _: None)
    assert not failed['checks']['same_epoch_rgb_advanced']
    assert not failed['checks']['oak-targets-a4.pdf']
    assert not failed['shipping_profile_observed']


def test_unreachable_station_reports_each_missing_component():
    def missing(url):
        raise OSError('offline')
    result=inspect('pi', missing, lambda _:None)
    assert not result['shipping_profile_observed'] and len(result['errors'])==6
