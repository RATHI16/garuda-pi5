"""Read PX4 companion-link events recorded by the lab's local QGroundControl."""
from datetime import datetime, timezone
import mmap
import os
from pathlib import Path
import struct
import threading

# PX4 event group 1, IDs from px4_sitl_default/events/all_events.json.
EVENTS = {0x01000000 | 11123101: 'Mission computer lost',
          0x01000000 | 10716939: 'Onboard controller regained'}
LOCK = threading.Lock()


def records(data):
    """Last reception of each EVENT from the lab autopilot (system 4, component 1)."""
    rows = []
    for event_id, label in EVENTS.items():
        marker = b'\x04\x01\x9a\x01\x00' + struct.pack('<I', event_id)
        end = len(data)
        while (at := data.rfind(marker, 0, end)) >= 0:
            end = at
            start = at - 5
            if start < 8 or data[start] != 0xfd:
                continue
            size = data[start + 1]
            stop = start + 10 + size
            if not 10 <= size <= 53 or stop + 2 > len(data):
                continue
            crc = 0xffff
            for byte in data[start+1:stop] + bytes([160]):  # MAVLink EVENT CRC extra
                t = (crc & 255) ^ byte
                t = (t ^ (t << 4)) & 255
                crc = (crc >> 8) ^ (t << 8) ^ (t << 3) ^ (t >> 4)
            if crc != struct.unpack_from('<H', data, stop)[0]:
                continue
            received_us = struct.unpack_from('>Q', data, start - 8)[0]
            try:
                received = datetime.fromtimestamp(received_us / 1e6, timezone.utc)
            except (OSError, OverflowError, ValueError):
                continue
            rows.append(dict(event=label, received=received.isoformat(timespec='milliseconds'),
                             boot_ms=struct.unpack_from('<I', data, start+14)[0]))
            break
    return sorted(rows, key=lambda row: row['received'])


def history():
    # Only inspect the active QGC simulator process. No client-supplied log paths.
    if not LOCK.acquire(blocking=False):
        raise ValueError('A QGC log read is already in progress.')
    try:
        logs = []
        for comm in Path('/proc').glob('[0-9]*/comm'):
            try:
                if comm.read_text().strip() != 'QGroundControl':
                    continue
                proc = comm.parent
                if b'GARUDA_SIM=1' not in (proc/'environ').read_bytes().split(b'\0'):
                    continue
                for fd in (proc/'fd').iterdir():
                    name = Path(os.readlink(fd))
                    if name.match('FlightData*.mavlink') and fd.is_file():
                        logs.append(fd)
            except OSError:
                continue
        if len(logs) != 1:
            raise ValueError('A single active QGC simulator recording could not be identified.')
        with logs[0].open('rb') as log:
            if not os.fstat(log.fileno()).st_size:
                return []
            with mmap.mmap(log.fileno(), 0, access=mmap.ACCESS_READ) as data:
                return records(data)
    finally:
        LOCK.release()
