# FilmEditor continuation handoff

## Current task and release lineage

Repository: https://github.com/georgieee03/filmeditor, branch `main`. Native rebuild baseline `9d2cdd1`; home-screen zero-FPS fix `c7faceb03e771843826dfc8ec9e42705c5542e4d`; previous playback/enhancement release `d7d53a9a0a1bea42f05ba4efefec1c41df78264c`. The latest follow-up validates the complete native Codex music-edit workflow on four real movies and a user-supplied song, adds independent output checks, and preserves the original result for user review. Production app code remains the Photos/drop/dark-interface release `923c98b848c17802b996d23ee59404a2d14d8955`. The previous follow-up repaired stream selection and UI refresh behavior and added local enhancement jobs. Use `git rev-parse HEAD` and `git ls-remote origin refs/heads/main` for the exact current release; historical hashes below identify evidence, not the newest build.

The user authorized implementation, installation, a normal GitHub push, documentation, and cleanup of generated test media. **The latest instruction overrides cleanup for the final Codex music edit: keep its final movie and complete editable project.** Do not silently replace that original creative result with a manually corrected version. Preserve the current user project and camera originals. The user's current timeline contains JPEG copies received from Photos; those files really are images. Re-import the movie representations instead of changing filename extensions or pretending to reconstruct motion from a JPEG. The current repair must not rewrite the user's existing timeline while testing.

## Latest music workflow: continuation state

- **Completed:** one real native creative submission used four movie sources, eight segments/seven cuts, a fitted local song, preview review and an 806-frame H.264/AAC export at 720×1280/30 fps. An observe-only reopen passed. Independent visual and audio evidence supports a music-led first cut; four of seven cuts were within two frames of detected transients and one shot retains blur/shake. See [MUSIC_WORKFLOW_VALIDATION.md](MUSIC_WORKFLOW_VALIDATION.md).
- **Retained user artifact:** normal library project **Massive — Codex original edit**. Its private `RETAINED_EDIT.md` identifies the exact movie, original revision, path-only relocated revision and archived historical MLT. Keep the whole project tree; a single MLT is not portable because media references are absolute. Machine-specific paths and evidence are in ignored `reports/music-workflow/retention.json` and `retained-native.json`.
- **Evidence map:** ignored `reports/music-workflow/` contains native control acceptance, source-preservation baselines, test-run reports and retention checks. `tests/native_music_workflow.py` retains the original workflow, incremental events, sanitized MCP results and observe-only readback. `tests/music_output_analysis.py` independently checks the song, timing and bounded audio continuity. `tests/music_visual_review.py` generates exact-frame sheets and motion diagnostics. None substitutes for listening or subjective approval.
- **Do not repeat:** do not rerun the AI creative brief just to collect evidence. `--resume <run-folder>` is observation only. A nested text/JSON image representation in Codex `thread/read` is not proof that the model missed the image; verify actual delivery separately and redact nested media payloads. Two corrected model contract errors must remain recorded.
- **Next action:** let the owner watch the preserved original export. Further creative revisions should use a separate copy/export and a fresh `inspect_project` revision; keep the original comparison artifact. For engineering changes, run appropriate source checks and the bounded native workflow serially, preserving all user sources.

## Architecture and ownership

