"""List the HID commands SteelSeries GG sends to a device in a USBPcap capture.

Used to reverse-engineer settings rivalcfg doesn't support yet:

    python3 tools/usbpcap_commands.py capture.pcapng            # list devices
    python3 tools/usbpcap_commands.py capture.pcapng 1038:185a  # commands + replies

Prints each distinct SET_REPORT payload (trailing zeros trimmed) with how often
and when it was sent, then the device's replies on interrupt IN endpoints
other than the movement endpoint.
"""

import collections
import struct
import sys


def _blocks(data):
    off, e = 0, "<"
    while off + 12 <= len(data):
        btype = struct.unpack_from(e + "I", data, off)[0]
        if btype == 0x0A0D0D0A:  # section header: byte order
            e = "<" if data[off + 8:off + 12] == b"\x4d\x3c\x2b\x1a" else ">"
        blen = struct.unpack_from(e + "I", data, off + 4)[0]
        yield btype, data[off + 8:off + blen - 4], e
        off += blen


def packets(path):
    """Yield (timestamp, usbpcap dict) for every captured URB."""
    for btype, body, e in _blocks(open(path, "rb").read()):
        if btype != 6:  # enhanced packet block
            continue
        th, tl, caplen = struct.unpack_from(e + "III", body, 4)
        pkt = body[20:20 + caplen]
        hlen, _irp, _st, _fn, info, bus, dev, ep, xfer, dlen = struct.unpack_from("<HQIHBHHBBI", pkt, 0)
        yield ((th << 32) | tl) / 1e6, dict(
            out=not (info & 1), bus=bus, dev=dev, ep=ep, xfer=xfer, data=bytes(pkt[hlen:hlen + dlen]))


def main(path, ids=None):
    pk = list(packets(path))
    t0 = pk[0][0]
    devices = {}
    for _, u in pk:  # device descriptor replies carry VID:PID
        d = u["data"]
        if u["xfer"] == 2 and not u["out"] and len(d) >= 18 and d[:2] == b"\x12\x01":
            vid, pid = struct.unpack_from("<HH", d, 8)
            devices[f"{vid:04x}:{pid:04x}"] = (u["bus"], u["dev"])
    if ids is None:
        for k, (bus, dev) in sorted(devices.items()):
            print(f"{k}  bus {bus} device {dev}")
        return
    addr = devices[ids]
    sent = collections.OrderedDict()
    for ts, u in pk:
        d = u["data"]
        if (u["bus"], u["dev"]) == addr and u["ep"] == 0 and u["out"] and d[:2] == b"\x21\x09":
            sent.setdefault(d[8:].rstrip(b"\0"), []).append(ts - t0)
    print("Commands sent (SET_REPORT):")
    for payload, times in sent.items():
        print(f"  x{len(times):4d}  {times[0]:8.3f}-{times[-1]:8.3f}s  {payload.hex(' ')}")
    sizes = collections.Counter(u["ep"] for _, u in pk
                                if (u["bus"], u["dev"]) == addr and u["ep"] & 0x80 and u["xfer"] == 1 and u["data"])
    movement_ep = sizes.most_common(1)[0][0] if sizes else None
    print("Replies (interrupt IN, excluding movement endpoint):")
    prev = None
    for ts, u in pk:
        if (u["bus"], u["dev"]) == addr and u["ep"] & 0x80 and u["xfer"] == 1 and u["ep"] != movement_ep and u["data"]:
            d = u["data"].rstrip(b"\0")
            if d != prev:
                print(f"  {ts - t0:8.3f}s  ep{u['ep']:02x}  {d.hex(' ')}")
            prev = d


if __name__ == "__main__":
    main(*sys.argv[1:3])
