# FilmEditor

FilmEditor is a macOS video editor with a native app window, a hands-on multitrack timeline, local rendering, and Codex working against the same project. It is designed around a familiar desktop editing workflow, with project media, source and program monitors, timeline tracks, clip controls, effects, and export.

FilmEditor ships with a native Swift AppKit shell, editor interface, and Python backend. The app hosts the interface in WKWebView, loads it from `filmapp://`, and uses stdio IPC to reach the backend; native mode does not start a TCP web server or launch a browser. Import from the Mac file panel, the system Photos picker, or a direct Photos file drag. Source selection distinguishes actual movie streams from image posters; the engine uses explicit video/audio stream selection for rendering.

The app supports direct timeline editing, rendered sequence previews, basic effects, titles, captions, and music fitting. See [validation](docs/VALIDATION.md) for the exact native, real-engine, synthetic, and short user-source checks; [the context handoff](docs/CONTEXT_HANDOFF.md) for continuation; and [Photos and playback](docs/PHOTOS_IMPORT.md) for import behavior and troubleshooting. Tests do not establish long-form reliability, broad camera compatibility, or Premiere parity.

FilmEditor builds its own editing workflow around the locally installed Shotcut MLT runtime and FFmpeg. A pinned Shotcut MCP adapter connects the editor backend to the installed engine. FilmEditor is an independent project: it does not include Adobe Premiere code, claim Premiere project compatibility, or claim complete feature parity with Premiere Pro.

## Editing workflow

- Create a project and sequence, then import video, audio, images, references, and LUT files. Use the **Photos** bin button or **File > Import from Photos…** for videos and pictures in Photos, or drag them directly into the app. Existing JPEG copies are stills; re-import the movie to recover motion and audio.
- Choose **Auto from media** for a new project when you want the first supported video to set the empty sequence profile. Import uses local FFprobe metadata to report geometry, rational frame rate, codec, color tags, and audio streams. It configures a supported automatic sequence once, preserves explicit settings and existing edits, and routes unresolved technical facts to a bounded Codex setup turn. Metadata does not identify a camera Log curve or verify a color conversion.
- Drag source media into video or audio tracks. Move clips, trim their edges, split at the playhead, duplicate, delete, and snap edits to timeline edges.
- Use the source monitor, frame stepping, and the rendered sequence preview. The sequence preview is an MLT render; it is not a real-time effect playback engine.
- Change clip timing, scale, rotation, opacity, volume, brightness, and saturation from the controls panel. Installed MLT services determine which engine operations are available; the color controls are basic SDR adjustments, not a camera color-management system.
- Choose an Editing, Color, Audio, or Graphics layout preset. The presets adjust widths of the existing panels and timeline height; dragging a panel divider saves that dimension locally. They do not provide dockable or floating panels. Browser acceptance verified every preset and saved divider width stayed within a 1280×800 viewport after reload; the final native build still needs this interaction rechecked.
- Open **Edit tools…** for numeric frame-based roll, slip, and slide edits; constant speed or reverse; a cross dissolve; text titles on a new video track; sequence markers; and SRT caption import, editing, and optional burn-in. These edits use the same revision-safe timeline transaction and undo history.
- Open **Enhance** for stabilization, subject-following portrait/square reframing, and dialogue cleanup. These local jobs create separate full-length assets, with preview, cancellation and explicit revision-safe replacement. The first release is bounded to five-minute sources and supported SDR video. [Enhancement guide and limits](docs/ENHANCEMENTS.md).
- Undo and redo timeline edits. Revision checks prevent stale changes from replacing newer work.
- Export H.264, HEVC, or ProRes to a new output file.
- Ask Codex to inspect the active project and make changes on the same timeline.

