# Alesis Andromeda A6 — front panel firmware

The front-panel EPROM image for the Alesis Andromeda A6, recovered from the machine's own
SysEx updates — together with the two SysEx files it was recovered from and the script that
does the recovering.

The factory part is a socketed 27C512 UV EPROM on the Front Panel Left board, and no dump of
it circulates publicly. The OTA updates do carry it, packed inside a MIDI SysEx payload, and
that is what this repository unpacks.

> **Nothing here has been proven to work.** It is structurally verified and it programs, but it
> has never been seen to bring a front panel up. Read [Status](#status-extracted-not-verified)
> before you burn anything.

## What's in here

| File | What it is |
|---|---|
| `A6_front_panel_eprom.bin` | 64 KB EPROM image, front-panel code addressed at `0x8000` — the layout the records themselves use |
| `A6_front_panel_eprom_dual.bin` | the same code written into **both** 32 KB halves — the layout the A6's actual wiring needs |
| `firmware/A6_OS_v1.40.13b.syx` | main-board OS update (v1.40.13b); carries the front-panel code |
| `firmware/A6_bootloader_1.20.31.syx` | bootloader update; carries the same front-panel code |
| `firmware/A6_OS_v1.40.13b_release-notes.txt` | release notes shipped with the OS file |
| `tools/extract_frontpanel_eprom.py` | the extraction itself: SysEx → 64 KB image |
| `SHA256SUMS` | hashes of everything above, so a copy can be checked |

## The front panel, in one picture

```
Main board                          Front Panel Left board
MCF5307 ColdFire  ── serial, ~152 kbps ──  P87C52UBBB (8052, QFP-44)
2 MB flash (U3)                             │  EA strapped low (R42)
                                            │  → executes from the external EPROM only
                                            ├── 27C512 UV EPROM (U6, socketed, DIP-28)
                                            ├── LCD (its 5x8 font lives in the EPROM)
                                            └── LED matrix + button scanning
```

The EPROM's **A15 pin is tied to ground** and its `/CE` comes from `EPROM ENA` (a PSEN-derived
fetch strobe), so the CPU only ever reaches the first 32 KB of the part — aliased into both
halves of its 64 KB code space. A jump to CPU address `0x8100` and a jump to `0x0100` read the
same EPROM offset. That is why one of the two images is written double.

## How the image was retrieved

Both official updates carry the front-panel code as a block of Intel HEX records inside their
SysEx payload. `tools/extract_frontpanel_eprom.py` walks it in order:

