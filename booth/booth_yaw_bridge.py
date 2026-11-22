#!/usr/bin/env python3
"""Booth yaw bridge: yaw-only follow with an angle limit and return-to-centre, position locked at home.

v2 (2026-11-20): sends a HEADING setpoint, not a yaw rate. Flight logs of v1 showed the heading
drifting by 20+ deg while the bridge commanded rate 0 (a rate setpoint gives PX4 no heading to
hold) and overshooting the limit. Now PX4 holds the heading actively, and the heading it is told
to hold can never be outside home +/- the limit.

What it does (and nothing else):
  * Reads the camera computer's on-drone command (/api/state -> onboard), validated with
    live_onboard.validate exactly like the existing bridges (vehicle mount, fresh frame, same
    detector/epoch, selected person seen confidently, identity confirmed when re-ID is on).
  * Reads the flight controller's ATTITUDE, LOCAL_POSITION_NED and HEARTBEAT over MAVLink/UDP.
  * Streams SET_POSITION_TARGET_LOCAL_NED at 20 Hz: POSITION + YAW ANGLE (type mask 2552).
      - before OFFBOARD: the current position and current heading (bumpless switch)
      - in OFFBOARD: position locked where OFFBOARD was entered (home). The heading setpoint
        starts at the home heading and moves with the camera's yaw-rate command, and
          * it is clamped to home +/- --yaw-limit-deg,
          * it never runs more than --max-lead-deg ahead of the real heading,
          * person lost for --lost-s, or pushing past the limit (or the vehicle pushed past it)
            for --beyond-s  ->  RETURN: the setpoint moves back to home at --return-rate,
          * after returning, tracking resumes only when the person is near the image centre,
          * no valid camera command: the setpoint stays where it is (PX4 holds that heading).
  * Never arms, disarms, changes mode or writes a parameter. The pilot switches to OFFBOARD
    and can leave it at any time (RC mode switch; sticks if COM_RC_OVERRIDE has bit 1).
  * FC telemetry stale (> 0.3 s) or Ctrl+C: it stops sending, and PX4's own offboard-loss
    failsafe (COM_OF_LOSS_T / COM_OBL_RC_ACT) takes over.
  * Keeps one local viewer on the detector open (garuda_mono only detects while viewed).

Like fc_bridge.py it refuses to start unless the endpoint is stated twice:
  simulator          GARUDA_SIM=1        and --simulator   (loopback only)
  flight controller  GARUDA_FC_BRIDGE=1  and --real-flight-controller
It must be the only offboard publisher: do not run fc_bridge.py or other scripts at the same time.
Lives in services/garuda-follow/ next to fc_bridge.py and live_onboard.py, which it imports.
"""
import argparse
import csv
import math
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RATE_HZ = 20.0
FC_STALE_S = 0.3            # advancing ATTITUDE older than this: stop sending (PX4 failsafe takes over)
FC_HEARTBEAT_MAX_S = 1.5    # mode/arming older than this: unknown, stop sending
FC_CONFLICT_HOLD_S = 1.5    # another sysid/port from the FC host: unknown for this long
LOOP_STALL_S = 0.25         # this program's own loop paused longer than this: treat as link loss
OFFBOARD_STATES = ("TRACK", "RETURN", "CENTRED")
TYPE_MASK_POS_YAW = 2552    # use x,y,z + yaw; ignore velocity (8|16|32), acceleration (64|128|256), yaw_rate (2048)
TYPE_MASK_POS_YAW_FF = 504  # use x,y,z + yaw + yaw_rate (feed-forward: PX4 starts turning without waiting for the error)
PX4_MAIN_OFFBOARD = 6


