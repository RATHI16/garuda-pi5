"""One bounded live-camera SITL trial; only an explicit grounded reset re-arms it."""
import math


def grounded(fresh, armed, landed, phase):
    if not fresh or armed or landed is not True or phase != 'GROUND':
        raise ValueError('Land and disarm with fresh telemetry before changing test mode or resetting the bench trial')


class BenchTurn:
    MAX_SECONDS = 3.0
    MAX_RADIANS = math.radians(30)

    def __init__(self):
        self.reset()

    def reset(self):
        self.started = None
        self.last_yaw = None
        self.travel = 0.0
        self.reason = None

    def step(self, now, yaw):
        if self.reason:
            return False
        if not all(type(v) in (int, float) and math.isfinite(v) for v in (now, yaw)):
            self.reason = 'Bench trial stopped: invalid clock or heading'
            return False
        if self.started is None:
            self.started = now
        if self.last_yaw is not None:
            self.travel += abs(math.atan2(math.sin(yaw-self.last_yaw), math.cos(yaw-self.last_yaw)))
        self.last_yaw = yaw
        if now < self.started or now-self.started >= self.MAX_SECONDS or self.travel >= self.MAX_RADIANS:
            self.reason = 'Bench turn complete: Hold. Land and reset the trial before another turn.'
            return False
        return True

    def state(self, now):
        return dict(mode='bench', elapsed_s=max(0, now-self.started) if self.started is not None else 0,
                    turned_deg=math.degrees(self.travel), max_seconds=self.MAX_SECONDS,
                    max_degrees=math.degrees(self.MAX_RADIANS), exhausted=bool(self.reason), reason=self.reason)
