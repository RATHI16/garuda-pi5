"""The three MAVLink messages the flight-controller bridge needs, standard library only.

Sent:     HEARTBEAT (0), SET_POSITION_TARGET_LOCAL_NED (84) as a body-frame velocity + yaw rate.
Received: HEARTBEAT (0), to learn the flight controller's mode, and ATTITUDE (30), whose
          boot timestamp shows the flight controller is alive and progressing right now.
Nothing here can arm, disarm, change mode or write a parameter: those messages do not exist
in this file. MAVLink 2 framing is written; MAVLink 1 and 2 are read. Signed frames and
frames with unknown incompatibility flags are skipped, not interpreted. Nothing here
authenticates the sender or protects against replayed or delayed data.
"""
import struct

HEARTBEAT, ATTITUDE, SET_POSITION_TARGET_LOCAL_NED = 0, 30, 84
CRC_EXTRA = {HEARTBEAT: 50, ATTITUDE: 39, SET_POSITION_TARGET_LOCAL_NED: 143}
MAV_FRAME_BODY_NED = 8                  # x forward, y right, z down, relative to the vehicle's heading
MAV_TYPE_ONBOARD_CONTROLLER, MAV_AUTOPILOT_INVALID, MAV_AUTOPILOT_PX4, MAV_STATE_ACTIVE = 18, 8, 12, 4
MAV_COMP_ID_AUTOPILOT1, MAV_COMP_ID_ONBOARD_COMPUTER = 1, 191
MAV_MODE_FLAG_SAFETY_ARMED = 0x80
PX4_MAIN_MODE_OFFBOARD = 6
# Use velocity and yaw rate only: ignore position (bits 0-2), acceleration (6-8) and yaw (10).
VELOCITY_YAWRATE_MASK = 0b0000_0101_1100_0111


def crc16(data, crc=0xFFFF):
    for b in data:
        t = b ^ (crc & 0xFF)
        t = (t ^ (t << 4)) & 0xFF
        crc = ((crc >> 8) ^ (t << 8) ^ (t << 3) ^ (t >> 4)) & 0xFFFF
    return crc


def frame(msgid, payload, seq, sysid, compid):
    body = payload.rstrip(b"\0") or payload[:1]          # MAVLink 2 drops trailing zero bytes
    head = struct.pack("<BBBBBB", len(body), 0, 0, seq & 0xFF, sysid, compid) + msgid.to_bytes(3, "little")
    return b"\xfd" + head + body + struct.pack("<H", crc16(head + body + bytes([CRC_EXTRA[msgid]])))


def heartbeat(seq, sysid, compid=MAV_COMP_ID_ONBOARD_COMPUTER):
    payload = struct.pack("<IBBBBB", 0, MAV_TYPE_ONBOARD_CONTROLLER, MAV_AUTOPILOT_INVALID, 0, MAV_STATE_ACTIVE, 3)
    return frame(HEARTBEAT, payload, seq, sysid, compid)


def body_velocity(seq, sysid, compid, time_boot_ms, target_system, target_component, forward_m_s, yaw_rate_rad_s):
    """Forward speed and yaw rate (+ = clockwise seen from above); sideways and vertical are zero."""
    payload = struct.pack("<I11fHBBB", time_boot_ms & 0xFFFFFFFF, 0.0, 0.0, 0.0, float(forward_m_s), 0.0, 0.0,
                          0.0, 0.0, 0.0, 0.0, float(yaw_rate_rad_s), VELOCITY_YAWRATE_MASK,
                          target_system, target_component, MAV_FRAME_BODY_NED)
    return frame(SET_POSITION_TARGET_LOCAL_NED, payload, seq, sysid, compid)


def parse(datagram):
    """Heartbeats and attitude messages found in one UDP datagram, as dicts with "kind".
    Anything else, and anything whose checksum does not match, is skipped."""
    out, i, n = [], 0, len(datagram)
    while i < n:
        magic = datagram[i]
        if magic == 0xFD and i + 10 <= n:
            length, incompat = datagram[i + 1], datagram[i + 2]
            if incompat & ~1:                                # a framing feature this reader does not know:
                i += 1                                       # never interpret it
                continue
            end = i + 10 + length + 2 + (13 if incompat & 1 else 0)
            if incompat & 1:                                 # signed frame: signing is not supported here, so its
                if end > n:                                  # content is skipped whole, not trusted
                    break
                i = end
                continue
            head, sysid, compid = datagram[i + 1:i + 10], datagram[i + 5], datagram[i + 6]
            msgid, body, crc_at = int.from_bytes(datagram[i + 7:i + 10], "little"), datagram[i + 10:i + 10 + length], i + 10 + length
        elif magic == 0xFE and i + 6 <= n:
            length = datagram[i + 1]
            end = i + 6 + length + 2
            head, sysid, compid, msgid = datagram[i + 1:i + 6], datagram[i + 3], datagram[i + 4], datagram[i + 5]
            body, crc_at = datagram[i + 6:i + 6 + length], i + 6 + length
        else:
            i += 1
            continue
        if end > n:
            break
        if msgid in (HEARTBEAT, ATTITUDE) and length <= (9 if msgid == HEARTBEAT else 28) and \
                struct.unpack("<H", datagram[crc_at:crc_at + 2])[0] == crc16(head + body + bytes([CRC_EXTRA[msgid]])):
            if msgid == HEARTBEAT:
                mode, kind, autopilot, base, status, _ = struct.unpack("<IBBBBB", body.ljust(9, b"\0"))
                out.append({"kind": "heartbeat", "sysid": sysid, "compid": compid, "custom_mode": mode, "type": kind,
                            "autopilot": autopilot, "base_mode": base, "system_status": status})
            else:
                out.append({"kind": "attitude", "sysid": sysid, "compid": compid,
                            "time_boot_ms": struct.unpack("<I", body.ljust(28, b"\0")[:4])[0]})
        i = end
    return out
