"""Decides, each control tick, what the flight-controller bridge may send. Pure: no sockets.

Three outcomes:
  stream False            nothing is sent at all: the flight controller's own configured
                          offboard-loss action applies.
  stream True, zero       zero forward speed and zero yaw rate keep flowing.
  stream True, command    the Pi's command for the operator-selected person.

Two separate operator acts, each a NEW request generation seen after this process started:
  "fc_hold"  opens a stream session: zero setpoints start. Nothing is streamed before it.
  "fc"       grants the follow command, only inside a session and only while the flight
             controller already reports offboard.
The zero stream is not innocuous: it gives PX4 its offboard liveness. PX4 was seen (SITL,
2026-11-16) to return to offboard by itself when a lost stream came back. So when contact
with the flight controller is lost, or this program's loop stalls, the SESSION ends and the
stream does not restart until an operator opens a new one. A perception problem only ends the
grant: zeros keep flowing. This file cannot arm, change mode or write parameters.
"""
import math

from live_onboard import validate
from mavlink_min import MAV_AUTOPILOT_PX4, MAV_COMP_ID_AUTOPILOT1, MAV_MODE_FLAG_SAFETY_ARMED, PX4_MAIN_MODE_OFFBOARD

FC_MAX_AGE_S = 1.5              # heartbeat: mode and arming only
# Perception problems that come and go while the same person stays selected (a missed
# detection, one unconfirmed identity frame, a late frame). Under a grant these send ZERO and
# the command comes back by itself when the camera computer again reports a valid command for
# the SAME person, camera run and distance source, for at most PAUSE_MAX_S. Everything else
# (limits, mount, distance source, another target) ends the grant at once.
# Owner, 2026-11-16, after the first live session: a grant that died on the first missed frame
# "doesn't really work as intended". The strings are live_onboard.validate's; a test pins them.
TRANSIENT = ("Live command stale", "Pi command invalid", "Command frame or detector mismatch",
             "Camera selection mismatch", "Selected person not observed confidently",
             "Identity does not confirm this frame")
PAUSE_MAX_S = 5.0
FC_TELEMETRY_MAX_AGE_S = 0.3    # liveness. PROPOSED bound, to be confirmed against measured gaps (astra-0120)
AGAIN = "; press Flight controller: hold, then follow again"
START = "press Nowhere, then Flight controller: hold"


class FcLink:
    """What the ONE flight controller this bridge was told about last said about itself.
    System id and host are given at start. The first accepted heartbeat fixes the full
    address for the life of the process: the bridge never moves to another sender. A PX4
    autopilot heartbeat from that host with another id or port makes the state unknown.

    Mode and arming come from HEARTBEAT (1 Hz from PX4). Liveness does not: it needs ATTITUDE
    messages from the same address whose boot timestamp keeps advancing, at most
    FC_TELEMETRY_MAX_AGE_S old. A timestamp that goes backwards is a reboot or reordered old
    data: the state becomes unknown, it is never taken as fresh."""
    def __init__(self, sysid, host):
        self.sysid, self.host = sysid, host
        self.address = None
        self.seen = None            # (monotonic s, offboard, armed)
        self.authority_loss = None  # falling edges observed during the current receive batch
        self.conflict = None
        self.attitude = None        # (monotonic s of the last ADVANCING message, its time_boot_ms)
        self.max_gap = 0.0          # longest gap between advancing attitude messages, for bring-up

    def note(self, msg, now, address):
        if address is None or address[0] != self.host:
            return
        if msg.get("compid") != MAV_COMP_ID_AUTOPILOT1:
            return
        if msg.get("kind") == "attitude":
            if msg["sysid"] != self.sysid or address != self.address:
                return                                       # only the pinned controller's telemetry counts
            boot = msg["time_boot_ms"]
            if self.attitude is not None and boot < self.attitude[1]:
                self.conflict, self.attitude = now, None      # rebooted, or old data: never fresh
            elif self.attitude is None or boot > self.attitude[1]:
                if self.attitude is not None:
                    self.max_gap = max(self.max_gap, now - self.attitude[0])
                self.attitude = (now, boot)
            return
        if msg.get("kind") != "heartbeat" or msg.get("autopilot") != MAV_AUTOPILOT_PX4:
            return
        if msg["sysid"] != self.sysid or (self.address is not None and address != self.address):
            self.conflict = now
            return
        self.address = address
        offboard = (msg["custom_mode"] >> 16) & 0xFF == PX4_MAIN_MODE_OFFBOARD
        armed = bool(msg["base_mode"] & MAV_MODE_FLAG_SAFETY_ARMED)
        if self.seen and self.seen[2] and not armed:
            self.authority_loss = "flight controller disarmed"
        elif self.seen and self.seen[1] and not offboard:
            self.authority_loss = "flight controller left offboard mode"
        self.seen = (now, offboard, armed)

    def problem(self, now):
        """None when the flight controller is known and alive, else why not."""
        if self.conflict is not None and 0 <= now - self.conflict <= FC_MAX_AGE_S:
            return "flight controller identity changed, rebooted or sent old data"
        if self.seen is None or not 0 <= now - self.seen[0] <= FC_MAX_AGE_S:
            return "no heartbeat from the expected PX4 flight controller"
        if self.attitude is None or not 0 <= now - self.attitude[0] <= FC_TELEMETRY_MAX_AGE_S:
            return "no advancing ATTITUDE telemetry from the flight controller (needs at least 10 Hz)"
        return None

    def view(self, now):
        if self.problem(now) is not None:
            return None
        return {"sysid": self.sysid, "offboard": self.seen[1], "armed": self.seen[2], "address": self.address}


