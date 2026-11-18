from qgc_link_history import records


def test_reads_timestamped_sitl_events_and_ignores_corrupt_and_incomplete_repeats():
    # A QGC telemetry record (8-byte receipt time followed by a MAVLink 2 EVENT).
    packet = bytes.fromhex('00066061f61d3760 fd0d0000f104019a01009db9a901a488fc00320000002266cf')
    good = records(packet)
    assert len(good) == 1
    assert good[0]['event'] == 'Mission computer lost'
    assert good[0]['boot_ms'] == 16550052
    assert good[0]['received'].endswith('+00:00')
    assert records(packet + packet[:-1]) == good
    assert records(packet[:-1] + b'\0') == []
    assert records(packet.replace(b'\xf1\x04\x01', b'\xf1\x01\x01')) == []
