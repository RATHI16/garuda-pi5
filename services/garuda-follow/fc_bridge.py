#!/usr/bin/env python3
"""Flight-controller bridge: forwards the camera computer's follow command over MAVLink/UDP.

It sends two things only: its own HEARTBEAT, and SET_POSITION_TARGET_LOCAL_NED carrying a
body-frame forward speed and a yaw rate (sideways and vertical always zero). It never arms,
disarms, changes mode or writes a parameter; the pilot switches to offboard. Whether the RC
can take the vehicle back, and what happens when the stream stops, depend on the flight
controller's own configuration, which this program neither reads nor changes. It streams
nothing until an operator opens a stream session ("Flight controller: hold" on the camera
page); from then on it streams ZERO setpoints even with no person granted, so it must be the
only offboard publisher. Losing the flight controller, or a stall of this program, ends the
session: the stream does not come back by itself. See fc_gate.py for when a command is forwarded and docs/FC_BRIDGE.md for use.

It refuses to start without an explicit statement of what is at the other end:
  simulator          GARUDA_SIM=1        and --simulator   (loopback addresses only)
  flight controller  GARUDA_FC_BRIDGE=1  and --real-flight-controller --receive-only
and, in both cases, the one flight controller it may talk to: --fc-host and --fc-sysid.
"""
import argparse
import ipaddress
import json
import math
import os
import socket
import sys
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mavlink_min as mav
from fc_gate import FcGate, FcLink

RATE_HZ = 20.0
STATUS_PERIOD_S = 0.25
MAX_TICK_GAP_S = 0.25          # a control loop that paused longer than this loses its grant before sending again
MAX_DATAGRAMS_PER_TICK = 50    # a flood of UDP cannot starve the control loop,
MAX_RECEIVE_S = 0.02           # by count or by time, whichever comes first


def _loopback(host):
    try:
        return ipaddress.IPv4Address(host).is_loopback
    except ipaddress.AddressValueError:
        return False


def endpoint_kind(args, env):
    """"simulator" or "flight controller"; raises SystemExit unless exactly one is stated in
    both the environment and the command line and the one flight controller is pinned."""
    sim = env.get("GARUDA_SIM") == "1" and args.simulator
    real = env.get("GARUDA_FC_BRIDGE") == "1" and args.real_flight_controller
    if args.simulator and args.real_flight_controller or env.get("GARUDA_SIM") == "1" and env.get("GARUDA_FC_BRIDGE") == "1":
        raise SystemExit("REFUSED: state either the simulator or the real flight controller, not both.")
    if not (sim or real):
        raise SystemExit("REFUSED: needs GARUDA_SIM=1 with --simulator, or GARUDA_FC_BRIDGE=1 with "
                         "--real-flight-controller. This program sends offboard setpoints.")
    if not args.fc_host or type(args.fc_sysid) is not int or not 1 <= args.fc_sysid <= 255:
        raise SystemExit("REFUSED: --fc-host and --fc-sysid (1..255) must name the one flight controller.")
    listen_host = args.listen.rsplit(":", 1)[0]
    if sim and not (_loopback(args.fc_host) and _loopback(listen_host)):
        raise SystemExit("REFUSED: simulator mode talks to loopback only (--listen 127.x and --fc-host 127.x), "
                         "so a real flight controller on the network can never be reached by it.")
    if real and _loopback(args.fc_host):
        raise SystemExit("REFUSED: a loopback --fc-host is a simulator; use GARUDA_SIM=1 --simulator.")
    if real and not args.receive_only:
        raise SystemExit("REFUSED: physical FC readiness checks are pending. Only --receive-only is available.")
    return "simulator" if sim else "flight controller"


