#!/usr/bin/env python3
"""Extract the Alesis Andromeda A6 front-panel (87C52) EPROM image from A6 SysEx files.

    python3 extract_frontpanel_eprom.py [--mirror] [--report] OUT.bin IN.syx [IN.syx ...]

Both A6 firmware updates (the OS and the bootloader) carry the front-panel EPROM's contents as
a block of Intel HEX records inside their MIDI SysEx payload.  The pipeline, in order:

  1. strip the SysEx framing - the bytes between F0 and F7, an F7 followed by 0x00 being a
     stuffed literal F7 rather than the end of the message
  2. each message payload is 316 bytes and its data region is 296 bytes (37 whole 8-byte
     groups) starting 21 bytes in
  3. undo the A6's diagonal 8-into-7 bit packing (see unpack_8to7)
  4. read the embedded Intel HEX records: address, data length, data, Intel checksum
  5. assemble a 64 KB image - bytes no record covers stay 0xFF, as an erased EPROM reads

The updates are not perfect sources for this EPROM: once per message the protocol splices a
per-message counter into the record text, clipping a few characters out of roughly one record
in thirty.  Two things cover that.  The characters *after* the splice survive, so a shortened
record still has an intact tail that can be re-anchored to its end.  And the two update files
are clipped at different records, so each one fills the other's holes.  A record is accepted
only when its characters all come from a source and its own Intel checksum validates; a gap too
wide to fill from the sources is brute-forced (bounded) and refused if more than one filling
fits the checksum.

CPython 3.8+, standard library only.  See the repository README for what the output means and
how far it has been verified.
"""
import argparse
import itertools
import sys

HEX = b"0123456789abcdefABCDEF"
BRUTE_LIMIT = 16 ** 4


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


def decode_payload(payload, chunk_size=316, data_start=21, data_len=296):
    """Undo the bit packing of every message's data region."""
    out = bytearray()
    for base in range(0, len(payload) - chunk_size + 1, chunk_size):
        data = payload[base + data_start:base + data_start + data_len]
        data = data[:len(data) - len(data) % 8]
        for k in range(0, len(data), 8):
            out += unpack_8to7(data[k:k + 8])
    return bytes(out)


def hexruns(body):
    """(offset, run) for every maximal run of hex characters in body."""
    runs = []
    i = 0
    while i < len(body):
        if body[i] in HEX:
            j = i
            while j < len(body) and body[j] in HEX:
                j += 1
            runs.append((i, body[i:j]))
            i = j
        else:
            i += 1
    return runs


def slot_texts(stream):
    """address -> list of (length, prefix, tail) character candidates, one per record slot.

    A slot is a ':' plus whatever record header survived; its extent is the next such ':' so an
    interior NUL (the splice inserts them) cannot cut a record in half.  Records are emitted
    back to back in ascending address order, and a ':' can occur inside record data, so the
    candidates are checked against that sequence: a readable header counts only if it continues
    the chain, and a chain gap is filled by the one ':' sitting in it (a record whose header the
    splice took out).
    """
    heads = []                                     # (position, addr, count) where readable
    colons = []                                    # every ':' position
    i = 0
    while True:
        i = stream.find(b":", i)
        if i < 0:
            break
        colons.append(i)
        runs = hexruns(stream[i + 1:i + 9])
        head = runs[0][1] if runs and runs[0][0] == 0 else b""
        if len(head) >= 6:
            count = int(head[0:2].decode(), 16)
            addr = int(head[2:6].decode(), 16)
            if count and 0x8000 <= addr <= 0xA000:
                heads.append((i, addr, count))
        i += 1

    def follow(start):
        """Walk the records from one readable header, keeping the sequence as the check."""
        chain = [start]
        expected = start[1] + start[2]
        for cand in heads[heads.index(start) + 1:]:
            if cand[1] < expected:
                continue                           # ':' inside already-accepted record data
            if cand[1] > expected:                 # a gap: a lost header, or nothing emitted
                inside = [c for c in colons if chain[-1][0] < c < cand[0]]
                if len(inside) == 1 and 0 < cand[1] - expected <= 32:
                    chain.append((inside[0], expected, cand[1] - expected))
            chain.append(cand)
            expected = cand[1] + cand[2]
        return chain

    chain = max((follow(h) for h in heads), key=len) if heads else []
    marks = sorted((pos, addr, 2 * count + 11) for pos, addr, count in chain)

    out = {}
    stats = {"slots": 0, "clipped_head": sum(1 for m in chain if m not in heads),
             "clipped_char": 0}
    for k, (pos, addr, length) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else pos + length
        runs = hexruns(stream[pos + 1:end])
        if not runs:
            continue
        window = length - 1
        prefix = runs[0][1][:window] if runs[0][0] == 0 else b""
        tail = None
        if len(prefix) < window:
            for off, run in reversed(runs):
                if off == 0 or off > window or len(run) < 2:
                    continue
                tail = run
                break
        stats["slots"] += 1
        seen = set(range(len(prefix)))
        if tail:
            seen |= set(range(window - len(tail), window))
        if len(seen) < window:
            stats["clipped_char"] += 1
        out.setdefault(addr, []).append((length, prefix, tail))
    return out, stats


