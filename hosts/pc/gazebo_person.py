#!/usr/bin/env python3
"""Show the bench camera target as a figure in the isolated Gazebo world.

Visualisation only. The figure is placed where the follow controller says its fixed-stereo
target is (or its explicit mono schematic estimate); nothing is read back from Gazebo into perception or control, and the figure's
pose is never treated as a measurement. When there is no current measurement the figure is
moved out of sight: a figure standing still must not look like a fresh observation.

The position is the torso-region stereo point (unvalidated depth) through an ASSUMED camera
pose; the figure's feet are simply put on the ground under it.
"""
import argparse
import json
import math
import os
import time
import xml.etree.ElementTree as ET
from urllib.request import urlopen

MODEL = "garuda_person"
WORLD = "garuda_lab"
HIDDEN = (0.0, 0.0, -20.0)            # below the ground plane: not visible to any camera
MAX_AGE_MS = 700

PERSON_SDF = f"""<model name="{MODEL}"><static>true</static><pose>0 0 -20 0 0 0</pose><link name="body">
  <visual name="legs"><pose>0 0 0.45 0 0 0</pose><geometry><cylinder><radius>0.16</radius><length>0.9</length></cylinder></geometry><material><ambient>0.12 0.16 0.32 1</ambient><diffuse>0.16 0.22 0.45 1</diffuse></material></visual>
  <visual name="torso"><pose>0 0 1.2 0 0 0</pose><geometry><cylinder><radius>0.21</radius><length>0.6</length></cylinder></geometry><material><ambient>0.6 0.12 0.12 1</ambient><diffuse>0.85 0.16 0.16 1</diffuse></material></visual>
  <visual name="head"><pose>0 0 1.63 0 0 0</pose><geometry><sphere><radius>0.12</radius></sphere></geometry><material><ambient>0.7 0.55 0.45 1</ambient><diffuse>0.9 0.72 0.6 1</diffuse></material></visual>
</link></model>"""


def external_person_world(sdf_text):
    """The lab world with the scripted walker replaced by the externally placed figure.
    Raises ValueError if the world has no single scripted actor to replace."""
    root = ET.fromstring(sdf_text)
    world = root.find("world")
    actors = [] if world is None else world.findall("actor")
    if len(actors) != 1:
        raise ValueError("expected exactly one scripted actor in the world")
    index = list(world).index(actors[0])
    world.remove(actors[0])
    world.insert(index, ET.fromstring(PERSON_SDF))
    return ET.tostring(root, encoding="unicode")


def _num(v):
    return type(v) in (int, float) and math.isfinite(v)


def world_pose(state, origin_xy=(0.0, 0.0)):
    """(x, y, z) in the Gazebo world (ENU) for the controller state, or (None, reason).

    PX4 local NED north/east map to world y/x; origin_xy is the world position of the
    vehicle's local origin (its spawn point; (0, 0) in the lab world). Only a current
    fixed-stereo or explicitly accepted mono schematic target of a locked selection is shown."""
    if isinstance(state, dict) and state.get("source_kind") == "pi_onboard":
        visual = state.get("onboard_visual") or {}
        age = state.get("perception_age_ms")
        frame = state.get("frame") or {}
        target = visual.get("target")
        if (state.get("telemetry_fresh") is not True or not _num(age) or not 0 <= age <= 400
            or visual.get("valid") is not True or visual.get("simulation_only") is not True
            or visual.get("method") != "onboard-stereo-display-v1"
            or visual.get("frame_epoch") != frame.get("epoch") or visual.get("frame_seq") != frame.get("seq")
            or not isinstance(target, (list, tuple)) or len(target) != 2 or not all(_num(v) for v in target)):
            return None, visual.get("reason") or "no fresh onboard stereo display position"
        return (origin_xy[0]+target[1], origin_xy[1]+target[0], 0.0), "onboard stereo preview; display only"
    if not isinstance(state, dict) or state.get("source_kind") not in ("fixed_stereo", "mono_estimate"):
        return None, "source is not an explicit fixed bench camera source"
    if state.get("lock_state") != "LOCKED" or state.get("requires_reselection"):
        return None, "no locked selection"
    age = state.get("perception_age_ms")
    if not _num(age) or not 0 <= age < MAX_AGE_MS:
        return None, "perception is stale"
    if state.get("source_kind") == "mono_estimate":
        o = state.get("mono_estimate")
        if not isinstance(o, dict) or o.get("accepted") is not True or o.get("simulation_only") is not True or o.get("method") != "box-size-schematic-v1":
            return None, "mono schematic source is not explicitly accepted"
    else:
        o = state.get("fixed_stereo")
        if not isinstance(o, dict) or o.get("status") != "ok" or o.get("target_id") != state.get("target_id"):
            return None, "no stereo measurement for the selected person"
    target = state.get("target")
    if not isinstance(target, (list, tuple)) or len(target) != 2 or not all(_num(v) for v in target):
        return None, "controller has no target position"
    north, east = target
    return (origin_xy[0] + east, origin_xy[1] + north, 0.0), ""


