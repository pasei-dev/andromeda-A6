# Alesis Andromeda A6 front panel firmware

The front panel EPROM image from the A6, pulled out of the two official firmware updates. The
factory chip is a socketed 27C512 on the Front Panel Left board, and no dump of it ever
circulated, but both updates carry a copy inside their MIDI SysEx payload. This repo unpacks it,
ships the bytes, and keeps the script so you can check them yourself.

**If you just want the file: `A6_front_panel_eprom_dual.bin`.** That is the one to burn.

> [!WARNING]
> Nobody has watched this image run a front panel. It checks out as bytes, not as behaviour.
> Read [Status](#status) before you burn anything.

## Files

| File | What it is |
|---|---|
| `A6_front_panel_eprom_dual.bin` | **Burn this one.** The code written into both 32 KB halves, which is what the A6's wiring needs |
| `A6_front_panel_eprom.bin` | The same code sitting only in the upper half, at `0x8000` |
| `firmware/*.syx` | The two official updates the image came from: the main-board OS v1.40.13b and the bootloader |
| `tools/extract_frontpanel_eprom.py` | The script that does the extraction |
| `SHA256SUMS` | Hashes, so you can prove a copy is the same bytes |

## Why two images

The EPROM's A15 pin is grounded, so the 87C52 on the panel only ever sees the first 32 KB of the
part. That half gets read through two different addresses (`0x8100` and `0x0100` are the same
EPROM byte), so the code has to be written into both halves of the file.

```
Main board                        Front Panel Left board
MCF5307 ColdFire ── serial ────  P87C52UBBB (8052)
2 MB flash (U3)                    │  EA low: runs from the EPROM, not its own ROM
                                   ├── 27C512 EPROM (U6, socketed)   ← the image below
                                   ├── LCD  (its font lives in the EPROM)
                                   └── LED matrix + buttons
```

## Burning it

```sh
minipro -p AT27C512@DIP28 -y -w A6_front_panel_eprom_dual.bin
minipro -p AT27C512@DIP28 -y -r verify.bin
cmp verify.bin A6_front_panel_eprom_dual.bin && echo MATCH
```

Do not pass `-E`. That erases the chip, and a UV EPROM only erases under ultraviolet light.

If a chip refuses to program and reads back `0xFF` everywhere, that is a programming problem, not
an image problem: weak Vpp on a cheap programmer, dirty ZIF contacts, or a tired part. A good
AT27C512 programs and reads back byte-identical.

## Where the bytes come from

Both updates contain the panel code as Intel HEX records inside a bit-packed SysEx payload. The
script walks that:

1. strips the SysEx framing
2. takes 296 bytes from each 316-byte message (37 whole 8-byte groups, starting 21 bytes in)
3. undoes the A6's 8-into-7 bit packing
4. reads the Intel HEX records and keeps only the ones whose checksum validates
5. writes a 64 KB image, with everything the records don't cover left as `0xFF`

Reproduce both images:

```sh
python3 tools/extract_frontpanel_eprom.py A6_front_panel_eprom.bin \
    firmware/A6_OS_v1.40.13b.syx firmware/A6_bootloader_1.20.31.syx

python3 tools/extract_frontpanel_eprom.py --mirror A6_front_panel_eprom_dual.bin \
    firmware/A6_OS_v1.40.13b.syx firmware/A6_bootloader_1.20.31.syx
```

Add `--report` to see what each source contributed.

- The updates are not clean copies. The protocol splices a counter field into the record text and
  cuts a few characters out of a record: 13 records per file lose part of their header, 7 lose one
  character. The characters after the cut survive, so the script re-anchors the tail, places a
  headerless record by the sequence around it, and keeps the result only if the checksum leaves
  exactly one possibility.
- Both files carry the same 6862 bytes, agreeing byte for byte, so either one alone reproduces
  this image. The second is a cross-check.

## What's inside

8051 code you would expect on a display panel: the reset vector at `0x8000` jumping to `0x8100`,
init code (`MOV PSW,#0`, stack at `0xBA`, LCD writes), interrupt vectors, and a 5x8 character
font from about `0x9620` up to the last addressed byte at `0x9BC1`.

There is no startup text or logo in it. The panel is a command interpreter: the main board sends
"clear display", "draw text" and so on over the serial link. A silent front panel is normal until
the main board boots and starts talking.

## Status

**Checked as bytes.** Every record passes its own checksum, everything from `0x8100` to `0x9BC1`
is covered, and the two update files agree on all 6862 bytes.

**Not checked as behaviour.** It has never been seen to bring a panel up: no splash, no LEDs. The
instrument it was tried in had other faults at the time (dead panel crystal, a main board that
would not boot), and a panel with working firmware still shows nothing until the main board sends
it something. So a silent panel proves nothing either way, and whether this is what a healthy A6
runs is still open.

- 244 bytes of the interrupt vector area (`0x8004` to `0x80FF`) are addressed by no record and are
  written `0xFF`. That is what a blank EPROM reads, but it is fill, not extracted data. The
  factory part may have `RETI` bytes there.
- No known-good dump of the factory EPROM exists to compare against, so the Intel checksum and the
  agreement between the two updates are the whole of the evidence.

Earlier versions of this image were wrong in two different ways, and both still pass checksums:
one dropped the last data byte of every record (the chip burned during the repair), the next read
only 288 of the 296 bytes per message and lost 1316 bytes of code. If you have a copy or a chip
from before, throw it away and re-burn.

## Sources

- The SysEx updates: the archived Alesis Yahoo group file area at
  `synth-diy.org/yg-archives2/group/Andromeda_A6/files`
- The 8-into-7 packing: the *Alesis Andromeda A6 MIDI SysEx Specification*
- Socket and wiring details: the A6 service manual and Front Panel Left schematic

Alesis firmware, kept alive as a repair reference for a discontinued instrument. Not affiliated
with Alesis; trademarks belong to their owners.