class Bridge:
    """One control tick: read heartbeats, decide, send. Socket and clock are injected for tests."""
    def __init__(self, sock, sysid, host, receive_only=False, clock=time.monotonic, label=""):
        self.sock, self.receive_only, self.clock, self.label = sock, receive_only, clock, label
        self.link, self.gate = FcLink(sysid, host), FcGate()
        self.simulator_endpoint = _loopback(host)
        self.seq, self.last_heartbeat, self.sent, self.last, self.last_tick = 0, -math.inf, 0, None, None

    def _send(self, packet, address):
        self.sock.sendto(packet, address)
        self.seq = (self.seq + 1) & 0xFF

    def receive(self, started):
        for _ in range(MAX_DATAGRAMS_PER_TICK):
            if not 0 <= self.clock() - started <= MAX_RECEIVE_S:
                return
            try:
                data, address = self.sock.recvfrom(4096)
            except (BlockingIOError, socket.timeout):
                return
            now = self.clock()
            for msg in mav.parse(data):
                self.link.note(msg, now, address)            # FcLink ignores every host but the pinned one

    def tick(self, snapshot, age_ms, progress_ms):
        started = self.clock()
        stalled = self.last_tick is not None and not 0 <= started - self.last_tick <= MAX_TICK_GAP_S
        self.last_tick = started
        self.link.authority_loss = None
        self.receive(started)
        now = self.clock()                                   # fresh time for the freshness and send decisions
        spent = now - started
        if not 0 <= spent <= MAX_TICK_GAP_S:                 # this very tick took too long: the session does not survive it
            stalled = True
            spent = max(spent, 0.0)
        # The camera state was aged by the caller at the start of the tick; time spent here
        # since then makes it older, and it is judged at the moment of sending.
        if isinstance(age_ms, (int, float)) and isinstance(progress_ms, (int, float)):
            age_ms, progress_ms = age_ms + spent * 1000, progress_ms + spent * 1000
        fc = self.link.view(now)
        # Several heartbeats can arrive in one read, including a disarm followed by a
        # re-arm. Consume this tick's request even if the last heartbeat looks healthy.
        authority_loss = "bridge control loop stalled" if stalled else self.link.authority_loss
        out = self.gate.step(snapshot, age_ms, progress_ms, None if authority_loss else fc,
                             authority_loss or self.link.problem(now), now)
        if self.receive_only or not self.simulator_endpoint:
            self.gate.end_session("bridge is receive-only: nothing is sent")
            out = dict(self.gate._out(), state="refused" if out["generation"] is not None else "ready",
                       reason="bridge is receive-only: nothing is sent")
        elif out["stream"]:
            address = fc["address"]
            if now - self.last_heartbeat >= 1.0:
                self._send(mav.heartbeat(self.seq, fc["sysid"]), address)
                self.last_heartbeat = now
            self._send(mav.body_velocity(self.seq, fc["sysid"], mav.MAV_COMP_ID_ONBOARD_COMPUTER, int(now * 1000),
                                         fc["sysid"], mav.MAV_COMP_ID_AUTOPILOT1, out["forward_vel"], out["yaw_rate"]), address)
            self.sent += 1
        self.last = dict(out, reason=(self.label + ": " if self.label else "") + out["reason"],
                         fc=None if fc is None else {k: fc[k] for k in ("sysid", "offboard", "armed")},
                         setpoints_sent=self.sent, attitude_max_gap_ms=round(self.link.max_gap * 1000, 1))
        return self.last


class PiState:
    """Reads the detector's state in its own thread and reports back what the bridge is doing.
    No network call ever happens in the control tick."""
    def __init__(self, url, opener=urlopen):
        self.url, self.opener, self.lock = url.rstrip("/"), opener, threading.Lock()
        self.data, self.received, self.progress, self.seq = None, -math.inf, -math.inf, None
        self.report, self.report_at, self.error, self.last_status = None, -math.inf, None, -math.inf
        self.cursor = -1

    def read_once(self):
        start = time.monotonic()
        with self.opener(self.url + "/api/state?" + urlencode({"after": self.cursor}), timeout=1.5) as r:
            data = json.load(r)
        now = time.monotonic()
        elapsed = (now - start) * 1000
        waited = data.pop("waited_ms", None)                 # time the detector held the request is inside its own age
        if isinstance(waited, (int, float)) and not isinstance(waited, bool) and math.isfinite(waited) and 0 <= waited <= elapsed:
            elapsed -= waited
        age = data.get("age_ms")
        data["age_ms"] = age + elapsed if isinstance(age, (int, float)) and not isinstance(age, bool) else None
        frame = data.get("frame") or {}
        key = (frame.get("epoch"), frame.get("seq"), (data.get("stats") or {}).get("instance"))
        with self.lock:
            if key != self.seq:
                self.seq, self.progress = key, now
            self.data, self.received = data, now
        if type(frame.get("seq")) is int:
            self.cursor = frame["seq"]

    def current(self, now):
        """(snapshot, age_ms, progress_ms) for the control tick."""
        with self.lock:
            data, received, progress = self.data, self.received, self.progress
        if data is None or not isinstance(data.get("age_ms"), (int, float)):
            return data, None, None
        return data, data["age_ms"] + (now - received) * 1000, (now - progress) * 1000

    def publish(self, report, now):
        with self.lock:
            self.report, self.report_at = dict(report), now

    def take_error(self, key):
        with self.lock:
            error, self.error = self.error, None
        return error[2] if error is not None and error[:2] == key else None

    def send_status(self, now):
        with self.lock:
            report, at = self.report, self.report_at
        if report is None or now - self.last_status < STATUS_PERIOD_S:
            return
        self.last_status = now
        if now - at > 0.2:                                   # the control loop stopped: never keep saying "sending",
            report = dict(report, state="refused", stream=False, reason="bridge control loop stalled")
            with self.lock:                                  # and make the loop give up its grant when it comes back
                self.error = (report.get("instance"), report.get("generation"), "bridge control loop stalled")
        bound = report.get("generation") is not None and report.get("instance")
        params = {"target": "fc", "state": report["state"] if bound or report["state"] == "ready" else "ready",
                  "reason": report.get("reason") or ""}
        if bound:                                            # whether setpoints (zero or not) are flowing, said explicitly
            params.update(generation=report["generation"], instance=report["instance"],
                          stream="1" if report.get("stream") is True else "0")
        try:
            with self.opener(Request(self.url + "/api/transmit_status?" + urlencode(params), data=b""), timeout=0.4) as r:
                ack = json.load(r)
            if ack.get("ok") is not True:
                raise ValueError("status refused")
            if bound and (ack.get("instance") != report["instance"] or ack.get("generation") != report["generation"]):
                raise ValueError("request changed during report")
        except Exception as exc:
            with self.lock:
                self.error = (report.get("instance"), report.get("generation"), "camera computer did not accept the status: " + str(exc))


