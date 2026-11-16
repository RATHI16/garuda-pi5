"""Cursor for a waiting read of the detector's /api/state. Pure; no detector imports."""

WAIT_LIMIT_S = 1.0


def parse_after(query):
    """`query` is a parse_qs dict. Returns (ok, after).

    No parameters means "answer now" (after is None). `after=<frame seq>` means "answer
    when the published frame differs from this one, or after WAIT_LIMIT_S". -1 never
    matches a published frame, so it answers as soon as any result exists."""
    if not query:
        return True, None
    if set(query) != {"after"} or len(query["after"]) != 1:
        return False, None
    try:
        after = int(query["after"][0])
    except ValueError:
        return False, None
    return after >= -1, after


def frame_identity(snapshot):
    """(epoch, seq) of the frame a snapshot was built from; (None, None) before any result."""
    frame = snapshot.get("frame") or {}
    return frame.get("epoch"), frame.get("seq")


def event_stream(snapshot, write, wait_changed, heartbeat_s=0.1, rounds=None):
    """Server-sent-event loop. The cursor is the identity of the snapshot that was actually
    written, never the state as it is afterwards: a result published while a snapshot is
    being serialised or written is then seen as a change at once, not after the heartbeat.
    wait_changed(sent, timeout) must test "current identity != sent" before it waits.
    A slow reader may still miss intermediate results; it is never told it has one it has not."""
    n = 0
    while rounds is None or n < rounds:
        snap = snapshot()
        write(snap)
        wait_changed(frame_identity(snap), heartbeat_s)
        n += 1
