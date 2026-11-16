"""Where the operator asked the on-vehicle command to be sent. Pure state, no I/O.

The detector never transmits anything itself: it publishes the command (`onboard`, always
`transmit: false`) and, next to it, this request. A separate sender process (the PX4 SITL lab
on the PC, or a flight-controller bridge) reads both, does its own checks and reports back
what it is actually doing. A request is only accepted for a sender that is alive, and it
falls back to "off" on every boundary, so nothing resumes without the operator asking again.
"""
import math

TARGETS = ("sim", "fc")                       # senders: PX4 SITL lab on the PC | the flight-controller bridge
# What the operator may ask for, and which sender it is addressed to. "fc_hold" asks the bridge
# to start (or keep) its zero stream only; "fc" asks it to forward the follow command.
REQUESTS = {"sim": "sim", "fc_hold": "fc", "fc": "fc"}
STATES = ("ready", "sending", "holding", "refused")
LISTENER_MAX_AGE_S = 2.0                      # a sender not heard from for this long is gone
LABELS = {"sim": "the PX4 simulator on the PC", "fc": "the flight-controller bridge"}


class Transmit:
    def __init__(self, instance=""):
        self.instance = str(instance)          # this detector process; reports about a request must name it
        self.request, self.generation, self.reason = "off", 0, "not requested"
        self._seen = {}                        # target -> (monotonic s, state, reason, bound generation or None, stream)

    def _alive(self, target, now):
        seen = self._seen.get(target)
        return seen is not None and 0 <= now - seen[0] <= LISTENER_MAX_AGE_S

    def _expire(self, now):
        """Drop a request whose sender has gone quiet. Runs BEFORE anything renews a heartbeat,
        so a late heartbeat can never keep or revive a request, page open or not."""
        if self.request != "off" and not self._alive(REQUESTS[self.request], now):
            self.off(LABELS[REQUESTS[self.request]] + " stopped answering")

    def report(self, target, state, reason, now, generation=None, instance=None, stream=None):
        """A sender's heartbeat. Returns the request it must honour.
        "ready" only says the sender is alive. Every other state is a statement about one
        request, so it must name that request's generation and this detector's instance; a
        statement about any other request is kept as a heartbeat and shown as "stale"."""
        if target not in TARGETS:
            raise ValueError("unknown target")
        if state not in STATES:
            raise ValueError("unknown state")
        if not math.isfinite(now):
            raise ValueError("bad time")
        bound = None
        if state != "ready":
            if type(generation) is not int or not isinstance(instance, str) or not instance:
                raise ValueError("generation and instance are required for this state")
            bound = generation if instance == self.instance else -1
        if stream not in (None, True, False):
            raise ValueError("stream must be true or false")
        self._expire(now)
        # "stream": whether the sender is putting setpoints on its link at all (zero or not).
        # It is a statement about now, bound like the state, and is kept apart from the state
        # so that "follow is off" can never hide a stream that is still flowing.
        self._seen[target] = (now, state, str(reason or "")[:120], bound, stream if bound is not None else None)
        return {"request": self.request, "generation": self.generation, "instance": self.instance}

    def ask(self, target, mount, now):
        """The operator's button. Raises ValueError with the reason shown on the page."""
        if target == "off":
            self.off("switched off by the operator")
            return
        if target not in REQUESTS:
            raise ValueError("target must be off, sim, fc_hold or fc")
        if not math.isfinite(now):
            raise ValueError("bad time")
        self._expire(now)
        if mount != "vehicle":
            raise ValueError("camera mount is not vehicle")
        if not self._alive(REQUESTS[target], now):
            raise ValueError("nothing is listening: start " + LABELS[REQUESTS[target]] + " first")
        if target != self.request:
            self.request, self.generation, self.reason = target, self.generation + 1, "requested by the operator"

    def off(self, reason):
        if self.request != "off":
            self.generation += 1
        self.request, self.reason = "off", reason

    def snapshot(self, now):
        """Published in /api/state. Drops the request when its sender has gone quiet."""
        self._expire(now)
        listeners = {}
        for target in TARGETS:
            seen = self._seen.get(target)
            alive = self._alive(target, now)
            # A statement about a request counts only for the request in force right now.
            current = alive and (seen[1] == "ready" or (REQUESTS.get(self.request) == target and seen[3] == self.generation))
            # The stream statement only needs to be about this detector and this generation; the
            # request need not be addressed to the sender (after "off" its zeros may still flow).
            bound_now = alive and seen[3] == self.generation
            listeners[target] = {"alive": alive, "state": (seen[1] if current else "stale") if alive else None,
                                 "streaming": seen[4] if bound_now else None,
                                 "reason": seen[2] if alive else None,
                                 "age_ms": round((now - seen[0]) * 1e3) if alive else None}
        return {"request": self.request, "generation": self.generation, "instance": self.instance,
                "reason": self.reason, "listeners": listeners}
