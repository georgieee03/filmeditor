# Music-led AI editing: use and validation

## Use it in the app

1. Create a project and import actual movies with **Photos**, **File > Import from Photos**, a Photos drag, or the file importer. A JPEG poster cannot supply motion. Import the local audio file as music.
2. Let technical detection finish. If source metadata is inconclusive, provide the intended output size/frame rate without inventing missing camera information.
3. Open **AI edit** and give a concrete creative brief. For example: “Review these four clips and my imported song. Make a 25–30 second portrait film, choose the best source ranges, reorder and trim picture to musical phrases, lower camera audio, review a rendered preview, and export a new H.264 file. Preserve originals.” Include any dialogue, shots or existing edits that must stay.
4. Watch the tool activity and wait for the final export to complete. Play the completed export from **Exports**. The timeline remains editable. Ask for specific revisions if shot choice, pacing or synchronization needs improvement.

The **Music Fit** control alone adds/fits a soundtrack while preserving picture. The separate creative brief authorizes picture changes. Native rendering is local; Codex model inference uses the signed-in ChatGPT service. Selected frame images are sent to the model. A local MP3 must be supplied by the user; the application does not fetch protected chart music from streaming services.

## Observed native workflow — 2026-10-11

This run used the installed dark-themed AppKit/WKWebView app, the real **AI edit** dialog/button, ChatGPT-authenticated Codex, FilmEditor's private Unix MCP, MLT and FFmpeg. It was not a mocked model or manually pre-authored creative timeline. The editor selected `gpt-6.1-sol` at `xhigh`; the separate test/review agents used the requested Sol/Luna/Astra roles at `max`.

Four actual portrait H.264/AAC videos were delivered by a Photos-to-app file-promise drag. Their imported hashes matched separately exported unmodified copies. This comparison does not assert direct access to the Photos library originals. Earlier picker acceptance is recorded separately in [VALIDATION.md](VALIDATION.md); the creative harness imports those verified copies through native upload IPC and does not itself prove Photos provenance.

One bounded automatic technical setup turn ran before one creative submission. Missing sample-aspect metadata remained explicitly unresolved; the creative brief requested 720×1280 square-pixel output at 30 fps. Setup did not create a creative timeline. Before picture authoring, the model received 16 actual source images covering all four videos. A private response-item audit matched delivered typed images to local PNG hashes; tool names or text summaries alone were not treated as proof of visual review. Two rendered contact sheets were delivered during preview review.

| Check | Observed result |
| --- | --- |
| Picture authoring | Eight segments, seven cuts, all four movies; different source ranges and shot order chosen by Codex |
| Song treatment | One excerpt starting at 31.1 seconds, −4 dB, 0.15-second fade-in and 0.8-second fade-out; camera audio muted |
| Preview and export | Separate completed jobs at the same saved revision; 806 frames, 26.867 seconds of video, 720×1280 at 30 fps; final H.264/AAC |
| Native playback | Preview and exact final export played and sought to every segment; visible pictures, advancing transport, no native media/JavaScript/navigation errors |
| Independent picture check | All 806 frames decoded; motion in all eight segments; inspected first/last frames, shot interiors and both sides of all cuts |
| Independent song check | Audio alone recovered a 31.100-second source offset; early/middle/late waveform correlations 0.999835–0.999906, above mismatched-time controls |
| Musical timing | Four of seven cuts within two frames of independent spectral-flux onsets; other source-onset distances 84, 109 and 141 ms; nominal window/hop timing allowance 20.5 ms, not a calibrated error bound |
| Audio continuity | No silent 250 ms window at −60 dBFS; no clipped samples in the bounded 16 kHz mono diagnostic; this is not a stereo true-peak claim |
| Permissions | No approval events; technical setup did ask a source-metadata question, so this is not a claim of zero questions |

