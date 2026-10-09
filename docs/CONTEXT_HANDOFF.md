# FilmEditor continuation handoff

## Current task and release lineage

Repository: https://github.com/georgieee03/filmeditor, branch `main`. Native rebuild baseline `9d2cdd1`; home-screen zero-FPS fix `c7faceb03e771843826dfc8ec9e42705c5542e4d`. The current follow-up repairs Photos import, moving-video rendering and UI refresh behavior, adds local enhancement jobs, and expands native multi-clip acceptance. Use `git rev-parse HEAD` and `git ls-remote origin refs/heads/main` for the exact current release; historical hashes below identify evidence, not the newest build.

The user authorized implementation, installation, a normal GitHub push, documentation, and cleanup of generated test media. Preserve the current user project and camera originals. The user's current timeline contains JPEG copies received from Photos; those files really are images. Re-import the movie representations instead of changing filename extensions or pretending to reconstruct motion from a JPEG. The current repair must not rewrite the user's existing timeline while testing.

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

## Managed engine dependency

The Shotcut adapter remains pinned at the revision in `patches/shotcut-mcp-stills.json`. `patches/shotcut-mcp-stills.patch` plus its manifest describe the complete managed delta; `scripts/managed_shotcut.py` verifies source and bundled hashes. Bootstrap recognizes clean pinned sources, the exact earlier still-duration patch and the exact current patch. Unknown edits, staged changes or unknown untracked files fail without reset or deletion. The earlier in-app Codex still-duration work was preserved and incorporated into this tracked patch. Include its MIT license when distributing it. Do not edit `.deps` alone and call the source release reproducible.

## Evidence and limits

Read the newest entry in [VALIDATION.md](VALIDATION.md). Machine-local reports are ignored. Native multi-clip acceptance uses real WKWebView UI controls, MLT and FFmpeg on three synthetic moving sources, including audio-first stream order; it measures exact frame counts and visual/audio effect signals. It is distinct from actual OS pointer dragging and Photos picker interaction. Short actual user-video acceptance compares source/render frame motion, full decode, uninterrupted playback, image classification and source hashes. Apple Vision acceptance uses known synthetic targets, rotation and a scene cut; it does not prove arbitrary human-selection quality.

The final source suite passed 199 tests. Installed-app enhancement acceptance rendered and played exactly 72 portrait frames; all 26 bundled Python/UI files and the managed adapter matched source, and the signature verified. A separate native OS-pointer check passed three bin-to-timeline drags, a vertical cross-track move and edge trimming; the same run completed and previewed stabilization from the native controls. Synthetic quality checks measured reduced jitter and noise with exact audio sample alignment at 44.1/48 kHz. Test media/workspaces and the obsolete app backup were removed, user originals/timeline hashes were preserved, and the normal project library reopened with the quickstart visible.

The app is locally ad-hoc signed, not notarized. Sequence previews are rendered, not a GPU realtime effect engine. Stabilization is modest deshake, reframing is 2-D region-following with a fixed fill crop, and audio cleanup is deterministic filtering rather than isolated speech reconstruction. Sources are bounded to five minutes per enhancement job. No claim of 90% Premiere/CapCut parity, `.prproj` interchange, multicam, full Lumetri/scopes, transcription, third-party Adobe plug-ins, broad HDR/Log compatibility or long-film reliability is warranted.

The system Photos picker was visibly opened and its library/video categories were displayed. Automated end-to-end selection and direct external Photos drag require separate evidence; controlled provider/file-promise tests alone do not prove real Photos delivery. The old JPEGs remain in the user project until the user replaces them with videos.

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
