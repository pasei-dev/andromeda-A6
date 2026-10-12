#!/usr/bin/env python3
"""Extract the Alesis Andromeda A6 front-panel (87C52) EPROM image from A6 SysEx files.

    python3 extract_frontpanel_eprom.py [--mirror] [--report] OUT.bin IN.syx [IN.syx ...]

The A6 ships its two firmware updates as MIDI SysEx files, and both of them carry the
front-panel EPROM's contents as a block of Intel HEX records.  This script walks that
pipeline in order:

  1. strip SysEx framing - collect the bytes between F0 and F7 (an F7 followed by 0x00 is a
     stuffed literal F7, part of the message rather than its end)
  2. each message payload is 316 bytes; the data region starts 21 bytes in, and is read as
     whole 8-byte groups
  3. undo the A6's diagonal 8-into-7 bit packing: seven data bytes are spread over eight
     7-bit words, the top bits of each byte walking down the words (see unpack_8to7)
  4. pick out the Intel HEX records embedded in the decoded stream - in the order they appear,
     with no separators beyond a NUL between records
  5. write a 64 KB EPROM image; bytes no record covers are written as 0xFF (erased EPROM)

Damage tolerance: the decoded stream carries the protocol's per-message counter field spliced
into the record text about once per message, which corrupts roughly one record in six.  Every
record is therefore checked against its declared length and its Intel checksum, and only
records that pass are used.  Pass several input files to fill each other's holes - the OS and
the bootloader file carry the same front-panel code, damaged in different places.

CPython 3.8+, standard library only.  See the repository README for what the output means and
how far it has been verified.
"""
import argparse
import sys

HEX = b"0123456789abcdefABCDEF"


def unpack_8to7(T):
    """Seven data bytes out of one 8-byte transmitted word (the spec's diagonal rotation).

        A: T1:0     T0:6..0      B: T2:1..0   T1:6..1     C: T3:2..0   T2:6..2
        D: T4:3..0  T3:6..3      E: T5:4..0   T4:6..4     F: T6:5..0   T5:6..5
        G: T7:6..0  T6:6

    Reading it the other way round: word 0 holds A6..A0, word 1 holds B5..B0 below A7, and so
    on - the byte that owns the low bits moves up one word and further right each time.
    """
    return bytes((
        (T[1] & 0x01) << 7 | (T[0] & 0x7F),
        (T[2] & 0x03) << 6 | (T[1] >> 1) & 0x3F,
        (T[3] & 0x07) << 5 | (T[2] >> 2) & 0x1F,
        (T[4] & 0x0F) << 4 | (T[3] >> 3) & 0x0F,
        (T[5] & 0x1F) << 3 | (T[4] >> 4) & 0x07,
        (T[6] & 0x3F) << 2 | (T[5] >> 5) & 0x03,
        (T[7] & 0x7F) << 1 | (T[6] >> 6) & 0x01,
    ))


def decode_sysex(raw):
    """Concatenated SysEx payload bytes: framing stripped, F7 00 stuffing undone."""
    payload = bytearray()
    i = 0
    while True:
        i = raw.find(b"\xf0", i)
        if i < 0:
            break
        j = i + 1
        while j < len(raw):
            b = raw[j]
            if b == 0xF7:
                if j + 1 < len(raw) and raw[j + 1] == 0:
                    payload.append(0xF7)          # stuffed literal F7, same message
                    j += 2
                    continue
                break                             # real end of message
            payload.append(b)
            j += 1
        i = j + 1
    return bytes(payload)


def decode_payload(payload, chunk_size=316, data_offset=21):
    """Undo the bit packing of every message's data region."""
    out = bytearray()
    for base in range(0, len(payload) - chunk_size + 1, chunk_size):
        data = payload[base + data_offset:base + chunk_size]
        data = data[:len(data) - len(data) % 8]
        for k in range(0, len(data), 8):
            out += unpack_8to7(data[k:k + 8])
    return bytes(out)


def scan_records(stream):
    """Yield (address, data) for every intact Intel HEX record in the stream."""
    i = 0
    while True:
        i = stream.find(b":", i)
        if i < 0:
            return
        head = stream[i + 1:i + 9]
        if len(head) < 8:
            return
        try:
            count = int(head[0:2], 16)
            addr = int(head[2:6], 16)
            rectype = int(head[6:8], 16)
        except ValueError:
            i += 1
            continue
        total = 2 * count + 11                     # ':' len addr type data checksum
        body = stream[i + 1:i + total]
        if len(body) < total - 1 or not all(c in HEX for c in body):
            i += 1
            continue
        raw = bytes(int(body[k:k + 2], 16) for k in range(0, len(body), 2))
        if sum(raw) & 0xFF:                        # Intel checksum: the bytes sum to zero
            i += 1
            continue
        if rectype == 0x00:
            yield addr, raw[4:4 + count]
        i += total


def extract(paths, chunk_size=316, data_offset=21, report=False):
    """Merged {address: byte} over every input file; later files only fill gaps."""
    mem = {}
    for path in paths:
        stream = decode_payload(decode_sysex(open(path, "rb").read()), chunk_size, data_offset)
        before = len(mem)
        for addr, data in scan_records(stream):
            for k, b in enumerate(data):
                mem.setdefault(addr + k, b)
        if report:
            print("%-40s +%d bytes" % (path.split("/")[-1], len(mem) - before))
    return mem


def build_image(mem, size=0x10000):
    img = bytearray(b"\xff" * size)
    for addr, b in mem.items():
        if 0 <= addr < size:
            img[addr] = b
    return bytes(img)


def mirror_32k(img):
    """Write the same 32 KB into both halves, for a board that straps the EPROM's A15 low.

    The records address the code at 0x8000 (the CPU's own addressing), so on an A15-grounded
    socket the same 32 KB also has to sit at file offset 0x0000.  Jump targets stay valid
    because CPU address 0x8000+n and CPU address 0x0000+n read the same EPROM offset.
    """
    if len(img) != 0x10000:
        raise ValueError("mirror expects a 64 KB image")
    return img[0x8000:0x10000] * 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("output")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--mirror", action="store_true",
                    help="write the code half into both halves of the image")
    ap.add_argument("--chunk-size", type=int, default=316)
    ap.add_argument("--data-offset", type=int, default=21)
    ap.add_argument("--report", action="store_true", help="one line per input file")
    args = ap.parse_args(argv)

    mem = extract(args.inputs, args.chunk_size, args.data_offset, args.report)
    if not mem:
        print("no records found - wrong file, or the packing assumptions have drifted")
        return 1
    img = build_image(mem)
    if args.mirror:
        img = mirror_32k(img)
    with open(args.output, "wb") as fh:
        fh.write(img)

    addrs = sorted(mem)
    print("records placed   : %d bytes" % len(mem))
    print("addressed range  : %s..%s" % (hex(addrs[0]), hex(addrs[-1])))
    print("left as 0xFF     : %d bytes" % (0x10000 - len(mem)))
    print("wrote            : %s (%d bytes)" % (args.output, len(img)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