class FcGate:
    def __init__(self):
        self.observed = None        # (detector instance, request generation) last seen
        self.session = False        # the operator opened a zero stream and contact has not been lost since
        self.ended = None           # why the last session ended, shown until a new one opens
        self.request = None         # the request last seen, for wording only
        self.grant = None
        self.pin = None             # (camera epoch, target id, distance source) of the grant
        self.sequence = None
        self.paused = None          # when the current perception pause began (caller's clock)
        self.state, self.reason = "ready", "no stream: " + START

    def drop(self, reason, again=True):
        had = self.grant is not None
        self.grant = None
        self.pin = None
        self.sequence = None
        self.paused = None
        self.state, self.reason = "holding", reason + (AGAIN if again and had else "")

    def _pause(self, why, now):
        """Zero setpoints while the same grant waits for the person. False = the wait is over."""
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            return False
        if self.paused is None:
            self.paused = now
        if not 0 <= now - self.paused <= PAUSE_MAX_S:
            return False
        self.state, self.reason = "holding", why + ": sending zero, resumes by itself when the selected person is seen again"
        return True

    def end_session(self, reason):
        """Contact with the flight controller, or this program's own continuity, was lost."""
        if self.session:
            self.ended = reason + "; stream stopped: " + START + " to start it again"
        self.drop(reason, again=False)
        self.session = False
        if self.ended:
            self.reason = self.ended

    def _out(self, forward=0.0, yaw=0.0):
        if not self.session and self.state != "ready":
            # No stream is not "holding": holding means zero setpoints are flowing.
            self.state = "refused" if self.request in ("fc", "fc_hold") else "ready"
        return {"stream": self.session, "forward_vel": forward, "yaw_rate": yaw, "state": self.state, "reason": self.reason,
                "generation": self.observed[1] if self.observed else None,
                "instance": self.observed[0] if self.observed else None}

    def step(self, snapshot, age_ms, progress_ms, fc, fc_problem=None, now=None):
        """snapshot: the Pi's /api/state (or None); age_ms: its age including transport;
        progress_ms: time since a NEW frame arrived; fc: FcLink.view(); fc_problem: why it is None;
        now: the caller's monotonic clock (without it a perception pause ends the grant at once)."""
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        stats, tx = snapshot.get("stats") or {}, snapshot.get("transmit") or {}
        instance, gen = stats.get("instance"), tx.get("generation")
        usable = isinstance(instance, str) and bool(instance) and tx.get("instance") == instance and type(gen) is int and gen >= 0
        restart = new = False
        if usable:
            # Every request generation is consumed the moment it is seen, whatever else is
            # wrong at that moment. A press made while the flight controller was silent, in
            # conflict or not in offboard can therefore never take effect later.
            key = (instance, gen)
            restart = self.observed is None or instance != self.observed[0] or gen < self.observed[1]
            new = not restart and gen > self.observed[1]
            self.observed = key
            self.request = tx.get("request")
        else:
            self.request = None
        if fc is None:
            self.end_session(fc_problem or "no contact with the expected PX4 flight controller")
            return self._out()
        # FC boundaries are independent of camera availability. Missing perception may
        # pause a grant, but must never conceal the pilot leaving Offboard.
        if self.grant is not None and not (fc["armed"] and fc["offboard"]):
            self.end_session("flight controller disarmed" if not fc["armed"] else "flight controller left offboard mode")
            return self._out()
        # Check the deadline on recovery too, not only on another invalid frame.
        if self.paused is not None and (not isinstance(now, (int, float)) or
                not math.isfinite(now) or not 0 <= now - self.paused <= PAUSE_MAX_S):
            self.drop("perception pause expired")
            return self._out()
        if self.grant is not None:
            frame = snapshot.get("frame") or {}
            lock = snapshot.get("lock") or {}
            onboard = snapshot.get("onboard") or {}
            caps = stats.get("capabilities") or {}
            # Absent observations may pause; an explicit changed identity is a boundary,
            # even when the same sample is invalid for a different, transient reason.
            if self.pin is not None:
                epoch, target, source = self.pin
                changed = any(value is not None and value != expected for value, expected in (
                    (frame.get("epoch"), epoch), (onboard.get("frame_epoch"), epoch),
                    (lock.get("id"), target), (onboard.get("target_id"), target),
                    (onboard.get("range_source"), source),
                    ((stats.get("identity") or {}).get("target") if isinstance(stats.get("identity"), dict) else None, target)))
                if changed:
                    self.drop("target selection, camera epoch or distance source changed")
                    return self._out()
            if self.observed is not None and isinstance(instance, str) and instance != self.grant[0]:
                self.drop("detector restarted", again=False)
                return self._out()
            if onboard.get("transmit") is True:
                self.drop("Pi command must be marked unsent")
                return self._out()
            if self.pin is not None and "camera" in caps and "distance_source" in caps:
                expected = "stereo" if caps["distance_source"] == "stereo" else "box_height" if caps["camera"] in ("cm3", "usb") else "none"
                if expected != self.pin[2]:
                    self.drop("distance source changed")
                    return self._out()
            if "mount" in caps and caps["mount"] != "vehicle":
                self.drop("camera mount changed")
                return self._out()
            if self.sequence is not None and type(frame.get("seq")) is int and frame["seq"] < self.sequence:
                self.drop("frame sequence went backwards")
                return self._out()
            for field, low, high in (("forward_vel", -.4, .8), ("yaw_rate", -.5, .5), ("vertical_vel", 0, 0)):
                if field in onboard:
                    value = onboard[field]
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                        self.drop("Command exceeds independent limits")
                        return self._out()
        if not usable:
            if self.grant is not None and self._pause("no usable state from the camera computer", now):
                return self._out()
            self.drop("no usable state from the camera computer")
            return self._out()
        if restart and self.session:                         # detector restart: a baseline, never a press
            self.drop("detector restarted", again=False)
            return self._out()
        request = tx.get("request")
        if new and request == "fc_hold":
            # The one way a stream starts. The press must be a fresh one: a request that reached
            # us late (old or frozen camera state) or with the camera not in vehicle mount is
            # used up without effect. No person is needed for a zero stream.
            fresh = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 400
                        for v in (age_ms, progress_ms))
            mount = ((stats.get("capabilities") or {}).get("mount") == "vehicle")
            if fresh and mount:
                self.session, self.ended = True, None
            elif not self.session:
                self.ended = ("hold request arrived stale" if not fresh else "camera mount is not vehicle") + ": " + START
        if not self.session:
            self.drop(self.ended or "no stream: " + START, again=False)
            self.state = "ready" if request not in ("fc", "fc_hold") else "refused"
            return self._out()
        if request != "fc":
            self.drop("zero stream only" + ("" if fc["offboard"] else "; flight controller is not in offboard"), again=False)
            return self._out()
        if new:
            if not fc["armed"]:
                self.drop("flight controller is disarmed; follow request expired", again=False)
                return self._out()
            if not fc["offboard"]:
                self.drop("flight controller is not in offboard mode: switch it to offboard first, then press Flight controller: follow again", again=False)
                return self._out()
            self.grant, self.pin, self.sequence = key, None, None
        if self.grant != key:
            return self._out()
        if not fc["offboard"]:
            self.drop("flight controller left offboard mode")
            return self._out()
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (age_ms, progress_ms)):
            if not self._pause("camera state has no usable age", now):
                self.drop("camera state has no usable age")
            return self._out()
        command, why = validate(snapshot, age_ms, progress_ms)
        if command is None:
            if why not in TRANSIENT or not self._pause(why, now):
                self.drop(why)
            return self._out()
        self.paused = None
        pin = (command["frame_epoch"], command["target_id"], command["range_source"])
        if self.pin is not None and pin != self.pin:
            self.drop("target selection, camera epoch or distance source changed")
            return self._out()
        if self.sequence is not None and command["frame_seq"] < self.sequence:
            self.drop("frame sequence went backwards")
            return self._out()
        self.pin, self.sequence = pin, command["frame_seq"]
        self.state = "sending"
        self.reason = "armed"
        return self._out(float(command["forward_vel"]), float(command["yaw_rate"]))
