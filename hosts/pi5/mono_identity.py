"""Keeps ONE selected person across track-ID changes, among other people. Pure (numpy only).

The tracker hands out a new ID whenever it loses someone for a moment, and it can also hand
an existing ID to a different person who steps into the same box. So a track ID is tracker
continuity, not identity. This owner decides who the selected person is from learned
appearance embeddings, and is built to fail closed: a wrong bind is worse than a hold.

Rules (design: docs/superpowers/specs/2026-11-14-robust-target-reid-design.md; review
astra-0062 finding 3):
  * `confirmed` says whether the current target track is known to be the selected person.
    Consumers hold while it is false. A frame whose embedding contradicts the gallery is
    reported unconfirmed at once, although the lock itself is only given up after several
    such frames in a row (one odd frame costs one frame of hold, not the selection).
  * A small gallery of DIFFERENT views is collected only from confirmed frames whose
    embedding is compatible with the gallery. An incompatible view is never learned.
  * The same track ID coming back after it was unseen for longer than gap_s is NOT taken on
    trust: it must pass the same score/margin/confirmation as a new ID. So must a track
    whose embeddings turn contradictory while it is continuously visible (an ID switch
    between overlapping people). A track that fails is rejected for the life of its ID.
  * Anyone visible at the same time as the confirmed person is never that person.
  * Binding to a different track needs an embedding for it, a score against the gallery at
    or above bind_score, a margin over the next best candidate, an optional position gate,
    and the same candidate winning for several frames and a minimum time.
  * Missing evidence never counts as a pass. After memory_s without the confirmed person,
    only a new click selects.
Thresholds are provisional until measured on recordings with other people in view.
"""
import numpy as np


def unit(v):
    if v is None:
        return None
    v = np.asarray(v, dtype=np.float32).ravel()
    n = float(np.linalg.norm(v))
    return v / n if np.isfinite(n) and n > 0 else None


