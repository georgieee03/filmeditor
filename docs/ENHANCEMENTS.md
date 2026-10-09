# Stabilization, reframing and audio cleanup

Select an imported source or an ordinary timeline clip, open **Enhance**, and choose an operation. A local job creates a separate, full-length media asset. Preview the result before using **Use on selected clip**. Replacement preserves the selected clip's source range and enters shared Undo history. Originals are never overwritten. Clips with speed changes, proxies or adjacent transitions may require a separate insertion or removal of the transition before replacement.

## Available processing

| Operation | Implementation | Practical limits |
| --- | --- | --- |
| Stabilize | Installed FFmpeg `deshake`, with strength presets | Corrects modest camera shake; can distort moving edges. It is not a rolling-shutter repair or optical-flow warp stabilizer. |
| Auto reframe | Apple Vision detects an initial person, face or salient region, then follows that region. FFmpeg crops along a smoothed path for 9:16, 1:1 or 4:5. | Fixed aspect-ratio fill crop; no adaptive subject-size zoom, semantic identification or 3-D camera reconstruction. Fast motion and occlusion can lose the target. |
| Manual target tracking | Nominate the target center on the **first source frame**. Vision tracks a region around it. | Choose a textured subject; the fixed nomination region is 24% of the source in each dimension. The center refers to the full source, not a trimmed timeline frame. |
| Clean audio | Local FFmpeg noise reduction, speech-oriented high-pass filtering, compression and limiting | Intended for dialogue. It can alter music and cannot recover missing or heavily clipped speech. It is not voice isolation or transcription. |

The current jobs accept sources up to five minutes. Video processing requires supported 8-bit SDR, known geometry and a single video stream. Reframing requires square pixels and an orthogonal display rotation. Known HDR/Log and unsupported geometry fail with an explanation. Untagged known 8-bit media carries an explicit SDR-assumption warning; it is not a verified camera transform. Only one heavy media job runs at a time on the configured Mac. Cancel stops the owned helper/filter process and removes incomplete output. Completed generated media remains part of the project.

Auto selection can find no clear subject; choose a manual target in that case. A lost target or detected scene change holds the last position and produces a warning. Split separate shots into separate source files when one trajectory cannot represent them. Review the rendered crop, especially faces near an edge. Processing uses a bounded sampling rate and an approximate smoothed path; it is not guaranteed to keep every moving subject centered.

In a bounded installed-FFmpeg synthetic check, stabilization reduced measured camera-position RMS from 6.990 to 3.049 px and frame-step RMS from 12.428 to 4.651 px while retaining nearly all measured target motion (238.97→238.19 px span). Audio cleanup reduced synthetic rumble/hiss by 7.764 dB and retained about 94% of two test tones. The tests also verified exact decoded sample counts and zero compensated impulse drift at 48 and 44.1 kHz, retained tail audio, and preserved all decoded video frames in the audio-cleanup video path. This measures known synthetic signals, not perceived quality on real footage or music.

The generated portrait file does not silently change a landscape sequence. Use the explicit sequence-format control when the whole sequence should become portrait; this keeps its frame rate and frame counts but changes the layout of other clips. Undo restores the prior sequence. For stabilization and reframing together, stabilize first and reframe the stabilized result so analysis and cropped pixels share the same coordinates.

## Codex and other providers

The same jobs are exposed through `film_enhance_start`, `film_enhance_status` and `film_enhance_cancel`. Start takes an imported `asset_id`, operation, strength, and optional reframe aspect/target parameters. Poll to completion, inspect warnings and the generated asset, then inspect the timeline again before a revision-checked `replace_item_media` or insertion. A completed processing job alone does not alter the timeline.

Apple Vision runs locally in the bundled `FilmVision` helper. Its output is bounded geometry JSON; it sends no frames to a model service. Codex may separately request frames through FilmEditor's existing tools when the user asks it to review the result. The native backend passes the helper path through `FILM_VISION_HELPER`. A future Claude adapter can reuse these MCP tools unchanged; see [provider handoff](AI_PROVIDER_HANDOFF.md).

## Research and scope

CapCut's documented [Auto Reframe](https://www.capcut.com/tools/auto-reframe), [Motion Tracking](https://www.capcut.com/tools/motion-tracking) and [Voice Enhance](https://www.capcut.com/tools/voice-enhancer) informed the workflow. Its motion tracking attaches text/effects to a target; FilmEditor's current reframe follows a target with the crop instead. Existing FilmEditor tools also expose speed/reverse, titles, dissolves, SRT captions, music fitting and animation through the installed engine. This is a list of concrete tools, not CapCut or Premiere parity.

Implementation references: [Apple object tracking](https://developer.apple.com/documentation/vision/tracking-multiple-objects-or-rectangles-in-video), [Apple saliency](https://developer.apple.com/documentation/vision/cropping-images-using-saliency), [AVAssetImageGenerator timing](https://developer.apple.com/documentation/avfoundation/avassetimagegenerator/requestedtimetolerancebefore), [FFmpeg deshake](https://ffmpeg.org/ffmpeg-filters.html#deshake), [FFmpeg afftdn](https://ffmpeg.org/ffmpeg-filters.html#afftdn), and [MLT vidstab](https://www.mltframework.org/plugins/FilterVidstab/). The installed FFmpeg has `deshake` but lacks the `vidstabdetect`/`vidstabtransform` pair; availability of MLT `vidstab` alone does not prove a working two-pass lifecycle, so this release uses the tested FFmpeg path.

See [validation](VALIDATION.md) for measured tests and their limits. Synthetic tracking success is not proof of person-selection accuracy across real footage.
