# Native macOS acceptance checklist

Use a disposable project and short synthetic clips. Do not use or overwrite camera originals for the smoke test. Record versions and evidence in the ignored local `reports/` directory; do not commit that report.

## 1. Build and launch

- Use Python 3.10 or newer, the selected Shotcut/MLT and FFmpeg/FFprobe runtime, and the documented dependency bootstrap.
- Build/install `FilmEditor.app`, launch it from Finder and through `open-editor.command`, and confirm one native window appears. The launcher should open the installed app (or `build/FilmEditor.app` if no installed copy exists); no external browser should launch.
- On a clean app workspace, confirm the guided quickstart appears, explains the first project/import/edit/export steps, and can be dismissed or completed without adding sample videos or songs to the project.
- Confirm the window loads from the `filmapp://app` scheme and the backend reports ready over stdio IPC.
- Inspect the process's own network sockets. Native mode must not have a TCP listener. Confirm the Unix-domain socket is owner-only and the project connection file does not appear in diagnostics or logs.

## 2. Native project and media flows

- Create a project and sequence. Use File > Import to select a synthetic video and audio file. Confirm the native file panel closes and the imported media appears in the project bin.
- Use the Photos bin button, File > Import from Photos, and the normal file panel's Choose from Photos button. Select multiple videos and verify their actual movie/audio streams, source playback, timeline placement, and rendered moving pictures. Repeat a direct drag from Photos. Never count a JPEG poster or a video element whose clock advances over a frozen image as success.
- Select a source or timeline clip and open Enhance. Exercise stabilization, automatic/manual portrait crop and audio cleanup; inspect warnings, preview the separate result, apply with current revision, then undo. Explicit sequence-format matching must preserve FPS and frame counts. Check cancellation, preserved originals and cleanup of partial output. Use known jitter/noise/impulse fixtures to distinguish successful processing from actual quality improvement and audio alignment.
- Verify a movie with audio at absolute stream 0 and video at stream 1 renders moving pictures, including after replacement/relink. Test in a disposable project; preserve the user's library and existing edits.
- Drag a file from Finder into the app's import area. Confirm the media is copied into the project workspace and the source file hash remains unchanged.
- Import a short synthetic 3840×2160 H.264 clip. Confirm its inspection proxy is 1280×720 and its source hash is unchanged. This verifies proxy generation only; it does not qualify sustained 4K or long-form performance.
- Use the native File > Open/New flows and File > Show Workspace action. Quit and reopen the app; confirm the project library and saved sequence are available.
- Export from the native File > Export flow. Confirm the save panel selects a new output path and the app does not overwrite an existing file without the expected system confirmation.

## 3. Direct editing and render

- Drag clips from the bin onto video and audio tracks. Move a clip, trim each edge, split at the playhead, change a track, and verify snapping.
- JPEG/PNG stills use a five-second default duration even when FFprobe reports a single-frame duration such as 0.04 seconds. Verify both bin drag and source Insert/Append, and render a frame near the end of the still. This requires the tracked managed Shotcut MCP patch; test clean bootstrap and bundle hash verification.
- Lock a track and confirm drag, trim, split, delete, inspector edits, and manual editor API calls cannot modify its clips. Unlock it and confirm editing works again. Separately test whether the installed engine applies its lock metadata to a Codex/MCP edit, and record the result. Verify vertical-only clip movement changes tracks without changing its time.
- Duplicate and delete a selected clip. Use Undo and Redo to restore and reapply the edit. Save, quit, reopen, and confirm the timeline revision and clip positions persist.
- Change brightness and saturation on a synthetic SDR clip, render a preview, and confirm the controls retain their values after reselecting the clip and reopening the project. Record which installed MLT services were available.
- Use **Edit tools…** to apply frame-based roll, slip, and slide adjustments, constant speed/reverse, and an adjacent cross dissolve. Generate a title on a new video track, add and remove a ruler marker, import an SRT file, edit a cue, and save the captions. Render and decode the sequence. Recheck these controls in the native WKWebView after the final app build; the current browser/static-route synthetic acceptance does not establish native behavior.
- Render and play the sequence preview, export a short H.264 file, and fully decode the output with the selected FFmpeg build. Confirm frame count, duration, audio presence, and no render failure. The sequence preview is an offline MLT render, not real-time playback. Keep source clips synthetic and outputs outside the source checkout.
- Confirm proxy or waveform preparation does not block unrelated editor actions. Keep final render sources pointed at original imported media, not inspection proxies.

## 4. Codex and external MCP

- Confirm the active account is the user's existing ChatGPT sign-in. API-key authentication is refused, and no token or account secret is displayed.
- For a new project, confirm the selected mode is full access: thread `approvalPolicy=never`, `sandbox=danger-full-access`, and turn `sandboxPolicy.type=dangerFullAccess`.
- Submit one benign command scoped to the disposable project. Confirm it completes without a command/file approval event and without accessing personal media or unrelated files. A live successful command proves the permission path for this test; it does not prove creative quality.
- Ask Codex to move or trim one synthetic clip. Confirm the same timeline changes and that the revision-safe editor sees the saved result.
- Connect an external MCP client using the private Unix socket from the project's connection file. Confirm it reads and edits the same project. Do not print or copy the socket token into the report.