class IdentityOwner:
    def __init__(self, bind_score=0.80, margin=0.10, confirm_frames=4, confirm_s=0.3, memory_s=30.0,
                 gallery_cap=16, gallery_add_below=0.80, learn_min=0.65, reject_score=0.55, gap_s=0.2,
                 verify_s=1.5, max_speed_mps=3.0, slack_m=1.0, announce_s=3.0):
        self.bind_score, self.margin = bind_score, margin
        self.confirm_frames, self.confirm_s, self.memory_s = confirm_frames, confirm_s, memory_s
        self.gallery_cap, self.gallery_add_below, self.learn_min = gallery_cap, gallery_add_below, learn_min
        self.reject_score, self.gap_s, self.verify_s = reject_score, gap_s, verify_s
        self.max_speed_mps, self.slack_m, self.announce_s = max_speed_mps, slack_m, announce_s
        self.release()

    def release(self):
        self.tid = None
        self.gallery, self.excluded, self.rejected = [], set(), set()
        self.last_seen = self.last_pos = self.candidate = None
        self.confirmed = False
        # Time the current target track has been VISIBLE while unconfirmed. It only advances on
        # frames where the track is seen, is not restarted by a missing frame, and is cleared when
        # the track is confirmed or the target changes (astra-0063).
        self.verify_elapsed, self.verify_last = 0.0, None
        self.doubt = 0                 # consecutive contradictory embeddings while confirmed
        self.rebound = None            # (old id, new id, time, score) of the latest bind
        self.state, self.reason, self.binds = "idle", "", 0

    def select(self, tid, now):
        """An operator selection: start a fresh identity on this track. The operator's click
        is the one piece of evidence taken on trust, for the frame it is made on."""
        self.release()
        self.tid, self.state, self.confirmed = tid, "tracking", True

    def score(self, emb):
        e = unit(emb)
        if e is None or not self.gallery:
            return None
        return max(float(e @ g) for g in self.gallery)

    def _learn(self, emb):
        """Add a view only if it is compatible with what is already known AND different enough
        to be worth keeping. The first view after a selection starts the gallery."""
        e = unit(emb)
        if e is None:
            return
        if not self.gallery:
            self.gallery.append(e)
            return
        s = max(float(e @ g) for g in self.gallery)
        if self.learn_min <= s < self.gallery_add_below and len(self.gallery) < self.gallery_cap:
            self.gallery.append(e)

    def _unconfirm(self):
        self.confirmed, self.candidate, self.doubt = False, None, 0
        self.verify_elapsed, self.verify_last = 0.0, None

    def _confirm_step(self, tid, score, now):
        """One frame of evidence for `tid`. True once it has won for long enough."""
        c = self.candidate
        if c is None or c["id"] != tid:
            self.candidate = c = dict(id=tid, since=now, frames=0, score=score)
        c["frames"] += 1
        c["score"] = min(c["score"], score)
        return c["frames"] >= self.confirm_frames and now - c["since"] >= self.confirm_s

    def _best(self, people, now):
        """(score, person) of the best acceptable candidate, or (None, reason)."""
        scored = []
        for p in people:
            if p["id"] in self.excluded or p["id"] in self.rejected:
                continue
            s = self.score(p.get("emb"))
            if s is not None:
                scored.append((s, p))
        scored.sort(key=lambda x: -x[0])
        if not scored:
            return None, "no candidate with an appearance reading"
        if scored[0][0] < self.bind_score:
            return None, "nobody resembles the selected person"
        if len(scored) > 1 and scored[0][0] - scored[1][0] < self.margin:
            return None, "two candidates look alike; select again"
        top_score, top = scored[0]
        if top.get("pos") is not None and self.last_pos is not None and self.last_seen is not None:
            reach = self.max_speed_mps * max(0.0, now - self.last_seen) + self.slack_m
            if float(np.hypot(top["pos"][0] - self.last_pos[0], top["pos"][1] - self.last_pos[1])) > reach:
                return None, "best candidate is too far from where the person was"
        return (top_score, top), ""

    def update(self, now, people):
        """people: this frame's visible person tracks, each {'id', 'emb' (vector or None),
        'pos' ((x, z) metres or None)}. Returns the decision summary (see summary())."""
        if self.tid is None:
            return self.summary(now)
        me = next((p for p in people if p["id"] == self.tid), None) if self.tid not in self.rejected else None

        if me is not None and self.confirmed:
            gap = None if self.last_seen is None else now - self.last_seen
            s = self.score(me.get("emb"))
            if gap is not None and gap > self.gap_s:
                # Same ID, but it was out of sight: tracker continuity is not identity.
                self._unconfirm()
            elif s is not None and s < self.reject_score:
                self.doubt += 1
                if self.doubt >= self.confirm_frames:      # contradictory for several frames in a row
                    self._unconfirm()
            else:
                if s is not None:
                    self.doubt = 0
            if self.confirmed:
                self.last_seen = now
                if me.get("pos") is not None:
                    self.last_pos = me["pos"]
                # Only a frame whose own appearance agrees with the gallery is evidence about who
                # else is in view: a doubtful or unreadable frame must not rule other people out
                # (the person in doubt may be the impostor, and the real one standing next to them).
                if not self.gallery or (s is not None and s >= self.learn_min):
                    self.excluded.update(p["id"] for p in people if p["id"] != self.tid)
                    self._learn(me.get("emb"))
                self.candidate, self.state, self.reason = None, "tracking", ""
                return self.summary(now)

        if self.last_seen is not None and now - self.last_seen > self.memory_s:
            self.candidate, self.state, self.reason = None, "expired", "not seen for too long; select again"
            return self.summary(now)
        if not self.gallery:
            self.candidate, self.state, self.reason = None, "no_reference", "no appearance reference yet; select again"
            return self.summary(now)

        if me is not None:
            # The target's own track is visible but not confirmed: verify it like any candidate.
            # However this state was reached (time passing, a contradictory look, or frames with
            # nobody in them), the clock for it runs on every frame the track is seen.
            if self.verify_last is not None:
                self.verify_elapsed += min(max(0.0, now - self.verify_last), self.gap_s)
            self.verify_last = now
            best, why = self._best(people, now)
            if best is not None and best[1]["id"] == self.tid:
                if self._confirm_step(self.tid, best[0], now):
                    self.confirmed, self.candidate, self.doubt = True, None, 0
                    self.verify_elapsed, self.verify_last = 0.0, None
                    self.last_seen = now
                    if me.get("pos") is not None:
                        self.last_pos = me["pos"]
                    self._learn(me.get("emb"))
                    self.state, self.reason = "tracking", ""
                else:
                    self.state, self.reason = "verifying", "checking that this is still the selected person"
                return self.summary(now)
            # Not the clear best this frame. Give it verify_s; then this ID is no longer trusted.
            if best is None or best[1]["id"] != self.tid:
                self.candidate = None if (self.candidate or {}).get("id") == self.tid else self.candidate
            if self.verify_elapsed > self.verify_s:
                self.rejected.add(self.tid)
                self.state, self.reason = "searching", "the track no longer looks like the selected person"
            else:
                self.state, self.reason = "verifying", "checking that this is still the selected person"
                return self.summary(now)

        else:
            self.verify_last = None                        # not seen this frame: the verification clock pauses
        # The selected person has no trusted track in this frame: look for them under another ID.
        # `confirmed` is not cleared here: whether the same ID may be trusted again when it comes
        # back is decided by how long it was gone (gap_s), the same for a skipped update and for
        # an update with nobody in it. The summary reports unconfirmed meanwhile.
        best, why = self._best([p for p in people if p["id"] != self.tid], now)
        if best is None:
            self.candidate = None
            self.state = "ambiguous" if why.startswith("two candidates") else "searching"
            self.reason = why
            return self.summary(now)
        top_score, top = best
        if self._confirm_step(top["id"], top_score, now):
            self.rebound = (self.tid, top["id"], now, self.candidate["score"])
            self.tid, self.last_seen, self.candidate = top["id"], now, None
            self.confirmed, self.doubt = True, 0
            self.verify_elapsed, self.verify_last = 0.0, None
            if top.get("pos") is not None:
                self.last_pos = top["pos"]
            self._learn(top.get("emb"))
            self.binds += 1
            self.state, self.reason = "tracking", ""
        else:
            self.state, self.reason = "confirming", "confirming a returning person"
        return self.summary(now)

    def summary(self, now):
        r = self.rebound if self.rebound is not None and now - self.rebound[2] <= self.announce_s else None
        return {"target": self.tid, "confirmed": bool(self.confirmed and self.tid is not None and self.state == "tracking" and self.doubt == 0), "state": self.state,
                "reason": self.reason, "gallery": len(self.gallery), "binds": self.binds,
                "candidate": self.candidate["id"] if self.candidate else None,
                "rebound_from": r[0] if r else None, "rebound_score": round(r[3], 3) if r else None}