The **AI edit** action accepts a creative brief and directs Codex to inspect actual frames, edit the active sequence, render and review an MLT preview, correct problems, and complete an H.264 export. Technical setup and creative editing are separate flows: an import-only setup turn cannot invent a brief or export a film. Live ChatGPT-authenticated tests passed through app-server stdio and FilmEditor's private Unix MCP from both the source backend and installed native AI button. They used synthetic media, edited one one-second clip, accepted the MLT preview and render-status tool responses, fully decoded the H.264 export, recorded no failed MCP calls or approval events, and left the source unchanged. Neither test establishes camera-footage or long-form results. See [automatic detection and AI setup](docs/AUTO_DETECTION.md) and the [Codex and future provider handoff](docs/AI_PROVIDER_HANDOFF.md).

The music workflow searches Openverse and imports a selected CC0, Public Domain Mark, or CC BY track with its source and license; it can also fit a user's authorized local audio file. Codex can estimate beats and add a soundtrack to a dedicated Music track with gain, fades, and optional looping while preserving picture and dialogue. Beat estimates do not automatically cut the edit. Synthetic MLT mix/decode, a live Openverse import/decode, browser UI import/Fit/export with credits, and native Music-track persistence after reopen passed. Openverse metadata does not guarantee copyright clearance, and chart music must be supplied with permission. See [music research and safety requirements](docs/MUSIC_RESEARCH.md).

The editor makes 720p proxies for video sources above 1280×720 and for codecs that cannot be previewed directly; the media engine renders from original sources. A synthetic 3840×2160 H.264 source produced a 1280×720 proxy with its original unchanged. This short synthetic check does not establish sustained 4K or long-form performance. Imported copies, project files, caches, and exports live in the user's project workspace, outside this source checkout.

## Codex access

FilmEditor connects to the installed Codex CLI using the user's existing ChatGPT sign-in. It does not use a separately billed API key or read authentication tokens. Run `codex login` and `codex login status` in Terminal if sign-in is needed.

Full access is the default for new FilmEditor projects: Codex receives `danger-full-access` and an approval policy of `never`, so it can read and change files accessible to the Mac user and run commands without per-action approval prompts. This is the permission model explicitly requested for this rebuild. Use trusted prompts and workspaces, and avoid placing secrets or unrelated private files in the project. The interface also offers restricted permission modes when a project is configured to use them.

Codex inference is provided by the signed-in ChatGPT service. Text, selected references, and other material included in a prompt may be processed by that service under the user's account settings. Local media rendering does not make Codex inference offline. Subscription and service limits still apply.

## Build and install on macOS

Requirements: macOS 13 or newer with Xcode Command Line Tools (including `swiftc` and the macOS SDK), Python 3.10 or newer with a relocatable standalone runtime available to bundle, Git, Node.js 22 or newer with npm, a current Shotcut.app with its MLT engine, discoverable FFmpeg/FFprobe executables, the Codex CLI, and a ChatGPT sign-in. HyperFrames rendering also requires a compatible Google Chrome installation. The build bundles the selected Python runtime, FilmEditor sources, and pinned adapters; Shotcut, Node.js, Chrome, and Codex remain installed separately.

From the repository root, select Python 3.10 or newer, prepare the pinned bridge and motion-rendering dependencies, then build and install the native app. On the configured development checkout, `.local/mac-env.sh` selects the installed runtime. For a fresh checkout, fill in paths to a Python 3.10+ interpreter and its relocatable standalone CPython prefix. Ordinary framework-based Homebrew Python installations are not guaranteed to produce a relocatable app runtime. The macOS Command Line Tools may provide Python 3.9, which is too old for these scripts.

```bash
if [[ -f .local/mac-env.sh ]]; then
  source .local/mac-env.sh
  python_prefix=()
else
  export FILM_PYTHON="/path/to/standalone-cpython/bin/python3.12"
  python_prefix=(--python-prefix "/path/to/standalone-cpython")
fi
"$FILM_PYTHON" --version
"$FILM_PYTHON" scripts/bootstrap.py
"$FILM_PYTHON" scripts/build_mac_app.py "${python_prefix[@]}" --install
```

The bootstrap step downloads project dependencies into the ignored `.deps/` directory and applies the tracked Shotcut adapter patch. Builds verify the pinned commit and exact patch; unknown dependency edits are preserved and rejected, never reset. It does not install system applications or change Codex authentication. The build script checks the installed engine/runtime paths and installs `FilmEditor.app` under `~/Applications`.