def wrap(a):
    """Angle to (-pi, pi]."""
    a = math.fmod(a + math.pi, 2 * math.pi)
    if a < 0:
        a += 2 * math.pi
    return a - math.pi


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class BoothYaw:
    """Pure decision logic (no I/O), unit-tested in test_booth_yaw.py.

    step(now, fc, cmd) -> dict(send, x, y, z, yaw, yaw_rate, state, reason, yaw_rel_deg, sp_rel_deg)
      fc  : None or dict(offboard, armed, x, y, z, yaw)   (only fresh telemetry)
      cmd : None or dict(yaw_rate)                       (only a validated command)
      yaw      = absolute heading setpoint to send (rad)
      yaw_rate = how fast the setpoint moved this tick (rad/s, for the log only)
    """
    def __init__(self, yaw_limit_deg=45.0, max_yaw_rate=0.3, return_rate=0.25, lost_s=2.0,
                 beyond_s=1.5, center_tol_deg=3.0, resume_rate=0.25, max_lead_deg=15.0):
        self.limit = None if yaw_limit_deg is None else math.radians(yaw_limit_deg)   # None = no yaw limit
        self.max_rate, self.return_rate = max_yaw_rate, return_rate
        self.lost_s, self.beyond_s = lost_s, beyond_s
        self.tol, self.resume_rate = math.radians(center_tol_deg), resume_rate
        self.lead = math.radians(max_lead_deg)
        self.home = None
        self.sp = 0.0               # heading setpoint relative to the home heading (rad)
        self.last_t = None
        self.state = "WAIT_FC"
        self.lost_since = self.beyond_since = None
        self.lockout = None         # reason, set when the link was lost / FC disarmed while in OFFBOARD
        self.last_armed = None

    def _reset(self, state):
        self.state, self.home, self.sp, self.last_t = state, None, 0.0, None
        self.lost_since = self.beyond_since = None

    def _out(self, rel, rate, reason):
        hx, hy, hz, hyaw = self.home
        return dict(send=True, x=hx, y=hy, z=hz, yaw=wrap(hyaw + self.sp), yaw_rate=rate,
                    state=self.state, reason=reason, yaw_rel_deg=math.degrees(rel),
                    sp_rel_deg=math.degrees(self.sp))

    @staticmethod
    def _nothing(state, reason):
        return dict(send=False, x=0, y=0, z=0, yaw=0.0, yaw_rate=0.0, state=state, reason=reason,
                    yaw_rel_deg=None, sp_rel_deg=None)

    def step(self, now, fc, cmd, event=None):
        """event: None or a reason string for a falling edge seen by the link this tick
        (disarm, FC reboot, conflict, loop stall). In OFFBOARD it locks the bridge out."""
        in_offboard = self.state in OFFBOARD_STATES
        if fc is None:
            if in_offboard and self.lockout is None:
                self.lockout = event or "FC telemetry lost while in OFFBOARD"
            self._reset("WAIT_FC")
            return self._nothing(self.state, "no fresh FC telemetry: sending nothing (PX4 failsafe applies)")
        disarmed_in_offboard = fc["offboard"] and self.last_armed is True and not fc["armed"]
        self.last_armed = fc["armed"]
        if fc["offboard"] and self.lockout is None and in_offboard and (event or disarmed_in_offboard):
            self.lockout = event or "flight controller disarmed in OFFBOARD"
        if self.lockout is not None:
            if fc["offboard"]:
                # Like Bhanu's session rule: after a loss nothing restarts by itself, not even zeros.
                self._reset("LOCKOUT")
                return self._nothing(self.state, self.lockout + ": sending nothing; switch RC to Position to reset")
            self.lockout = None                     # pilot left OFFBOARD: normal pre-stream again
        if not fc["offboard"]:
            # Not in offboard: current position and heading, so the pilot's switch is bumpless.
            self._reset("PRESTREAM")
            return dict(send=True, x=fc["x"], y=fc["y"], z=fc["z"], yaw=fc["yaw"], yaw_rate=0.0,
                        state=self.state, reason="streaming current position; pilot may switch to OFFBOARD",
                        yaw_rel_deg=None, sp_rel_deg=None)
        if self.home is None:                       # first tick in offboard: lock home here
            self.home = (fc["x"], fc["y"], fc["z"], fc["yaw"])
            self.sp, self.last_t, self.state = 0.0, now, "TRACK"
        dt = clamp(now - self.last_t, 0.0, 0.2)
        self.last_t = now
        if self.limit is None:      # no limit: heading relative to home, unwrapped (can exceed 180 deg)
            rel = self.sp + wrap(fc["yaw"] - wrap(self.home[3] + self.sp))
        else:
            rel = wrap(fc["yaw"] - self.home[3])

        if self.state == "RETURN":
            before = self.sp
            self.sp -= math.copysign(min(abs(self.sp), self.return_rate * dt), self.sp)
            if abs(self.sp) < 1e-9 and abs(rel) <= self.tol:
                self.sp, self.state = 0.0, "CENTRED"
                return self._out(rel, 0.0, "back at home heading; waiting for the person near centre")
            return self._out(rel, (self.sp - before) / dt if dt else 0.0, "returning to home heading")

        if cmd is None:
            self.beyond_since = None
            if self.lost_since is None:
                self.lost_since = now
            if self.limit is not None and self.state == "TRACK" and now - self.lost_since >= self.lost_s and \
                    (abs(self.sp) > self.tol or abs(rel) > self.tol):
                self.state = "RETURN"
                return self._out(rel, 0.0, "person lost: returning to home heading")
            return self._out(rel, 0.0, "no valid command: holding heading")
        self.lost_since = None

        rate = clamp(cmd["yaw_rate"], -self.max_rate, self.max_rate)
        if self.state == "CENTRED":
            if abs(rate) > self.resume_rate:
                return self._out(rel, 0.0, "person off-centre: waiting (no chase after a return)")
            self.state = "TRACK"

        eps = 1e-9
        if self.limit is None:
            pushing = pushed_out = False
        else:
            pushing = (self.sp >= self.limit - eps and rate > 0) or (self.sp <= -self.limit + eps and rate < 0)
            pushed_out = abs(rel) > self.limit + self.tol      # vehicle itself beyond the limit (disturbance)
        if pushing or pushed_out:
            if self.beyond_since is None:
                self.beyond_since = now
            if now - self.beyond_since >= self.beyond_s:
                self.state, self.beyond_since = "RETURN", None
                return self._out(rel, 0.0, "person beyond the yaw limit: returning to home heading")
        else:
            self.beyond_since = None
        before = self.sp
        new = self.sp + rate * dt
        # never run further than max_lead ahead of the real heading (limits advancing only, never drags)
        new = clamp(new, min(self.sp, rel - self.lead), max(self.sp, rel + self.lead))
        self.sp = new if self.limit is None else clamp(new, -self.limit, self.limit)
        moved = (self.sp - before) / dt if dt else 0.0
        if pushing:
            return self._out(rel, moved, "at yaw limit: holding")
        return self._out(rel, moved, "tracking")