- `native/FilmEditor.swift` and `native/main.swift`: AppKit/WKWebView application, `filmapp://app` UI, native menus/import/export, owned backend and media streaming. Native mode uses stdio JSON IPC and a private Unix MCP socket, with no TCP listener or external browser.
- `native/MediaImport.swift`: PHPicker selection and AppKit file-promise delivery. Prefer movie representations over poster images; stage temporary provider URLs inside callbacks. Preserve project/generation binding across asynchronous imports. Cancellation and shutdown stop owned work; general external file promises have no universal cancellation mechanism.
- `native/VisionReframe.swift`: bounded, on-device Apple Vision helper. Oriented 720p sampling, initial human/face/saliency selection or manual first-frame region, sequential object tracking, actual decoded timestamps, held crop warnings for lost targets/cuts. Emits geometry only. The build compiles `Resources/FilmVision` and passes its path through `FILM_VISION_HELPER`.
- `film/core.py`, `film/desktop.py`, `film/editor.py`: workspace state, lifecycle, stream probing, media preparation, shared revision-checked editing and undo, real MLT rendering and IPC. Keep original files immutable. `heavy_jobs=1`, HyperFrames workers=1, engine workers=1 on this 8 GB Mac.
- `film/enhance.py`: persistent cancellable full-source derivative jobs for FFmpeg deshake, smoothed tracked crop and dialogue cleanup. Processing and applying to the timeline are separate. Completed output is a new asset; manual UI and Codex use the same current-revision edit path to apply it.
- `web/app.js`: stable media/timeline DOM, visible-thumbnail preparation, source playback, coalesced frame requests and render polling. `web/enhance.js`: enhancement UI. Other separate modules provide music, advanced timeline tools, AI setup and quickstart.
- `film/codex.py`: existing ChatGPT-authenticated Codex app-server connection. New projects default to `approvalPolicy=never`, `sandbox=danger-full-access`, turn `sandboxPolicy.type=dangerFullAccess`; saved project modes are preserved. Do not add approval gates to authorized work. This is broad Mac-user file/command authority, not media isolation.

## Bugs fixed and why

1. Home Automatic mode omitted a disabled FPS field, and `Number(null)` became zero. It now bootstraps at 30/1, then auto-configures an eligible empty sequence from imported media. Invalid backend profiles fail before project allocation.
2. Photos can offer a movie and a JPEG poster. The native route now prefers movie file representations and promised files, offers **Photos**, **File > Import from Photos**, and a file-panel Photos accessory. The picker uses the current representation; unmodified export from Photos remains the fallback. See [PHOTOS_IMPORT.md](PHOTOS_IMPORT.md).
3. A real short H.264/AAC video had audio stream 0 and video stream 1. MLT produced a fully decodable but frozen picture until the correct absolute stream selectors were supplied. Managed adapter creation, replacement and relinking now write explicit default non-artwork video/default audio indices and remove conflicting relative aliases. Playback time advancing alone is insufficient evidence: acceptance compares actual moving frames.
4. One-frame technical empty-track placeholders produced an extra black tail. Marked placeholders are consumed by the first insertion; intentional or unmarked legacy gaps are preserved.
5. Expensive startup media preparation and repeated DOM replacement interrupted interaction. Preparation is bounded/lazy, unchanged timeline nodes survive polling, missed completed previews are recovered by revision, and stale frame completion does not clear newer source overlays.
6. An intermittent Photos drag accepted the drop but received no files: promise negotiation waited for a WKWebView JavaScript callback after the native drop callback returned. Start `receivePromisedFiles` synchronously while the drop is active, and gate only backend import on the later import-kind response. Keep native `.copy` ownership through entered/updated/prepared stages. Diagnostic reads must not materialize JPEG pasteboard URLs when a movie promise exists.
7. MLT writes exports to a hidden staging movie, then renames it into the completed output. Publishing that staging name in the Exports bin exposed an unfinished or vanished file to playback. Output discovery must exclude hidden intermediate files and tolerate files disappearing during a directory scan. Native acceptance should select the exact completed export and verify its pictures, audio, and full decode.

## Managed engine dependency

The Shotcut adapter remains pinned at the revision in `patches/shotcut-mcp-stills.json`. `patches/shotcut-mcp-stills.patch` plus its manifest describe the complete managed delta; `scripts/managed_shotcut.py` verifies source and bundled hashes. Bootstrap recognizes clean pinned sources, the exact earlier still-duration patch and the exact current patch. Unknown edits, staged changes or unknown untracked files fail without reset or deletion. The earlier in-app Codex still-duration work was preserved and incorporated into this tracked patch. Include its MIT license when distributing it. Do not edit `.deps` alone and call the source release reproducible.

## Evidence and limits

Read the newest entry in [VALIDATION.md](VALIDATION.md). Machine-local reports are ignored. Native multi-clip acceptance uses real WKWebView UI controls, MLT and FFmpeg on three synthetic moving sources, including audio-first stream order; it measures exact frame counts and visual/audio effect signals. It is distinct from actual OS pointer dragging and Photos picker interaction. Short actual user-video acceptance compares source/render frame motion, full decode, uninterrupted playback, image classification and source hashes. Apple Vision acceptance uses known synthetic targets, rotation and a scene cut; it does not prove arbitrary human-selection quality.