def main(argv=None, env=os.environ):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--simulator", action="store_true", help="the MAVLink endpoint is PX4 SITL (needs GARUDA_SIM=1)")
    p.add_argument("--real-flight-controller", action="store_true", help="the MAVLink endpoint is a real flight controller (needs GARUDA_FC_BRIDGE=1)")
    p.add_argument("--source", default="http://127.0.0.1:8098", help="the detector's address")
    p.add_argument("--listen", required=True, help="local UDP address the flight controller sends MAVLink to, HOST:PORT")
    p.add_argument("--fc-host", required=True, help="the one address MAVLink is accepted from and sent to")
    p.add_argument("--fc-sysid", required=True, type=int, help="the flight controller's MAVLink system id; any other is refused")
    p.add_argument("--receive-only", action="store_true", help="read heartbeats and report; never send MAVLink (bring-up stage 3)")
    args = p.parse_args(argv)
    kind = endpoint_kind(args, env)
    host, port = args.listen.rsplit(":", 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, int(port)))
    sock.setblocking(False)
    bridge = Bridge(sock, args.fc_sysid, args.fc_host, args.receive_only,
                    label="SIM" if kind == "simulator" else "REAL FC")
    pi = PiState(args.source)
    running = threading.Event()
    running.set()

    def reader():
        while running.is_set():
            try:
                pi.send_status(time.monotonic())
                pi.read_once()
            except Exception:
                time.sleep(0.2)                              # the snapshot simply ages; the gate holds on its age
    threading.Thread(target=reader, daemon=True).start()
    print(f"fc_bridge: endpoint is a {kind}: host {args.fc_host}, system {args.fc_sysid}; listening on {args.listen}; "
          + ("RECEIVE ONLY, sends nothing" if args.receive_only else
             "streams nothing until an operator opens a stream session, then ZERO setpoints, and the follow command only under a grant"),
          flush=True)
    period, shown, next_tick = 1.0 / RATE_HZ, None, time.monotonic()
    summary_at = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            error = pi.take_error(bridge.gate.observed)
            if error:
                (bridge.gate.end_session if "stalled" in error else bridge.gate.drop)(error)
            out = bridge.tick(*pi.current(now))
            pi.publish(out, now)
            line = (out["state"], out["reason"], out["fc"])
            if line != shown:
                shown = line
                print(time.strftime("%H:%M:%S"), json.dumps({"state": out["state"], "reason": out["reason"], "fc": out["fc"],
                                                              "setpoints_sent": out["setpoints_sent"],
                                                              "attitude_max_gap_ms": out["attitude_max_gap_ms"]}), flush=True)
            if now - summary_at >= 5.0:                      # bring-up evidence: how regular the fast telemetry is
                summary_at = now
                print(time.strftime("%H:%M:%S"), json.dumps({"summary": True, "fc": out["fc"], "setpoints_sent": out["setpoints_sent"],
                                                              "attitude_max_gap_ms": out["attitude_max_gap_ms"]}), flush=True)
            next_tick += period
            delay = next_tick - time.monotonic()
            if delay <= 0:
                next_tick = time.monotonic()                 # missed periods are skipped, never sent in a burst
            else:
                time.sleep(delay)
    except KeyboardInterrupt:
        pass
    finally:
        running.clear()
        sock.close()


if __name__ == "__main__":
    main()