## 5. Music discovery and soundtrack

- Search Openverse anonymously with a short query. Limit default results to CC0, Public Domain Mark, and CC BY; display the track title, creator, source page, and exact license. Treat these as unverified catalog metadata, not a guarantee of copyright clearance. Do not require paid credentials or fetch arbitrary user-provided URLs.
- Import one selected result through the fixed provider API. Re-fetch details, re-check the permitted license, validate the downloaded audio, bound its size/time/redirects, preserve source attribution and license details in the project, and remove partial files after a failed import.
- Analyze no more than the requested span and report estimated beat markers without overstating confidence. Do not automatically cut or move picture to beats.
- Add the soundtrack beneath existing video on a dedicated Music track. Fit it to the sequence range by trim or explicit loop, apply the requested gain and start/end fades, preserve existing picture/dialogue edits, and include the exact range in the revision-safe undo transaction.
- Render a short video with the music through actual MLT, decode the complete output, and verify the resulting audio content. Check exact duration, trim/loop behavior, gain/fades, attribution, undo, stale revision handling, and behavior when a track is locked. Keep downloaded test media and outputs out of the source checkout.

## 6. Lifecycle and source hygiene

- Quit from the app menu. Confirm the app-owned Python backend and its child processes stop, the socket is removed, and no other FilmEditor workspace process was terminated.
- Reopen the app and repeat a project read, then quit again.
- After final app acceptance, remove all test video and song files, open a fresh project workspace, and confirm the first-run quickstart works without test media. Verify cleanup did not remove user media, unrelated projects, or source files.
- Review Git exclusions and the source file list. No media, exports, private reports, workspaces, runtime dependencies, `.app`, credentials, or machine-specific configuration may be staged.

## Evidence limits

Unit tests and fixture-browser tests establish application logic only. MLT plus synthetic media establish an engine path for those exact formats and settings. A live ChatGPT command establishes that one authenticated turn completed with the selected permission policy. Native macOS acceptance establishes only the tested app build and host. None of these checks alone proves Premiere Pro feature parity, project interchange, real-camera Log/HDR/RAW or Dolby Vision handling, long-film stability, color accuracy, or notarized distribution.

## Acceptance record

The frozen source passed 161 unit/regression tests in 16.664 seconds. Source-level ingest acceptance passed synthetic rotated geometry at exact 30000/1001 FPS, one revision-safe profile/history edit, decoded portrait export, and PQ render blocking while an unused HDR bin asset did not block SDR rendering. The browser detection test used actual FFprobe/MLT and mocked only Codex dispatch. A separate live ChatGPT-authenticated Codex test used app-server stdio and FilmEditor's private Unix MCP to configure synthetic media, inspect frames, author exactly one 30-frame clip, render a new H.264 export, fully decode it, and complete without approval events or source changes. The installed native bundle passed launch/reopen checks on synthetic H.264 media: WKWebView source playback and seek, a rendered and decoded 151-frame MLT preview, restored timeline state, no TCP listeners or connections, external stdio MCP against the same workspace, unchanged source checksum, and clean stopping of the owned backend. The native media-info and AI-brief dialogs opened, the six-step quickstart appeared and entered editing without adding sample media, and the local music Fit track persisted after reopen. Manual native interaction passed import, bin-to-track drag, cross-track move, end trim, native Save-panel export, and full decode of the saved output. A separate live Codex check in the app was read-only project inspection and a benign shell command with zero approval events. The corrected native AI-button acceptance passed: it authored the requested 30-frame clip, completed preview and render-status MCP calls without failures or shell recovery, fully decoded the H.264 export, produced no approval events, left the source unchanged, and retained a valid app signature after the live turn. The earlier parser issue was fixed by stripping only optional content annotations at the stdio MCP boundary. The final general launch/reopen run passed; the final build report confirms that all 23 bundled source files match and the ad-hoc signature remains valid after live AI and reopen. The advanced-controls dialog opened without applying its controls. Browser/static-route acceptance passed advanced synthetic MLT edits/render, workspace presets and saved divider dimensions at 1280×800, local-song import/Fit/export/credits, and a separate live Openverse import/decode. Final cleanup removed all owned test-media directories, left zero test video/audio in the user workspace and reports area, and preserved external camera originals. Finder-originated external file drop, applying advanced edits and layout presets in native WKWebView, full native catalog and credit export, long-form editing, and real-camera Log/HDR/RAW formats remain unverified. Record any new acceptance using the OS/build, engine versions, media type, operation, decode, audio, attribution, undo/lock, Codex, file-panel, lifecycle, cleanup, failure, and unverified-area fields in an ignored report under `reports/`.