def endpoint_kind(args, env):
    sim = env.get("GARUDA_SIM") == "1" and args.simulator
    real = env.get("GARUDA_FC_BRIDGE") == "1" and args.real_flight_controller
    if (args.simulator and args.real_flight_controller) or (env.get("GARUDA_SIM") == "1" and env.get("GARUDA_FC_BRIDGE") == "1"):
        raise SystemExit("REFUSED: state either the simulator or the real flight controller, not both.")
    if not (sim or real):
        raise SystemExit("REFUSED: needs GARUDA_SIM=1 with --simulator, or GARUDA_FC_BRIDGE=1 with "
                         "--real-flight-controller. This program sends offboard setpoints.")
    host = args.listen.rsplit(":", 1)[0]
    if sim and not (args.fc_host.startswith("127.") and host.startswith("127.")):
        raise SystemExit("REFUSED: simulator mode is loopback only.")
    if real and args.fc_host.startswith("127."):
        raise SystemExit("REFUSED: a loopback --fc-host is a simulator.")
    return "simulator" if sim else "flight controller"


class FcState:
    """Pure bookkeeping of the ONE flight controller (unit-tested). Mirrors Bhanu's fc_gate.FcLink rules:
      * only packets from --fc-host; the first accepted packet pins the full address (host, port)
      * a PX4 heartbeat from that host with another sysid, or from another port -> conflict -> unknown
      * liveness = ATTITUDE whose time_boot_ms keeps ADVANCING, at most FC_STALE_S old
      * time_boot_ms going backwards = FC rebooted -> unknown + event
      * heartbeat (mode/arming) at most FC_HEARTBEAT_MAX_S old
      * a disarm seen in a heartbeat is reported as an event, even if a re-arm follows in the same read"""
    def __init__(self, fc_host, fc_sysid):
        self.host, self.sysid = fc_host, fc_sysid
        self.address = None
        self.att_at = self.hb_at = self.conflict_at = -math.inf
        self.boot_ms = None
        self.yaw = self.x = self.y = self.z = None
        self.offboard = self.armed = False
        self.event = None

    def note(self, kind, src_sys, src_comp, addr, now, **f):
        if addr is None or addr[0] != self.host:
            return
        if kind == "HEARTBEAT" and f.get("px4") and src_comp == 1 and \
                (src_sys != self.sysid or (self.address is not None and addr != self.address)):
            self.conflict_at, self.event = now, "another MAVLink id or port from the FC host"
            return
        if src_sys != self.sysid or src_comp != 1:
            return
        if self.address is None:
            self.address = addr
        elif addr != self.address:
            self.conflict_at, self.event = now, "FC packets from another port"
            return
        if kind == "ATTITUDE":
            tb = f["time_boot_ms"]
            if self.boot_ms is not None and tb < self.boot_ms:
                self.boot_ms, self.att_at, self.event = tb, -math.inf, "flight controller rebooted"
                return
            if self.boot_ms is None or tb > self.boot_ms:
                self.boot_ms, self.att_at, self.yaw = tb, now, f["yaw"]
        elif kind == "LOCAL_POSITION_NED":
            self.x, self.y, self.z = f["x"], f["y"], f["z"]
        elif kind == "HEARTBEAT" and f.get("px4"):
            armed = bool(f["armed"])
            if self.armed and not armed:
                self.event = "flight controller disarmed"
            self.offboard, self.armed, self.hb_at = bool(f["offboard"]), armed, now

    def pop_event(self):
        e, self.event = self.event, None
        return e

    def view(self, now):
        if now - self.att_at > FC_STALE_S or now - self.hb_at > FC_HEARTBEAT_MAX_S \
                or now - self.conflict_at < FC_CONFLICT_HOLD_S or None in (self.yaw, self.x, self.y, self.z):
            return None
        return dict(offboard=self.offboard, armed=self.armed, x=self.x, y=self.y, z=self.z, yaw=self.yaw)