class Steady:
    """What to do with the figure for each controller reading. Display smoothing only.

    A single missing or rejected stereo reading used to hide the figure for one frame, and
    every reading moved it by its measurement noise: it flickered and shimmered. Now the
    figure stays where it was through gaps shorter than hold_s (then it is hidden, so a
    figure left standing never passes for a current measurement), its position is low-pass
    filtered, and moves smaller than deadband_m are not sent."""

    def __init__(self, hold_s=0.7, alpha=0.35, deadband_m=0.02):
        self.hold_s, self.alpha, self.deadband_m = hold_s, alpha, deadband_m
        self.at = None            # filtered (x, y, z) currently wanted on screen
        self.sent = None          # last position actually sent, or HIDDEN
        self.last_valid = None

    def step(self, pose, now):
        """pose: (x, y, z) from world_pose or None. Returns a position to send, or None for
        'leave the figure as it is'."""
        if pose is not None:
            self.last_valid = now
            if self.at is None:
                self.at = tuple(pose)                     # first sight: no easing in from elsewhere
            else:
                self.at = tuple(a + self.alpha * (p - a) for a, p in zip(self.at, pose))
            if self.sent in (None, HIDDEN) or math.dist(self.at, self.sent) >= self.deadband_m:
                return self.at
            return None
        if self.last_valid is not None and now - self.last_valid <= self.hold_s:
            return None                                   # brief gap: keep it where it was
        self.at = None
        return HIDDEN if self.sent != HIDDEN else None

    def confirm(self, position):
        """Call when a returned position was accepted by Gazebo."""
        self.sent = tuple(position)


class Placer:
    """Thin wrapper over the world's set_pose service in the isolated partition."""

    def __init__(self, world=WORLD):
        from gz.transport13 import Node
        from gz.msgs10.pose_pb2 import Pose
        from gz.msgs10.boolean_pb2 import Boolean
        self.node, self.Pose, self.Boolean = Node(), Pose, Boolean
        self.service = f"/world/{world}/set_pose"

    def place(self, xyz):
        msg = self.Pose()
        msg.name = MODEL
        msg.position.x, msg.position.y, msg.position.z = xyz
        msg.orientation.w = 1.0
        ok, reply = self.node.request(self.service, msg, self.Pose, self.Boolean, 200)
        return bool(ok and reply.data)


def frame_view(node, eye=(3.2, -6.0, 3.2), look_at=(0.0, 1.0, 0.0), pitch=0.42):
    """Point the Gazebo window at the home area once, so the vehicle and the figure are not
    specks in the default far view. Cosmetic; a missing GUI is not an error."""
    from gz.msgs10.gui_camera_pb2 import GUICamera
    from gz.msgs10.boolean_pb2 import Boolean
    m = GUICamera()
    m.pose.position.x, m.pose.position.y, m.pose.position.z = eye
    yaw = math.atan2(look_at[1] - eye[1], look_at[0] - eye[0])
    cy, sy, cp, sp = math.cos(yaw / 2), math.sin(yaw / 2), math.cos(pitch / 2), math.sin(pitch / 2)
    m.pose.orientation.w, m.pose.orientation.x, m.pose.orientation.y, m.pose.orientation.z = cy * cp, -sy * sp, cy * sp, sy * cp
    ok, reply = node.request("/gui/move_to/pose", m, GUICamera, Boolean, 1000)
    return bool(ok and reply.data)


def main():
    if os.environ.get("GARUDA_SIM") != "1":
        raise SystemExit("Refused: GARUDA_SIM=1 is required")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--controller", default="http://127.0.0.1:8099")
    p.add_argument("--seconds", type=float, default=0, help="0 = until stopped")
    a = p.parse_args()
    os.environ["GZ_PARTITION"] = "garuda_isolated"
    placer = Placer()
    end = time.monotonic() + a.seconds if a.seconds else math.inf
    steady, last_key, last_log = Steady(), None, 0.0
    framed, frame_tries = False, 0
    counts = dict(placed=0, hidden=0, failed=0, errors=0)
    while time.monotonic() < end:
        start = time.monotonic()
        if not framed and frame_tries < 40:               # the window may open after us
            frame_tries += 1
            try:
                framed = frame_view(placer.node)
            except Exception:
                pass
        try:
            with urlopen(a.controller + "/api/state", timeout=0.5) as r:
                state = json.load(r)
            pose, reason = world_pose(state)
            frame = state.get("frame") or {}
            key = (frame.get("epoch"), frame.get("seq"))
        except Exception as e:
            pose, reason, key = None, "controller unreachable: " + type(e).__name__, None
            counts["errors"] += 1
        if pose is not None and key == last_key:
            pose_for_step = False                         # same observation as last time: nothing new
        else:
            pose_for_step = pose
            last_key = key if pose is not None else last_key
        if pose_for_step is not False:
            want = steady.step(pose_for_step, start)
            if want is not None:
                if placer.place(want):
                    steady.confirm(want)
                    counts["hidden" if want == HIDDEN else "placed"] += 1
                else:
                    counts["failed"] += 1
        if start - last_log >= 5:
            last_log = start
            print(json.dumps(dict(kind="gazebo_person", shown=steady.sent not in (None, HIDDEN), reason=reason, **counts)), flush=True)
        time.sleep(max(0.0, 1 / 15 - (time.monotonic() - start)))
    placer.place(HIDDEN)


if __name__ == "__main__":
    main()
