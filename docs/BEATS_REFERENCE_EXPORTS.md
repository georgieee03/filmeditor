# Music beats, reference review, chat usage, and exports

This guide describes the implemented workflow and tool contracts. The separate [validation record](VALIDATION.md) documents source tests, native playback and the real authenticated desktop/chat round trip, including their limits.

## Music beat cut plans

FilmEditor can produce a `film_music_cut_plan` with picture-cut frame boundaries. Planning does not edit the timeline. It returns the project frame rate, exact cut frames, segment ranges, the beat source and phase origin, source-song offset, quantization error, and warnings. Applying those frames is a separate revision-safe timeline edit.

The song's source offset and beat phase are separate values. The offset says where playback starts inside the file; the phase says where the beat grid or first observed onset lies in that file. FilmEditor does not assume that the beginning of a song is a downbeat. An explicit BPM still needs an explicit phase, or a phase obtained from analyzing an imported song.

When both BPM and phase are estimated from an imported song, the plan prefers observed onset times within the analyzed window. If observed peaks are unavailable, it may use the analyzer's estimated regular grid and reports that choice. Supplying BPM or phase switches planning to a regular grid anchored to the selected phase; the response warns when analyzed values were overridden. An analyzed plan must be covered by the decoded audio window; it will not extrapolate beat times beyond that coverage. `beats_per_cut` sets the spacing in whole beats.

Each selected beat time is rounded to the nearest frame using the project's rational frame rate. The resulting timing error is bounded to half a frame: for example, at 30 fps that is at most 1/60 second; at 30000/1001 fps it is at most 1001/60000 second. This is only frame-quantization error relative to the supplied or estimated beat time. It does not guarantee musical accuracy. Onset analysis is heuristic and can mistake half or double tempo, miss quiet or syncopated beats, and cannot identify musical meter or a downbeat. Short openings or closings are reported as partial beats instead of being silently recut.

Try prompts such as:

> Plan cuts every two beats over the next 20 seconds of my imported song, starting at 12 seconds into the song. Show the exact timeline frames and explain whether the BPM and phase are observed or explicit. Do not change the picture yet.

> I know this song is 120 BPM and the first beat is at 0.18 seconds. Plan one cut per beat from a 5-second song offset using the current project's frame rate. Include partial-beat and quantization warnings.

Ask Codex to plan and apply a music-led picture edit in one request when that is what you want. Planning and timeline authoring are separate tool operations; an add-music-only request still preserves the picture.

## Reference video review and audio extraction

Open **Reference**, choose an imported edited video, set a start time and an analysis window, then choose **Analyze transitions**. The window defaults to 60 seconds and is bounded to five minutes. The analyzer samples video at the chosen rate (1–12 samples per second, 8 by default) and reduces frames to 96×54 RGB for measurement. It reports candidate hard cuts, gradual changes, fades, or other abrupt changes, plus actual sampled review frames and a contact sheet. When audio is readable, it also estimates audio pulse and compares candidate boundaries with observed or estimated beats.

These are candidates, not a certified edit list. Pixel changes can be caused by camera motion, flashes, lighting, or overlays; smooth or quick cuts can be missed. A flattened movie cannot reveal the exact original transition effect. Review the contact sheet and individual source frames before naming a transition. Audio pulse estimates are heuristic and may be half or double the musical tempo; they do not prove a cut was intentional or establish meter.

Choose **Extract audio** to create a separate generated WAV asset from the video's selected/default audio stream. The full selected stream can be extracted only when it is no longer than five minutes; for longer audio, choose a range no longer than five minutes. Extraction produces 48 kHz, stereo, signed 16-bit PCM, keeps the original video unchanged, and does not edit the timeline. Extracting a reference soundtrack does not establish rights to reuse it.

Try:

> Analyze Reference 1 from 30 seconds for up to 45 seconds. Show the contact sheet and candidate cut times. Mark transition identity and audio alignment as uncertain, and do not alter my timeline.

> Extract the audio from Reference 1 between 30 and 75 seconds as a new WAV asset. Keep the source video and current edit unchanged.

## Chat token meter