class FcLink:
    """Plain UDP socket + pymavlink parser (like Bhanu's bridge): every packet is handled with its
    real source address, and setpoints are only ever sent to the pinned FC address."""
    def __init__(self, listen, fc_host, fc_sysid, yaw_ff=True):
        import socket
        from pymavlink import mavutil
        self.mavutil = mavutil
        host, port = listen.rsplit(":", 1)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, int(port)))
        self.sock.setblocking(False)
        self.tx = mavutil.mavlink.MAVLink(None, srcSystem=1,
                                          srcComponent=mavutil.mavlink.MAV_COMP_ID_ONBOARD_COMPUTER)
        self.parsers = {}
        self.fc_sysid = fc_sysid
        self.yaw_ff = yaw_ff
        self.st = FcState(fc_host, fc_sysid)
        self.last_hb = -math.inf

    def poll(self, now):
        mav = self.mavutil.mavlink
        for _ in range(200):
            try:
                data, addr = self.sock.recvfrom(4096)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:            # e.g. ICMP port unreachable reported on Windows
                break
            if addr[0] != self.st.host:
                continue
            parser = self.parsers.get(addr)
            if parser is None:
                parser = self.parsers[addr] = mav.MAVLink(None)
                parser.robust_parsing = True
            try:
                msgs = parser.parse_buffer(data) or []
            except Exception:
                continue
            for msg in msgs:
                t = msg.get_type()
                if t == "ATTITUDE":
                    f = dict(time_boot_ms=msg.time_boot_ms, yaw=msg.yaw)
                elif t == "LOCAL_POSITION_NED":
                    f = dict(x=msg.x, y=msg.y, z=msg.z)
                elif t == "HEARTBEAT":
                    f = dict(px4=msg.autopilot == mav.MAV_AUTOPILOT_PX4,
                             offboard=((msg.custom_mode >> 16) & 0xFF) == PX4_MAIN_OFFBOARD,
                             armed=bool(msg.base_mode & mav.MAV_MODE_FLAG_SAFETY_ARMED))
                else:
                    continue
                self.st.note(t, msg.get_srcSystem(), msg.get_srcComponent(), addr, now, **f)

    def view(self, now):
        return self.st.view(now)

    def send(self, out, now):
        addr = self.st.address
        if addr is None:
            return
        mav = self.mavutil.mavlink
        if now - self.last_hb >= 1.0:
            self.sock.sendto(self.tx.heartbeat_encode(mav.MAV_TYPE_ONBOARD_CONTROLLER,
                                                      mav.MAV_AUTOPILOT_INVALID, 0, 0, 0).pack(self.tx), addr)
            self.last_hb = now
        msg = self.tx.set_position_target_local_ned_encode(
            int(now * 1000) & 0xFFFFFFFF, self.fc_sysid, 1, mav.MAV_FRAME_LOCAL_NED,
            TYPE_MASK_POS_YAW_FF if self.yaw_ff else TYPE_MASK_POS_YAW,
            out["x"], out["y"], out["z"], 0, 0, 0, 0, 0, 0, out["yaw"], out["yaw_rate"] if self.yaw_ff else 0)
        self.sock.sendto(msg.pack(self.tx), addr)