Open the app from Finder or run:

```bash
open "$HOME/Applications/FilmEditor.app"
```

`./open-editor.command` and `./start.command` also open the installed app (or the build under `build/` when it has not been installed). If an earlier loopback service still owns the workspace, stop it with `./stop-editor.command` before the first native launch. The `.command` launchers support `--legacy-server` for service tests and `--no-browser` for compatibility.

Quit FilmEditor to stop its owned backend process. Reopening the app starts a fresh backend for the same local project library. The build script applies an ad-hoc signature by default; a Developer ID signature and notarized download are not claimed.

## Native app transport

The app loads its UI from the `filmapp://app` scheme and communicates with its owned Python backend over standard input and output. The backend does not bind a web port. External Codex clients connect through the project's owner-only Unix-domain socket and its local connection file. Start FilmEditor before connecting an external client, and do not run two agents that edit the same project at once.

The native menu bar exposes project selection and creation, media import, timeline export, and the workspace in Finder. Edit and View menus provide the common timeline actions, render queue, inspector, engine tools, and diagnostics. Manual acceptance verified the native import and export panels, including a fully decoded saved export. The six-step quickstart opens on a clean first run, all steps are reachable, and Start editing closes it into the editor; it creates no sample media. Finder file drop is implemented but still needs an acceptance run.

## Security and media boundaries

FilmEditor is a local, single-user desktop app. Full-access Codex can use the Mac user's filesystem and command-line privileges. Keep project scope clear in prompts and preserve separate backups of camera originals. FilmEditor does not modify originals outside the selected project workspace as part of import or rendering.

Use current, trusted MLT and FFmpeg builds. Do not assume a codec or color transform from a camera name or file extension. Apple Log, HDR, RAW, Dolby Vision, and other camera formats require format-specific verification. Synthetic source tests and generated media do not establish that real camera footage or long-form editing works.

Imported HTML and scripts are not trusted editor assets. Do not ask Codex to run untrusted project code. See [SECURITY.md](SECURITY.md) for the authority model and reporting instructions.

## Development and evidence

Source lives in `film/`, `web/`, and the native app sources added by the rebuild. Build outputs, project data, generated video and audio, local reports, and machine-specific settings are ignored by Git.

Current validation distinguishes unit/regression tests, real MLT timeline renders, browser automation, installed native-app checks, and live Codex authorization. The current source suite passes 162 tests. The native home form also passes default Automatic, manual Vertical at 24 fps, and Manual 60 fps switched back to Automatic, followed by reopening each saved project. Automatic creation uses a valid 30 fps starting profile until media is imported. Synthetic detection tests cover a supported tagged portrait, uncertain color metadata, a rotated source at exact fractional FPS, and a tagged PQ render guard. A browser test verifies detection and brief dispatch with Codex mocked; separate live ChatGPT-authenticated tests through the source backend and native AI button edited one 30-frame synthetic clip through the shared Unix MCP, rendered H.264, fully decoded the export, and produced no approval events. The corrected native AI-button run accepted preview and render-status tool results without failed MCP calls or shell recovery; it fully decoded the export, left the source unchanged, and preserved the app signature. The final installed app passed general launch/reopen acceptance; all 23 bundled source files match the frozen source. No personal camera footage was used. Finder-originated file drop and individual advanced edits in WKWebView remain unverified. See [docs/VALIDATION.md](docs/VALIDATION.md) and [docs/CONTEXT_HANDOFF.md](docs/CONTEXT_HANDOFF.md).

Contributors and coding agents should read [AGENTS.md](AGENTS.md) before changing source. The project uses a lead implementer and bounded support/research agents when the user requests parallel work; see the role and ownership rules there.

## Research and licenses

The engine and editor comparisons are in [docs/REBUILD_RESEARCH.md](docs/REBUILD_RESEARCH.md), and the current Adobe feature comparison is in [docs/PREMIERE_PARITY.md](docs/PREMIERE_PARITY.md). FilmEditor's source license applies to original project code only. Third-party runtime and bundled components keep their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