def assemble(candidate_sets):
    """Merge the per-file character candidates; return the record's data bytes, or None."""
    length = candidate_sets[0][0]
    window = length - 1
    cand = [set() for _ in range(window)]
    for _, prefix, tail in candidate_sets:
        for off, run in [(0, prefix)] + ([(window - len(tail), tail)] if tail else []):
            for k, ch in enumerate(run):
                if 0 <= off + k < window:
                    cand[off + k].add(ch)
    unknown = [i for i in range(window) if len(cand[i]) != 1]
    fixed = [next(iter(c)) if len(c) == 1 else None for c in cand]
    fills = []
    if unknown:
        if 16 ** len(unknown) > BRUTE_LIMIT:
            return None
        for combo in itertools.product(b"0123456789ABCDEF", repeat=len(unknown)):
            t = list(fixed)
            for pos, ch in zip(unknown, combo):
                t[pos] = ch
            fills.append(bytes(t))
    else:
        fills = [bytes(fixed)]
    valid = set()
    for t in fills:
        try:
            buf = bytes.fromhex(t.decode())
        except ValueError:
            continue
        if sum(buf) & 0xFF == 0:
            valid.add(buf)
    if len(valid) != 1:
        return None
    return next(iter(valid))[4:-1]


def extract(paths, chunk_size=316, data_start=21, data_len=296, report=False):
    """Merged {address: byte} over every input file, plus the addresses refused."""
    per_file = []
    for path in paths:
        stream = decode_payload(decode_sysex(open(path, "rb").read()),
                                chunk_size, data_start, data_len)
        slots, stats = slot_texts(stream)
        per_file.append(slots)
        if report:
            print("%-40s %d record slots (%d clipped header, %d clipped character)"
                  % (path.split("/")[-1], stats["slots"], stats["clipped_head"],
                     stats["clipped_char"]))
    image, refused = {}, []
    for addr in sorted(set().union(*[set(p) for p in per_file])):
        data = assemble([p[addr][0] for p in per_file if addr in p])
        if data is None:
            refused.append(addr)
            continue
        for k, b in enumerate(data):
            image[addr + k] = b
    return image, refused


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
    ap.add_argument("--data-start", type=int, default=21)
    ap.add_argument("--data-len", type=int, default=296)
    ap.add_argument("--report", action="store_true", help="one line per input file")
    args = ap.parse_args(argv)

    mem, refused = extract(args.inputs, args.chunk_size, args.data_start, args.data_len,
                           args.report)
    if not mem:
        print("no records found - wrong input file, or the packing assumptions have drifted")
        return 1
    img = build_image(mem)
    if args.mirror:
        img = mirror_32k(img)
    with open(args.output, "wb") as fh:
        fh.write(img)

    addrs = sorted(mem)
    print("record bytes      : %d (%s..%s)" % (len(mem), hex(addrs[0]), hex(addrs[-1])))
    if refused:
        print("records refused   : %d (%s)" % (len(refused), ", ".join(hex(a) for a in refused)))
    print("left as 0xFF      : %d bytes" % (0x10000 - len(mem)))
    print("wrote             : %s (%d bytes)" % (args.output, len(img)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