The previous 2026-10-08 release passed 199 source tests. Its installed-app enhancement acceptance rendered and played exactly 72 portrait frames; all 26 bundled Python/UI files and the managed adapter matched source, and the signature verified. A separate native OS-pointer check passed three bin-to-timeline drags, a vertical cross-track move and edge trimming; the same run completed and previewed stabilization from the native controls. Synthetic quality checks measured reduced jitter and noise with exact audio sample alignment at 44.1/48 kHz. That release's test media/workspaces and obsolete app backup were removed, user originals/timeline hashes were preserved, and the normal project library reopened with the quickstart visible. The newest validation entry records this release's separate Photos, dark UI, and four-movie acceptance.

The app is locally ad-hoc signed, not notarized. Sequence previews are rendered, not a GPU realtime effect engine. Stabilization is modest deshake, reframing is 2-D region-following with a fixed fill crop, and audio cleanup is deterministic filtering rather than isolated speech reconstruction. Sources are bounded to five minutes per enhancement job. No claim of 90% Premiere/CapCut parity, `.prproj` interchange, multicam, full Lumetri/scopes, transcription, third-party Adobe plug-ins, broad HDR/Log compatibility or long-film reliability is warranted.

The latest route ledger records real picker delivery (user selected the movies) and actual Photos-to-editor dragging. After an intermittent stall was reproduced and fixed, two repeated automated drops and the user's own two-video drop completed on the final installed build. The user confirmed delivery. Read the newest validation entry for the exact evidence. The actual-media harness consumes existing imported asset IDs; it does not inject media or prove Photos provenance by itself. The old JPEGs remain in the user project until the user replaces them with videos. Optional native `--import-report` diagnostics are local, bounded metadata; see PHOTOS_IMPORT.md. Never publish them or infer original-library checksums from hashes of imported copies.

## Build, test and install

```sh
source .local/mac-env.sh
"$FILM_PYTHON" scripts/bootstrap.py
"$FILM_PYTHON" -m unittest discover -s tests -v
node --check web/app.js
node --check web/enhance.js
"$FILM_PYTHON" scripts/build_mac_app.py --install
"$FILM_PYTHON" tests/native_project_creation.py
"$FILM_PYTHON" tests/native_multiclip_acceptance.py --app build/FilmEditor.app
"$FILM_PYTHON" tests/native_enhance_acceptance.py --app build/FilmEditor.app
"$FILM_PYTHON" tests/native_vision_acceptance.py
"$FILM_PYTHON" tests/enhance_acceptance.py --output-directory /tmp/filmeditor-enhance-test
```

Use Python 3.10+; the old Command Line Tools Python is too old. Install Shotcut, Node 22+, Chrome, Codex with existing ChatGPT login and a relocatable standalone Python as described in [README.md](../README.md). Launch `~/Applications/FilmEditor.app`; the app owns startup/shutdown. Do not restart the retired loopback LaunchAgent for ordinary use. Run media acceptance serially on the 8 GB Mac. Test scripts create temporary workspaces and clean them; explicit retained-debug workspaces are the caller's cleanup responsibility.

## AI-provider migration and publishing

[AI_PROVIDER_HANDOFF.md](AI_PROVIDER_HANDOFF.md) documents authentication, JSONL app-server events, the external MCP shim, content annotation compatibility, shared tools, permissions, cancellation and history. Claude should replace the model adapter while retaining the app's MCP and revision/undo contracts. Never export ChatGPT credentials or assume Codex permission strings translate to another provider. [ENHANCEMENTS.md](ENHANCEMENTS.md) documents the provider-independent enhancement jobs.

Before publishing, regenerate `SOURCE_MANIFEST.json` for intentional public files, run `scripts/publish.py --dry-run`, review the staged diff, and push normally without rewriting remote history. Never include app bundles, dependency installs, private transcripts, connection tokens, user footage, generated test media, runtime state or machine-specific reports. Record the commit and remote verification in the final response rather than introducing a self-referential commit hash into this file.
