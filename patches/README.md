# Managed Shotcut adapter patch

FilmEditor pins Shotcut MCP at `f1b78d350a9992296f0b7f11a55c04819ea25730` and applies `shotcut-mcp-stills.patch` through `scripts/bootstrap.py`. The patch keeps genuine JPEG/PNG/WebP stills at their requested edit duration and requires positive probe evidence before choosing QImage. Missing movie duration produces a timing error instead of an image producer. Creation and replacement use the same rule.

Created, replaced and relinked media use the probed absolute video/audio stream indices, including movies whose audio stream comes first. Attached artwork is excluded, default streams are preferred, absent streams are disabled, and relative selector aliases are cleared. Timewarp producers inherit the same direct selectors. Newly created or emptied tracks mark their technical one-frame placeholder so first placement consumes it without deleting intentional or legacy one-frame gaps. Existing user timelines are not migrated.

`shotcut-mcp-stills-legacy.patch` records the exact earlier local still-duration correction so bootstrap can preserve and upgrade that recognized checkout. `shotcut-mcp-stills.json` records the base pin, patch checksums, and clean/legacy/patched file hashes. Other tracked edits, unrelated untracked files, or unrecognized staged edits cause preparation to stop without resetting the checkout or index.

The app build requires the final managed patch and verifies copied runtime hashes. Its `Contents/Resources/shotcut-adapter.json` records the packaged pin and patch identity. Tests establish patch reproducibility and source behavior; native playback and engine/media acceptance require their own checks.
