"""Explicit browser ownership for the simulator. GETs never renew this lease.

An applied command acknowledges our controller handler, not PX4 command acceptance.
Legacy headless commands work only until a browser has claimed control.
"""
import threading
import uuid


class ControlLease:
    def __init__(self, timeout=3.0):
        self.timeout = timeout
        self.owner = None
        self.deadline = 0.0
        self.generation = 0
        self.browser_claimed = False
        self.expired = False
        self.pending_hold = False
        self.last_command = None
        self.lock = threading.RLock()

    def _expire(self, now):
        if self.owner is not None and now >= self.deadline:
            self.owner = None
            self.generation += 1
            self.expired = True
            self.pending_hold = True

    @staticmethod
    def session(value):
        if value is None:
            return None
        if not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.isascii() or any(c.isspace() for c in value):
            raise ValueError('Invalid control session')
        return value

    def heartbeat(self, session, now):
        with self.lock:
            self._expire(now)
            if not session or session != self.owner:
                raise ValueError('Control lease absent or expired; explicitly resume control')
            self.deadline = now + self.timeout

    def submit(self, action, session, now):
        with self.lock:
            session = self.session(session)
            self._expire(now)
            safe = action.get('command') in ('hold', 'land')
            if not safe:
                if session is None and self.browser_claimed:
                    raise ValueError('Browser control requires an explicit session')
                if self.owner is not None and session != self.owner:
                    raise ValueError('Another browser owns simulator control')
                if session is not None:
                    if self.owner is None:
                        self.owner = session
                        self.generation += 1
                    self.browser_claimed = True
                    self.expired = False
                    self.deadline = now + self.timeout
            return dict(id=uuid.uuid4().hex, action=dict(action), session=session,
                        generation=self.generation, safe=safe)

    def consume_disconnect(self, now):
        with self.lock:
            self._expire(now)
            result = self.pending_hold
            self.pending_hold = False
            return result

    def gestures_allowed(self, now):
        with self.lock:
            self._expire(now)
            return not self.browser_claimed or (self.owner is not None and not self.expired)

    def execute(self, ticket, now, callback):
        with self.lock:
            self._expire(now)
            error = None
            try:
                if not ticket['safe'] and (ticket['generation'] != self.generation or
                        (self.browser_claimed and ticket['session'] != self.owner)):
                    raise ValueError('Queued command belongs to an expired control lease')
                callback(ticket['action'], now)
            except (KeyError, ValueError, TypeError) as exc:
                error = str(exc)
            self.last_command = dict(id=ticket['id'], name=ticket['action'].get('command'),
                                     applied=error is None, error=error)
            return error

    def state(self, now):
        with self.lock:
            self._expire(now)
            return dict(owner=self.owner, lease_ms=max(0, round((self.deadline-now)*1000)) if self.owner else 0,
                        expired=self.expired, last_command=dict(self.last_command) if self.last_command else None)
