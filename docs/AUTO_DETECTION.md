# Automatic media detection and AI fallback

Reviewed on 2026-10-08. This document distinguishes what the imported file reports, what FilmEditor can infer, and what its current rendering path has actually demonstrated. It also defines the review requirements for automatic setup and provider-independent AI editing. Implementation acceptance is recorded separately below.

## Read local evidence first

[FFprobe](https://ffmpeg.org/ffprobe.html) can report container and stream information as JSON. Use a bounded local probe of the immutable imported copy, cache it by file identity, and retain the probe version and relevant fields. Missing fields, `N/A`, zero-denominator rationals, and unsuccessful probing are unknown values, not defaults. Skip attached pictures when choosing timeline video; audio artwork must not select a video project profile. Still-image demuxers also need explicit exclusion.

| Evidence | Safe interpretation | Boundary |
| --- | --- | --- |
| Container and codec names | The detected container and coded media type | An extension or camera filename is not sufficient. Multiple video streams require a stated selection policy. |
| Width, height, display matrix, sample aspect | Coded dimensions plus a derived display geometry | Handle quarter-turn orientation once. Preserve sample/display aspect or defer unsupported combinations. Conflicting or arbitrary transforms need review. |
| Average rate and timing base | Preserve exact rational values such as `30000/1001` | A guessed timing base is not proof of constant cadence. A short sample cannot certify an entire file as CFR. |
| Pixel format and component precision | Report known component depth and sampling | Do not parse the `420` in `yuv420p` as bit depth, or treat 10-bit storage as HDR evidence. |
| Primaries, transfer, matrix, range and relevant side data | Report a tagged color description with its evidence | Metadata can be absent, conflicting, or wrong. Known source tags do not certify the output pipeline. |
| Audio stream layout/rate | Describe present audio streams | Do not infer dialogue, language, ownership, or editorial purpose from technical metadata. |

[FFmpeg's AVStream contract](https://ffmpeg.org/doxygen/trunk/structAVStream.html) defines `avg_frame_rate` as an average and `r_frame_rate` as a guessed timing base. Rate disagreement can flag suspected variable timing; agreement is not a proof of constant frame intervals. If needed, inspect a bounded timestamp sample and name its analyzed interval. FFprobe warns that interval seeking may start at a different position than requested.

[FFmpeg rotation handling](https://ffmpeg.org/ffmpeg.html#Video-Options) applies display transforms during transcoding when automatic rotation is enabled. The [MLT avformat producer](https://www.mltframework.org/plugins/ProducerAvformat/) also enables automatic rotation by default. A derived portrait profile must therefore not be accompanied by an unnecessary second rotation filter. Validate a real rotated source through both source preview and sequence export.

MLT can select an absolute `video_index` or a relative `vstream`; `vstream` takes precedence. A file's default stream therefore need not be the stream a saved producer renders. The current release conservatively sends files with multiple non-artwork video streams for analysis and requires a derived source containing the chosen stream before rendering. It does not certify all streams by checking only the default one. ([MLT stream selection](https://www.mltframework.org/plugins/ProducerAvformat/#video_index))

## Configure only an authorized empty sequence

[MLT profiles](https://www.mltframework.org/docs/profiles/) specify dimensions, rational frame rate, progressive/interlaced state, sample aspect, display aspect, and colorspace. Preserve rational fields instead of rounding a fractional frame rate to an integer. The installed engine's `set_profile` operation preserves frame numbers; changing FPS on an existing edit can change its timing. Automatic import configuration must therefore refuse a populated sequence.

A new project can start in an explicit **Auto from media** mode. An output format/FPS chosen by the user takes precedence over detection. Merely having an empty timeline does not make an explicit format choice disposable. A multi-file batch needs a reproducible recommendation policy, such as the largest compatible group with an import-order tie break, and must identify its reference asset. Call that a recommendation, not a recovered creative intention.

Capture the selected sequence path and revision before probing. At commit, take the existing shared edit lock and workspace selection lock, verify the same active path, revision, asset identities, automatic mode, and empty sequence, and apply one ordinary revision-checked transaction. Record the decision with the sequence identity and resulting revision. Concurrent imports must not repeatedly reconfigure it; a later file must not silently replace the already accepted output profile. Keep source hashes and original copies unchanged.

## Color certainty and rendering readiness differ

[FFmpeg's color enums](https://ffmpeg.org/doxygen/trunk/pixfmt_8h.html) distinguish primaries, transfer characteristics, matrix coefficients, and range. SMPTE ST 2084 is the PQ transfer; ARIB STD-B67 is HLG. Complete consistent tags can establish **tagged PQ** or **tagged HLG**. Generic logarithmic tags, a flat-looking frame, 10-bit ProRes, or a camera-model filename do not establish a specific manufacturer's Log encoding or its required transform. Do not manufacture a LUT or replace unknown values with confirmed Rec.709.

The installed engine's `set_color_workflow` writes `shotcut:processingMode`, `shotcut:colorTransfer`, and profile colorspace. That metadata edit is not evidence that the separate Melt rendering process performs the intended high-precision color processing. Its HDR encoder presets set pixel format and output tags; those settings alone do not demonstrate correct image conversion. FilmEditor's ordinary H.264 preview/export explicitly uses 8-bit `yuv420p`.

Consequently, a file can have certain HDR metadata while still requiring AI review because the preview/output pipeline is unverified. Keep those two facts separate. Unresolved HDR, camera Log, contradictory tags, or unsupported color processing must not silently enter an ordinary SDR export. A valid resolution needs a supported source-to-output plan, the actual conversion or preservation settings, representative rendered-frame review, and output metadata/decode verification. A model's confident statement, a profile label, or a tag-only operation does not resolve the issue.

Ordinary untagged video with known 8-bit component precision may continue through the existing SDR working path with a warning; this is an explicit working assumption, not confirmed source color. Missing precision, untagged high-bit media, explicit PQ/HLG/Log, conflicting color evidence, and Dolby Vision/HDR side data require further review. The render guard must examine resources reachable from the actual rendered timeline, including speed-adjusted and nested sources and retired bin assets. An unrelated imported file or unused XML producer should not block an otherwise supported sequence.

The implemented guard follows actual `resource`/`warp_resource` inputs rather than stale undo bookkeeping. It rejects nested cycles and ambiguous XML identities. Inspection is bounded to 512 unique media resources, 32 nested MLT files, depth eight, 16 MiB per XML file, 32 MiB cumulative XML, and a 120-second total inspection budget with bounded owned probe subprocesses. Those bounds may refuse unusually large projects; they are not long-film performance evidence. The shared render entry points apply this check to manual and AI requests before launching the engine.

Technical geometry may still be reported while color remains unresolved. State what can proceed and what remains unverified; avoid claiming that all imported camera formats are now automatically normalized.

## Bound the AI fallback

Run deterministic local analysis first. Queue a technical-review turn only for a new unresolved asset/configuration signature, not on every refresh, reopen, or repeated probe. Serialize or coalesce requests when the agent is already busy. Authentication, rate limits, provider failure, cancellation, and an unresolved result must remain visible states; none may fall back to fabricated certainty.

The automatic prompt should contain a small allowlist of technical fields and explicit reasons for uncertainty. Exclude GPS/location tags, arbitrary comments, unrelated file paths, and entire raw metadata dictionaries. Media names and tags remain untrusted data. Keep the instruction outside that data block and tell the model to use existing local tools to gather only needed evidence. If frames are required, request a bounded representative set and record their timestamps. Local probing is local; metadata or frames included in an AI request may leave the Mac for the selected model provider. Do not describe model analysis as offline.

The implemented automatic request names unresolved asset IDs. Codex's submission context includes the library's IDs, labels, names, and paths. Public `film_assets` omits cached raw metadata and probe stamps; public `film_probe` returns the same normalized technical view used by detection. Raw FFprobe output remains local for engine use. Sentinel regressions verify that GPS and arbitrary comments do not appear in these default tool responses. This minimizes routine responses; it is not a data-access sandbox. The user-authorized full-access agent can still read files, run commands, and request frames or other tool data.

The import fallback has authority to diagnose and configure the media workflow. It must not invent a creative brief, recut clips, download music, or publish an export simply because a file was imported. The user has separately authorized AI editing and rendering: execute that flow using the current request or saved project brief, inspect before editing, and render to a new path. The explicit **AI edit & render** dialog also offers a clearly stated delegation when no current or saved brief exists: choose a concise edit using natural sound. That affirmative action supplies creative scope; an import alone does not. Technical uncertainty is not a reason to ask again for already-authorized editing permission.

## Codex and Claude share the project contract

The [MCP tool specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) defines discovery, schemas, structured results, and tool-error reporting independently of the model provider. Keep FilmEditor operations behind that shared boundary: assets/status, technical detection/configuration, inspection, revision-checked edits, media/frame inspection, music, render start/status/cancel, and shared undo history. The renderer and project state remain authoritative.

[Claude Code supports local stdio MCP servers](https://code.claude.com/docs/en/mcp). A future Claude adapter can connect to the same FilmEditor stdio entry point and app-owned Unix transport. It must discover installed tool schemas rather than copy imagined APIs or start a second owner for the project. Codex app-server permission/event fields are not Claude protocol fields; implement authentication, streaming, cancellation, and permission policy in the provider adapter and validate them against the installed client.

Provider handoff should preserve the active project path, fresh revision, imported asset IDs/hashes, technical evidence and unresolved questions, actual brief, last committed actions, running render IDs, attribution paths, and user-authorized scope. Reinspect before the next write. Do not transfer credentials, private provider session files, or treat an interrupted response as proof that an edit failed. Keep the user's selected provider; do not silently send media to another service on failure. MCP compatibility is an integration path, not evidence that a Claude adapter has been implemented or tested.

## Acceptance required

- Classifier fixtures: missing/invalid rationals, attached artwork, still images, multiple streams, rotated portrait, non-square pixels, interlaced material, suspected VFR, tagged SDR/PQ/HLG, untagged 10-bit media, contradictory tags, and misleading filenames.
- State regressions: explicit profile choice, populated sequence, concurrent imports, stale revision, changed active path, asset removal during analysis, one shared undo entry, and no duplicate automatic AI turn for the same unresolved evidence.
- Real media: preserve source hashes; configure a synthetic rotated/fractional-rate source correctly; render and fully decode the resulting sequence; verify dimensions/rate and actual orientation. Distinguish those checks from camera-specific color acceptance.
- AI fallback: a known technical case causes no model turn; an uncertain case submits only the bounded intended evidence; failure remains unresolved; any resulting write uses a fresh path/revision; an explicit edit/render request follows the saved brief and reports the completed render.
- Provider migration: repeat shared-tool conformance, stale-write rejection, event routing, cancellation, and permission-mode checks for the actual Claude adapter before claiming it works.

## Reviewed implementation and evidence

The independent `tests/test_ingest_review.py` suite passed **24 tests** after the final corrections. It covers exact rational/rotated geometry, attached artwork, invalid metadata, missing sections, privacy sentinels, explicit unsupported transfer and gamut, apparent SDR with HDR side data, misleading image extensions, multiple video streams, actual speed-input precedence, nested HDR/cycles, repeated nested sources, unused HDR producers, manual/nonempty protection, and active-project/revision changes during probing. These use controlled metadata and project fixtures; they are not decode or camera-color tests.

Separate local acceptance records were inspected:

| Record | What it demonstrates | Evidence boundary |
| --- | --- | --- |
| `reports/rebuild/ingest/synthetic-acceptance.json` | Real FFprobe and MLT: rotated 180 × 320 profile, exact `30000/1001`, one shared history transaction, preserved tracks, fully decoded portrait export, tagged PQ recognition and render refusal, and an unused HDR producer not blocking SDR output. | Synthetic inputs; no camera-specific HDR conversion proof. |
| `reports/rebuild/detection/ui-acceptance.json` | Real imported 270 × 480 `24000/1001` tagged media configures without an AI turn. Unknown metadata sends a setup-only prompt; the explicit edit button sends the intended brief. No JavaScript errors or source changes. | Chromium UI; AI dispatch is mocked in this report. |
| `reports/rebuild/ai/live-edit-render.json` | Actual ChatGPT-authenticated Codex inspected frames, configured the project, authored a 30-frame clip, and completed a fully decoded H.264 export through shared native Unix MCP/app-server transport, with no approval events and unchanged source. | A bounded one-second synthetic edit; not general creative quality, long-film reliability, or real camera interpretation. |

A later native run completed the edit/export but exposed client parsing failures on annotated upstream preview images and completed-render links. Final-output success alone did not prove a clean MCP path. The [implementation review](REVIEW_FINDINGS.md) records the exact floating-point priority incompatibility, the stdio response normalization, four regression checks, and direct installed-client acceptance of normalized images/links. After rebuilding, the actual native AI button passed the live edit, preview and final export with no failed MCP tools, no approvals, a fully decoded output, unchanged synthetic source, and a valid signature after shutdown. These results are recorded locally in `reports/rebuild/ai/native-live-edit-render.json` and `native-ui.json`; they remain bounded synthetic evidence.

The final source review found no unresolved blocking defect in this detection scope. The release lead owns the final aggregate suite and rebuilt native-app checks after integration. Camera Log/RAW transforms, calibrated HDR delivery, exhaustive VFR/interlaced conversion, HDR still-image color management, and a working Claude provider adapter remain outside the demonstrated result. The legacy MLT profile description may retain an earlier label; numeric dimensions and rational rates are authoritative.