def main(argv=None, env=os.environ):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--simulator", action="store_true")
    p.add_argument("--real-flight-controller", action="store_true")
    p.add_argument("--source", default="http://127.0.0.1:8098", help="camera computer (detector) address")
    p.add_argument("--listen", required=True, help="local UDP HOST:PORT the FC sends MAVLink to")
    p.add_argument("--fc-host", required=True)
    p.add_argument("--fc-sysid", required=True, type=int)
    p.add_argument("--dry-run", action="store_true", help="compute and print only; never send (bring-up)")
    p.add_argument("--yaw-limit-deg", type=float, default=45.0)
    p.add_argument("--no-yaw-limit", action="store_true",
                   help="follow the person all the way round; person lost = hold the current heading (no return)")
    p.add_argument("--max-yaw-rate", type=float, default=0.3, help="rad/s (Pi command itself is capped at 0.5)")
    p.add_argument("--return-rate", type=float, default=0.25, help="rad/s when turning back to home heading")
    p.add_argument("--lost-s", type=float, default=2.0)
    p.add_argument("--beyond-s", type=float, default=1.5)
    p.add_argument("--center-tol-deg", type=float, default=3.0)
    p.add_argument("--resume-rate", type=float, default=0.25, help="resume after a return only when |Pi yaw rate| <= this")
    p.add_argument("--no-yaw-ff", action="store_true", help="send heading only, no yaw-rate feed-forward (mask 2552)")
    p.add_argument("--max-lead-deg", type=float, default=15.0, help="heading setpoint never runs further ahead of the real heading")
    p.add_argument("--log", default=os.path.expanduser("~/booth_yaw_logs"))
    a = p.parse_args(argv)
    kind = endpoint_kind(a, env)
    if not ((a.no_yaw_limit or 5 <= a.yaw_limit_deg <= 90) and 0 < a.max_yaw_rate <= 0.5 and 0 < a.return_rate <= 0.5
            and 3 <= a.max_lead_deg <= 45):
        raise SystemExit("REFUSED: limits outside 5..90 deg / 0..0.5 rad/s / lead 3..45 deg")

    from fc_bridge import PiState                    # Bhanu's reader (age handling, cursor)
    from live_onboard import validate                # Bhanu's command validation
    pi = PiState(a.source)

    def reader():
        while True:
            try:
                pi.read_once()
            except Exception:
                time.sleep(0.2)
    threading.Thread(target=reader, daemon=True).start()

    # garuda_mono.py only runs detection while a viewer is connected (/stream or /api/events);
    # /api/state polling does not count. Keep one local, lightweight viewer open so tracking
    # does not stop when the laptop browser tab sleeps or the WiFi drops.
    def keep_detector_running():
        from urllib.request import urlopen
        while True:
            try:
                with urlopen(a.source.rstrip("/") + "/api/events", timeout=5) as r:
                    while r.read(4096):
                        pass
            except Exception:
                pass
            time.sleep(1.0)
    threading.Thread(target=keep_detector_running, daemon=True).start()

    fc = FcLink(a.listen, a.fc_host, a.fc_sysid, yaw_ff=not a.no_yaw_ff)
    ctl = BoothYaw(None if a.no_yaw_limit else a.yaw_limit_deg, a.max_yaw_rate, a.return_rate, a.lost_s, a.beyond_s,
                   a.center_tol_deg, a.resume_rate, a.max_lead_deg)
    os.makedirs(a.log, exist_ok=True)
    path = os.path.join(a.log, time.strftime("booth_yaw_%Y%m%d_%H%M%S.csv"))
    log = open(path, "w", newline="")
    w = csv.writer(log)
    w.writerow(["t", "state", "offboard", "armed", "yaw_rel_deg", "pi_yaw_rate", "sent_yaw_rate", "x", "y", "z", "sent", "reason",
                "sp_rel_deg", "fc_x", "fc_y", "fc_z", "loop_gap_ms"])
    print(f"booth_yaw_bridge -> {kind} {a.fc_host} sysid {a.fc_sysid}, listen {a.listen}, "
          f"limit {'NONE' if a.no_yaw_limit else '+/-%g deg' % a.yaw_limit_deg}, max {a.max_yaw_rate:g} rad/s, {'DRY RUN (sends nothing)' if a.dry_run else 'LIVE'}; log {path}")
    sent = 0
    last_print = 0.0
    period = 1.0 / RATE_HZ
    prev_t0 = None
    try:
        while True:
            t0 = time.monotonic()
            gap = 0.0 if prev_t0 is None else t0 - prev_t0
            prev_t0 = t0
            fc.poll(t0)
            now = time.monotonic()
            stalled = gap > LOOP_STALL_S or now - t0 > LOOP_STALL_S
            snap, age, prog = pi.current(now)
            cmd = None
            why = "no camera state"
            if snap is not None:
                o, why = validate(snap, age, prog)
                if o is not None:
                    cmd, why = {"yaw_rate": float(o["yaw_rate"])}, "ok"
                elif why == "Pi command invalid" and isinstance(snap.get("onboard"), dict):
                    why += " (" + str(snap["onboard"].get("reason")) + ")"   # the Pi's own reason
            event = fc.st.pop_event()
            if stalled:
                event = "bridge loop stalled %.0f ms" % (max(gap, now - t0) * 1000)
            view = None if stalled else fc.view(now)
            out = ctl.step(now, view, cmd, event)
            do_send = out["send"] and not a.dry_run
            if do_send:
                fc.send(out, now)
                sent += 1
            v = view
            w.writerow([f"{now:.3f}", out["state"], v and v["offboard"], v and v["armed"],
                        None if out["yaw_rel_deg"] is None else f"{out['yaw_rel_deg']:.1f}",
                        None if cmd is None else f"{cmd['yaw_rate']:.3f}", f"{out['yaw_rate']:.3f}",
                        f"{out['x']:.2f}", f"{out['y']:.2f}", f"{out['z']:.2f}", int(do_send), out["reason"] + ("" if cmd else " | cam: " + str(why)),
                        None if out["sp_rel_deg"] is None else f"{out['sp_rel_deg']:.1f}",
                        *(("", "", "") if v is None else (f"{v['x']:.2f}", f"{v['y']:.2f}", f"{v['z']:.2f}")),
                        f"{gap * 1000:.0f}"])
            if now - last_print >= 0.5:
                last_print = now
                rel = "--" if out["yaw_rel_deg"] is None else f"{out['yaw_rel_deg']:+5.1f}"
                spr = "--" if out["sp_rel_deg"] is None else f"{out['sp_rel_deg']:+5.1f}"
                print(f"[{out['state']:9s}] offboard={bool(v and v['offboard'])} armed={bool(v and v['armed'])} "
                      f"yaw_rel={rel} target={spr} deg  sent={sent}  {out['reason']}"
                      + ("" if cmd else f"  (camera: {why})"), flush=True)
            time.sleep(max(0.0, period - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        print("\nstopped: no more setpoints are sent (PX4 offboard-loss failsafe applies if still in OFFBOARD)")
    finally:
        log.close()


if __name__ == "__main__":
    main()