The chat meter uses the installed Codex app-server's `thread/tokenUsage/updated` notification. The local adapter validates the notification's thread ID and uses its `tokenUsage.total`, `tokenUsage.last`, and `modelContextWindow` fields. The supported breakdown includes total, input, cached-input, cache-write-input, output, and reasoning-output token counts. Missing, malformed, or unavailable counts stay unavailable; the meter does not substitute zero.

The small meter appears in the Codex chat panel after valid usage data arrives and shows counts only. It does not reveal prompt text or reasoning content.

**Thread total** is the app-server's cumulative usage snapshot for the active FilmEditor conversation. **Latest model usage** uses the latest reported `last.totalTokens` snapshot and, when the server provides a positive `modelContextWindow`, shows it as a proportion of that model context window. This is not an aggregate of every turn. Usage is scoped to the saved thread and persisted with that project's private workspace state so it can be restored when the project reopens. Starting **New chat** clears that thread's saved meter data.

The meter is not account-wide usage, plan quota, remaining allowance, billing, or cost. FilmEditor does not derive a price or report an account balance from token counts. The event name is documented in the official [Codex App Server documentation](https://learn.chatgpt.com/docs/app-server); the exact usage fields are taken from the installed CLI schema used by the current adapter.

The current chat provider is the installed Codex CLI/app-server authenticated with the user's existing ChatGPT sign-in. FilmEditor exposes its editing operations separately through its local MCP tool boundary. The tool boundary can be consumed by an MCP client, but the chat usage meter is Codex-specific. A Claude chat or usage adapter is a future possibility and is not implemented here.

Try:

> Explain the difference between the thread total and latest model usage shown in the meter, and tell me what it cannot report.

## Project routing for native MCP and in-app Codex

The native desktop's external root MCP connection follows the currently selected project. Each external MCP client must call `film_assets` to bind its session to the selected project before using project tools. Switching projects invalidates that binding; the client must call `film_assets` again and inspect the newly selected project before continuing. A stale request is rejected instead of being routed silently into another project.

The in-app Codex connection starts its MCP process with `--pin-project` and an explicit workspace path. That includes the legacy root workspace. Each library project owns a separate workspace and saved Codex conversation, so changing the native project selection does not repoint an already-open in-app conversation.

## Completed video delivery

Completed video renders are published into the selected library root, normally `~/Movies/FilmEditor/[project name]/`. The folder name is sanitized and claimed for that project ID; an existing unowned or colliding folder is left alone. Only a completed render under that project's `exports/` folder is considered. Before first publication, the backend checks the completed file with FFprobe and requires a real video stream. It skips non-video formats, known audio-only render settings, attached-picture cover streams without video, and files with no non-artwork video stream. An FFprobe failure returns a delivery warning, separate from render success.

Publication stages a private copy on the same filesystem, then uses a no-clobber link into the project folder. The job ID makes retries idempotent. Project-folder ownership and file identity are checked before a saved item is listed; a collision, replaced folder, or failed integrity check returns a warning instead of overwriting a file. `film_export_info` reports the folder and only manifest-verified delivered videos. The library folder registry is private in `~/Movies/FilmEditor/.film/delivery-folders.json`; each project's publication manifest is private in `<workspace>/.film/deliveries.json`. The verified-source stamp cache is also kept in the workspace's private `.film` directory.

The delivery folder is videos-only. Project files, audio, credits, logs, previews, generated reference WAVs and caches remain in the workspace. Previously completed jobs may also publish when their status is polled; the render queue checks the latest 30 finished jobs for the project. There is no exhaustive historical backfill. Internal exports remain under the project workspace's `exports/` folder and available through the Exports view. The delivery folder lists only files published and recorded by the delivery manifest.

Try:

> Export the current project to a new H.264 file. When it completes, report the delivered video path or the delivery warning. Leave older exports where they are.

> Show the verified videos in this project's delivery folder and the folder path.

Finished exports play in a separate **EXPORT** monitor mode. Play, pause, frame stepping and scrubbing use that file's duration without rendering another sequence preview. Select **Program** or interact with the timeline to return to sequence editing.

Native live validation is recorded in [the validation record](VALIDATION.md). The bounded H.264 workflow does not establish acceptance of every supported export preset or arbitrary media.
