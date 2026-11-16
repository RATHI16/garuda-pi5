"""Isolated SITL companion presence. Heartbeats only; never commands or setpoints.

Owned by the ROS controller and driven by its control tick, not an independent timer.
Reserves the same local port as the alternative MAVLink bridge so both cannot coexist.
"""
import math
import os
import socket
from mavlink_min import heartbeat

class SitlPresence:
    def __init__(self, sock=None):
        if os.environ.get('GARUDA_SIM') != '1' or os.environ.get('ROS_DOMAIN_ID') != '42':
            raise ValueError('SITL presence requires isolated simulation')
        self.sock = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(('127.0.0.1', 14543))
            self.sock.setblocking(False)
        except Exception:
            self.sock.close()
            raise
        self.last = None
        self.count = 0
        self.error = None

    def tick(self, now, fresh):
        if not fresh or not math.isfinite(now):
            return
        if self.last is not None and 0 <= now - self.last < 1.0:
            return
        try:
            self.sock.sendto(heartbeat(self.count, 4), ('127.0.0.1', 14583))
            self.last = now
            self.count += 1
            self.error = None
        except OSError as exc:
            self.error = type(exc).__name__

    def state(self):
        return dict(transport='SITL loopback MAVLink heartbeat only', sent=self.count, error=self.error)

    def close(self):
        self.sock.close()