The visual result is a coherent short car/highway montage with shot-size changes. One later shot remains visibly soft/shaky. Timing measurements support a music-led first cut, not exact beat editing, automatic stabilization or polished creative quality. The original Codex edit and export are retained privately at the user's request; do not silently improve or delete that comparison artifact. A complete copy in the normal project library uses durable media paths. Its active MLT differs only by those paths; the exact historical MLT is archived privately. Native reopen, source/final playback and a newly rendered 806-frame preview passed after relocation, with the original final movie hash unchanged.

Two model calls initially failed schema validation: a Boolean `progressive` value where an integer was required, and a black-duration threshold below 0.05 seconds. Codex corrected both. The first harness attempt failed before inference because of test-file transport; the later evidence parser falsely rejected nested image results in `thread/read`. Both harness issues were corrected without rerunning the creative edit. Nested diagnostic image/audio payloads are redacted before serialization. Those failures remain part of the evidence history and must not be described as a zero-error test.

A separate eight-second native control test on these four sources exercised five-frame trimming, Undo/Redo, brightness/saturation, full preview/export decode and changing pictures. It is component evidence, separate from AI authorship. The source suite passed 206 tests. No production app code changed in this validation follow-up; the installed app remains the Photos/dark-interface build documented in [VALIDATION.md](VALIDATION.md).

## Reproduce with your own authorized media

Run serially on the 8 GB Mac. Close other FilmEditor validation workspaces first. These scripts retain their project and outputs; keep them private and decide cleanup explicitly. A live new run consumes the account's normal Codex service allowance. `--resume` only observes/reopens the existing run; it never submits a new creative turn, imports, edits or renders.

```sh
source .local/mac-env.sh
"$FILM_PYTHON" -E tests/native_music_workflow.py \
  --output-dir /private/tmp/filmeditor-music-check \
  --video /absolute/path/clip-1.mp4 \
  --video /absolute/path/clip-2.mp4 \
  --video /absolute/path/clip-3.mp4 \
  --song /absolute/path/authorized-song.mp3

# Continue evidence collection without a second creative submission:
"$FILM_PYTHON" -E tests/native_music_workflow.py \
  --resume /private/tmp/filmeditor-music-check
```

Read `manifest.json`, `acceptance.json`, the initial native workflow report, event records and sanitized tool evidence together. A resumed native report has a separate observe mode; preserve the original workflow report. The harness checks source hashes, picture authoring, song analysis/placement, revision/job association, native playback, full output decode and backend shutdown. Its persisted tool evidence distinguishes nested stored images from actual model-facing image delivery; independently verify delivery when making a visual-understanding claim. Read-only command use is recorded and reviewed separately from MCP edits.

Pass a JSON array of actual cut times (seconds) from the saved timeline to the independent audio check. It does not consume the app's beat grid and does not call a model:

```sh
"$FILM_PYTHON" -E tests/music_output_analysis.py \
  --song /absolute/path/authorized-song.mp3 \
  --export /absolute/path/completed-export.mp4 \
  --cuts-json /absolute/path/cut-times.json \
  --output /absolute/path/private-audio-report.json
```

The audio checker requires FFmpeg/FFprobe, bounds source audio to ten minutes and output analysis to one minute, verifies a regular 8 ms analysis clock, and tests early/middle/late waveform structure against mismatched excerpts. Correlation and transient proximity remain diagnostics; they do not establish musical phrasing, continuous source identity, licensing or subjective quality.

Use a Python environment with NumPy and Pillow for exact-frame contact sheets (these are analysis dependencies, not app runtime requirements):

```sh
python3 -E tests/music_visual_review.py \
  --movie /absolute/path/completed-export.mp4 \
  --native-report /absolute/path/workflow-native.json \
  --output /absolute/path/private-visual-review \
  --ffmpeg /absolute/path/ffmpeg
```

Inspect the generated sheets. This checker binds the movie hash and timeline revision, checks decoded frame count, and samples by exact frame number. Motion statistics alone cannot accept an edit aesthetically. Keep source media, rendered frames, full session records, connection files and machine reports out of Git. The [AI provider handoff](AI_PROVIDER_HANDOFF.md) describes how another model provider must preserve the same editing, media and revision contracts.
