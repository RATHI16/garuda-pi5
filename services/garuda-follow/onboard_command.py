"""Body-frame follow command for a camera that is mounted ON the vehicle.

Pure and host-agnostic: no camera, no network, no MAVLink, no DDS. It turns one frame's
selected-person observation into the command a vehicle WOULD be given:

    yaw_rate     rad/s, positive = turn right (clockwise seen from above; PX4 FRD body yaw)
    forward_vel  m/s,   positive = toward the person (body x)
    vertical_vel m/s,   positive = up; always 0 here: altitude belongs to the flight controller

Nothing in this module sends anything. Whoever forwards the command owns limits and failsafes
on the flight-controller side; this output is not a safety function.

Rules:
- Turning comes from where the box is in the image, for every camera.
- Forward speed comes from exactly one stated source: "stereo" (measured distance minus the
  wanted gap) or "box_height" (apparent size, a proxy, not a distance). There is no fallback
  from one to the other. Without a usable value forward is 0 and `forward_reason` says why,
  while turning continues.
- Any doubt about the observation itself gives HOLD: every speed 0, valid False, a reason.
- A HOLD is immediate, and so is losing the distance source (forward or backward motion
  stops at once); speeds otherwise change no faster than the configured accelerations.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

RANGE_SOURCES = ("stereo", "box_height", "none")


@dataclass(frozen=True)
class OnboardConfig:
    gap_m: float = 2.5                 # stereo: distance to keep
    gap_deadband_m: float = 0.25
    range_gain: float = 0.5            # m/s per metre of distance error
    min_range_m: float = 0.8           # stereo values outside this span are not acted on
    max_range_m: float = 12.0
    desired_height_frac: float = 0.5   # box_height: wanted box height as a fraction of the frame
    height_deadband: float = 0.04
    height_gain: float = 2.0           # m/s per unit of height-fraction error
    edge_margin_frac: float = 0.01     # a box this close to the top/bottom edge counts as cut off
    yaw_gain: float = 0.8              # rad/s when the box centre is at the frame edge
    yaw_deadband: float = 0.06         # fraction of half the frame width
    advance_max_offset: float = 0.5    # no forward motion while the person is further off-centre
    max_yaw_rate: float = 0.5
    max_forward_vel: float = 0.8
    max_back_vel: float = 0.4
    max_accel: float = 0.8             # m/s^2
    max_yaw_accel: float = 1.5         # rad/s^2
    min_confidence: float = 0.35
    max_age_ms: float = 400.0
    min_height_frac: float = 0.08
    max_height_frac: float = 0.95


def _finite(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _hold(reason, target_id=None, range_source="none"):
    return {"valid": False, "reason": reason, "yaw_rate": 0.0, "forward_vel": 0.0, "vertical_vel": 0.0,
            "target_id": target_id, "range_m": None, "range_source": range_source, "forward_reason": "hold"}


def command(obs: dict, cfg: OnboardConfig = OnboardConfig()) -> dict:
    """One frame, no memory. `obs`:
      frame_w, frame_h   pixels
      age_ms             age of the frame when the command is computed
      target             None, or {id, x, y, w, h (pixels, top-left), conf, coasting}
      identity           None when no identity check is in use, else True/False
      range_source       "stereo" | "box_height" | "none"
      stereo             for "stereo": {status, z_m, reason} of THIS frame, or None
    """
    source = obs.get("range_source")
    if source not in RANGE_SOURCES:
        return _hold("unknown distance source")
    fw, fh, age = obs.get("frame_w"), obs.get("frame_h"), obs.get("age_ms")
    if not _finite(fw, fh) or fw <= 0 or fh <= 0:
        return _hold("no frame", range_source=source)
    if not _finite(age) or age < 0 or age > cfg.max_age_ms:
        return _hold("stale frame", range_source=source)
    target = obs.get("target")
    if not isinstance(target, dict):
        return _hold("no person selected", range_source=source)
    tid = target.get("id")
    if target.get("coasting"):
        return _hold("selected person not seen in this frame", tid, source)
    x, y, w, h, conf = (target.get(k) for k in ("x", "y", "w", "h", "conf"))
    if not _finite(x, y, w, h, conf) or w <= 0 or h <= 0:
        return _hold("unusable box", tid, source)
    if conf < cfg.min_confidence:
        return _hold("low confidence", tid, source)
    if obs.get("identity") is False:
        return _hold("identity not confirmed", tid, source)
    hfrac = h / fh
    if hfrac < cfg.min_height_frac or hfrac > cfg.max_height_frac:
        return _hold("implausible box size", tid, source)

    offset = _clamp((x + w / 2.0 - fw / 2.0) / (fw / 2.0), -1.0, 1.0)     # -1 left edge … +1 right edge
    yaw = 0.0 if abs(offset) < cfg.yaw_deadband else _clamp(cfg.yaw_gain * offset, -cfg.max_yaw_rate, cfg.max_yaw_rate)

    forward, why, range_m = 0.0, "ok", None
    if source == "stereo":
        block = obs.get("stereo") or {}
        z = block.get("z_m")
        if block.get("status") != "ok" or not _finite(z):
            why = "no stereo distance" + (f" ({block.get('reason')})" if block.get("reason") not in (None, "ok") else "")
        elif not cfg.min_range_m <= z <= cfg.max_range_m:
            why = "stereo distance outside the usable span"
        else:
            range_m = float(z)
            err = range_m - cfg.gap_m
            if abs(err) >= cfg.gap_deadband_m:
                forward = _clamp(cfg.range_gain * err, -cfg.max_back_vel, cfg.max_forward_vel)
    elif source == "box_height":
        err = cfg.desired_height_frac - hfrac                             # + = looks small = far
        if abs(err) >= cfg.height_deadband:
            forward = _clamp(cfg.height_gain * err, -cfg.max_back_vel, cfg.max_forward_vel)
        margin = cfg.edge_margin_frac * fh
        if forward > 0 and (y <= margin or y + h >= fh - margin):
            # A box cut by the top or bottom edge looks smaller than the person is.
            forward, why = 0.0, "box cut by the frame edge"
    else:
        why = "no distance source"
    if forward > 0 and abs(offset) > cfg.advance_max_offset:
        forward, why = 0.0, "turning toward the person first"
    return {"valid": True, "reason": "tracking", "yaw_rate": yaw, "forward_vel": forward, "vertical_vel": 0.0,
            "target_id": tid, "range_m": range_m, "range_source": source, "forward_reason": why,
            "offset": offset}


class OnboardFollower:
    """`command` plus acceleration limits. A HOLD zeroes the output at once and the next valid
    frame starts again from zero; a gap in time longer than `max_age_ms` does the same."""

    def __init__(self, cfg: OnboardConfig = OnboardConfig()):
        self.cfg = cfg
        self.reset()

    def reset(self):
        self.yaw, self.forward, self.t = 0.0, 0.0, None

    def step(self, obs: dict, t_s: float) -> dict:
        out = command(obs, self.cfg)
        if not out["valid"] or not _finite(t_s):
            self.reset()
            return out if out["valid"] is False else _hold("no clock")
        dt = 0.0 if self.t is None else t_s - self.t
        if dt < 0 or dt * 1000.0 > self.cfg.max_age_ms:
            self.yaw, self.forward, dt = 0.0, 0.0, 0.0
        self.t = t_s
        self.forward += _clamp(out["forward_vel"] - self.forward, -self.cfg.max_accel * dt, self.cfg.max_accel * dt)
        self.yaw += _clamp(out["yaw_rate"] - self.yaw, -self.cfg.max_yaw_accel * dt, self.cfg.max_yaw_accel * dt)
        if out["forward_reason"] != "ok":                      # no usable distance, or motion withheld:
            self.forward = 0.0                                  # zero at once in BOTH directions, no ramp-down
        return dict(out, yaw_rate=self.yaw, forward_vel=self.forward)


def limits(cfg: OnboardConfig = OnboardConfig()) -> dict:
    return {k: v for k, v in asdict(cfg).items()
            if k in ("gap_m", "max_yaw_rate", "max_forward_vel", "max_back_vel", "max_accel", "max_yaw_accel", "max_age_ms")}