1. **Fetch the sources** — the OS and bootloader SysEx from the community archive of the
   Alesis Yahoo group (see [Sources](#sources)).
2. **Strip the SysEx framing** — collect what sits between `F0` and `F7`; an `F7` followed by
   `00` is a stuffed literal `F7`, part of the message rather than its end.
3. **Take each message's data** — every payload is 316 bytes and its data region is 296 bytes
   (37 whole 8-byte groups) starting 21 bytes in.
4. **Undo the bit packing** — the A6 spreads 7 data bytes over eight 7-bit MIDI words: word 0
   holds bits A6–A0, word 1 holds B5–B0 plus A7, and so on diagonally (this is the packing
   described by the A6 MIDI SysEx specification).
5. **Pick out the Intel HEX records** — they sit back to back in the decoded stream. A record
   is placed at the address its header gives, and kept only if its Intel checksum validates.
6. **Build the 64 KB image** — bytes are placed at the addresses the records give; everything
   else is written `0xFF`, as an erased EPROM reads.

Reproduce both images:

```sh
python3 tools/extract_frontpanel_eprom.py A6_front_panel_eprom.bin \
    firmware/A6_OS_v1.40.13b.syx firmware/A6_bootloader_1.20.31.syx

python3 tools/extract_frontpanel_eprom.py --mirror A6_front_panel_eprom_dual.bin \
    firmware/A6_OS_v1.40.13b.syx firmware/A6_bootloader_1.20.31.syx
```

**Both sources are passed on purpose.** The protocol splices a per-message counter field into
the record text, which clips a few characters out of a record. In each file 13 records lose part
of their header and 7 lose one character of their text (`--report` prints both counts). Two
properties make that recoverable:

- the characters *after* the splice survive, so a shortened record still has an intact tail,
  and the tail can be re-anchored against the record's end;
- records are emitted back to back in ascending address order, so a record whose header is gone
  can be placed by the sequence around it.

A record is accepted only when its characters all come from a source and its own Intel checksum
validates; with both files nothing is left unresolved. They turn out to carry **the same 6862
bytes of record data, with zero disagreements**, so the second file is a cross-check rather than
a patch — either one alone reproduces this image byte for byte.

## What the image contains

- the 8051 vector table the records carry — reset at `0x8000` (`LJMP 0x8100`), `0x8003`, `0x8013`
  and `0x801B` (`RETI`, i.e. unused), Timer 0 at `0x800B` (`LJMP 0x8ECB`) and Timer 2 at `0x802B`
  (`LJMP 0x8D4B`). The rest of the vector area is not addressed by any record (see
  [Limits](#limits-of-this-extraction))
- init code at `0x8100`: `MOV PSW,#0`, `MOV SP,#0xBA`, `MOV P1,#0x7F`, then MOVX writes to the
  LCD controller
- a 5x8 LCD character font table, from about `0x9620` up to the highest addressed byte: 16-byte
  glyphs, marked off by `00 07 00` at 16-byte intervals
- the highest byte the records address is `0x9BC1`
- no display text: the front panel is a **command interpreter** — the Coldfire sends "clear
  display", "draw text" and similar over the serial link, so a front panel with valid firmware
  still shows nothing until the main board boots and starts talking

## Using it

`A6_front_panel_eprom_dual.bin` is the one to burn: with A15 grounded the code has to sit in
the file's lower 32 KB.

```sh
minipro -p AT27C512@DIP28 -y -w A6_front_panel_eprom_dual.bin
minipro -p AT27C512@DIP28 -y -r verify.bin
cmp verify.bin A6_front_panel_eprom_dual.bin && echo MATCH
```

Do **not** pass `-E`: that asks a flash programmer to erase the part, and a UV EPROM erases
with ultraviolet light. A chip that refuses to program and reads back `0xFF` everywhere is a
programming problem (weak Vpp on a cheap programmer, dirty ZIF contacts, a tired part), not an
image problem — a genuine AT27C512 programmed cleanly and read back byte-identical.

## Status: extracted, not verified

**Its shape is verified.** The bytes form valid 8051 code (vector table, init sequence, LCD
writes, a font table), every record in it passes its own Intel checksum, and the two official
update files carry the same 6862 bytes with **zero disagreements**. A burn to an AT27C512 of the
*earlier, wrong* build verified byte-for-byte on read-back, and ALE on the front panel's 87C52
then ran at fosc/6 with that chip in its socket — so the CPU does fetch front-panel code from
this socket, though not from these exact bytes.

**Its behaviour is not verified.** It has never been seen to bring a front panel up: no splash,
no LEDs. The instrument it was tried in had other faults at the time (dead front-panel crystal,
a main board that would not boot), and since the front panel waits for display commands from
the main board, a silent front panel is expected in that state. Whether this image is what a
healthy A6 runs is therefore still open.

### Limits of this extraction

- **Coverage:** the records address 6862 bytes, and that covers **everything from `0x8100` to
  `0x9BC1`** — including the 40 `0xFF` bytes inside that region, which are values the records
  themselves declare and their checksums cover. The other 244 bytes of `0x8000`–`0x9BC1` (all in
  `0x8004`–`0x80FF`, the interrupt vector area) are addressed by no record and are written
  `0xFF`: that is what an erased EPROM reads, but it is not extracted data, and the factory part
  may well hold `RETI` filler there.
- **No reference dump.** No known-good copy of the factory EPROM has turned up, so nothing can
  be compared byte-for-byte; record integrity is judged by the Intel checksum, plus the two
  sources agreeing on all 6862 bytes.
- **Superseded builds.** Two earlier extractions of this image were wrong and are not what the
  files here contain. The one actually burned into a chip during the repair dropped the last
  data byte of every record (354 bytes) through an off-by-two in the record slice. A later one
  read only the first 288 bytes of each message's data region instead of 296, losing 1316 bytes
  of the front-panel code. Both produce images whose records still pass their own checksums, so
  neither is detectable from the file alone — if you have a chip or a copy from either, replace
  it.

## Sources

- OS v1.40.13b and bootloader SysEx, and OS releases in general: the archived Alesis Yahoo
  group file area, `synth-diy.org/yg-archives2/group/Andromeda_A6/files`
- The 8-into-7 diagonal packing: the *Alesis Andromeda A6 MIDI SysEx Specification*
- Board layout, EPROM socket and the A15/`EPROM ENA` wiring: the A6 service manual, BOM and
  Front Panel Left schematic

## Provenance

Alesis firmware, redistributed as a repair reference for a discontinued instrument. Alesis and
Andromeda are trademarks of their owners; this repository is not affiliated with them.
