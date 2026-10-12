# AGENTS.md

## Overview

A data repository: two vendor SysEx firmware files, the 64 KB front-panel EPROM images
extracted from them, and the one script that does the extraction. The README explains the
machine, the pipeline and the verification status — read it before reasoning about either
image.

## Map

- `README.md` — human documentation: the front-panel architecture, the extraction pipeline, the
  retrieval sources, and the limits of the result. The place every claim belongs.
- `firmware/*.syx` — the two vendor updates; they are the input, never edited.
- `A6_front_panel_eprom*.bin` — generated output. Never hand-edit one; regenerate it.
- `tools/extract_frontpanel_eprom.py` — the whole pipeline: SysEx framing → 8-into-7 unpack →
  Intel HEX record selection (checksum-gated) → image assembly, with `--mirror` for the
  A15-grounded board layout.
- `SHA256SUMS` — regenerate after any change to a data file.

## Commands

```sh
# regenerate both images (both sources: the second is a cross-check, see the README)
python3 tools/extract_frontpanel_eprom.py A6_front_panel_eprom.bin \
    firmware/A6_OS_v1.40.13b.syx firmware/A6_bootloader_1.20.31.syx
python3 tools/extract_frontpanel_eprom.py --mirror A6_front_panel_eprom_dual.bin \
    firmware/A6_OS_v1.40.13b.syx firmware/A6_bootloader_1.20.31.syx

# verify a checkout
shasum -a 256 -c SHA256SUMS
```

`--report` prints one line per input file, including how many records the protocol's splice
clipped. There is no test suite and no build; the check is that the two commands above reproduce
`SHA256SUMS`.

## Git

- Branch `pasei/<topic>`, full PR, the user merges. Never push `main`, never force-push.
- One commit per change; regenerate the images in the same commit as a pipeline change, and say
  in the body what the byte counts moved from and to.

## Boundaries

- Always: state the verification status next to any claim about the images — extracted and
  structurally verified, never observed working on a machine.
- Ask first: rewriting `firmware/*.syx`, re-deriving the packing assumptions, or shipping a
  third image variant.
- Never: hand-edit a `.bin`, claim the image "works" or "is the factory EPROM", or describe the
  extraction as lossy — the records cover every addressed byte, and the unaddressed 244 bytes of
  the vector area are `0xFF` fill, not lost data (see the README's coverage note).
