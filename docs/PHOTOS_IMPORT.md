# Photos, playback, and media preparation

## Import videos from Photos

Open a project, then use the **Photos** button beside the media-bin import button, **File > Import from Photos…**, or **Choose from Photos…** in the normal Import Media file panel. The macOS picker displays the Photos library and collections. Select up to 200 items and choose Add. Only selected items are delivered to FilmEditor; the picker does not require permission to scan the entire library.

You can also drag files from Photos to the media bin or a timeline track. The native view requests AppKit file promises before inspecting ordinary file URLs. When Photos advertises a movie promise alongside a JPEG poster URL, this selects the promised movie. It keeps native copy acceptance through drag entry, updates, and release, while respecting the source's permitted operations. Promise negotiation starts synchronously within drop acceptance; the asynchronous import-kind lookup gates only the later backend import. Promised files use one shared destination and are restored to receiver order and each receiver's reported filename order even if delivery callbacks arrive out of order.

A JPEG file URL or image promise is still a valid image import. The app cannot infer whether that JPEG was an intended photograph or a movie poster from its extension alone. Promise-first handling does not guarantee that a promise contains a movie; the advertised promise type, delivered file, and media probe provide separate evidence. If a drag cannot supply the movie, choose **File > Export > Export Unmodified Original** in Photos, then import that exported movie.

The picker requests the current Photos representation using the generic `.movie`, `.image`, or `.audio` type family, preferring a movie over an image when both are advertised. Generic families avoid requesting a specific encoding that can still trigger transcoding with `.current`. Each temporary file is copied before its provider callback returns, and a failed movie download is reported without falling back to its poster. “Current” does not guarantee a camera-original file: Photos may have edited or converted the asset, and Cinematic videos may contain rendered depth effects. To explicitly use the original, export its unmodified original from Photos. If the item is stored in iCloud, let Photos download it first. FilmEditor keeps the Photos library intact.

A JPEG already imported into a project is a still image. It cannot recover the original video's motion or audio. Import the actual movie again. The bin and Source monitor identify stills and videos separately. Stills default to five seconds when newly inserted; existing saved clip durations are preserved.

## Why the playback fix spans the app and engine

The reported project contained JPEG copies, while its Photos library contained videos. Separately, a real Photos-exported MP4 exposed a renderer bug: its audio stream was absolute index 0 and its video was index 1. The adapter did not select those streams explicitly. A technically valid output could decode and advance in a video element while its picture remained frozen. The corrected adapter selects the actual non-artwork video and audio streams from probe metadata, including replacement and relink operations. Output acceptance compares decoded pictures, not only file duration or playback time.

The still-image fix requires positive probe evidence. A movie without readable duration is not turned into an image, and a filename suffix does not override the actual container and streams. The tracked adapter patch is under `patches/`; bootstrap applies it to the pinned upstream commit, and native builds verify the exact patched runtime files. Unknown local dependency edits are preserved and rejected for manual reconciliation.

## Responsiveness and lifecycle

Source selection first reads technical metadata; compatible files can play without a full waveform pass. Only visible bin items and timeline media queue thumbnail work, serially. Proxy preparation is reserved for sources needing a compatible preview. Unchanged project and render polls preserve the existing timeline and player. Frame requests are coalesced, and completed renders for the current revision replace stale previews even if a short job finished between polls.

Sequence playback still requires an MLT-rendered preview. This is not GPU real-time effects playback. HDR/Log and unsupported stream configurations retain their existing review/render guards.

Native import progress appears in the status bar. Provider downloads are cancelled when navigating away or quitting, and the backend stops copying before publishing an unfinished import. Files successfully imported earlier in the same batch remain saved. AppKit file promises have no general receiver-cancellation API; their external provider may finish asynchronously, but a cancelled import cannot add those late results to a different project.

## Optional import diagnostics

For an import acceptance run, launch the native app with `--import-report /absolute/path/to/import-report.jsonl`. The report's parent directory must already exist. An existing report is appended to; each launch has a separate session identifier. New reports use private file permissions. Keep these reports in the disposable test workspace, outside the source checkout.

The report records app-owned import callbacks: the menu, Photos button, file-panel, or drop route; picker focus and completion; advertised provider and pasteboard types; promised file types; native drag stages and copy support; per-receiver request, filename count, callback count, and error type; received file basenames, byte counts, and extension-derived types; backend asset IDs and results; and callbacks ignored after cancellation, navigation, or shutdown. The `import-started` route values are `menu`, `photos-button`, `file-panel`, `file-panel-photos`, `drop-promises`, and `drop-file-urls`. An extension-derived type is a filename hint, not a decoded-media verdict. Drag-stage metadata is recorded once per stage per drag. Diagnostics inspect advertised types during drag entry and updates; they do not materialize alternate file URLs or promise receivers. Actual receiver metadata is recorded only when that drop route is accepted.

Writes run on a serial background queue. Each launch records at most 1,000 events and 4 MiB of report data; individual JSON records larger than 64 KiB are skipped. The report does not include media contents, full source paths, Photos library identifiers, or credentials. Basenames can still be private, so retain the report locally and do not commit it. A `picker-presented` event alone proves presentation, not selection or file delivery.

## Source ownership and verification

- `native/MediaImport.swift`: system Photos picker, generic movie-first provider delivery, bounded sequential copies, ordered file-promise completion, cancellation, and bounded metadata reporting.
- `native/FilmEditor.swift`: native menu/file-panel entry points, native drag ownership through release, project-scoped completion, import status, diagnostics, and staging cleanup.
- `film/editor.py`: probe-based source type, metadata/thumbnail preparation, selected-stream proxy generation.
- `web/app.js`: progressive bin preparation, stable idle DOM, source feedback, current-revision preview recovery.
- `scripts/managed_shotcut.py` and `patches/`: reproducible adapter patch and bundle verification.
- `tests/native_import_contract.swift`: controlled generic movie-over-poster requests, temporary-file copying, synchronous promise negotiation, out-of-order multi-file promises, shared destinations, early or late import-kind replies, download failure without poster fallback, cancellation, per-receiver diagnostics, and report bounds. This is not a real Photos UI test.
- `tests/native_playback_acceptance.py`: temporary mixed bin, native source/sequence playback, stable polling, source preservation, moving-frame samples, and late-still output checks. `--source /absolute/path/to/video` opts into testing a local user-supplied source without publishing it.
- `tests/native_actual_media_acceptance.py`: native playback, editing, preview, and high-quality H.264 export checks for at least two distinct movie asset IDs already imported into a disposable project with an empty timeline. Close that workspace in FilmEditor before running it. The harness does not import files or establish Photos provenance; it edits the disposable timeline, checks changing source/preview/export pictures, trim/undo/redo and color controls, decoded frame counts, MLT stream selection, source audio, unchanged imported-source hashes, and backend shutdown. During export it checks that hidden renderer staging files are not published, then plays the exact completed export. Pass `--workspace`, `--project`, repeated `--asset` IDs, and `--report`; `--app` selects the installed app bundle. The caller owns test-workspace cleanup.

Keep a separate route ledger linking the observed native import route and diagnostic session/import events to the resulting asset IDs used by the actual-media harness. Record picker and direct-drag results separately, including failed or uncompleted attempts. A playback/edit/render report cannot establish how those movies entered the project, and successful drag delivery does not establish picker delivery or rendered playback.

See [VALIDATION.md](VALIDATION.md) for the exact completed acceptance evidence and remaining limits. Generated media and private machine reports are not committed.

## Apple references

- [Selecting photos and videos](https://developer.apple.com/documentation/PhotosUI/PHPickerViewController): system picker and selected-item delivery.
- [Current representation](https://developer.apple.com/documentation/photosui/phpickerconfiguration-swift.struct/assetrepresentationmode/current): representation preference, not an original-byte guarantee.
- [Photos picker format guidance](https://developer.apple.com/videos/play/wwdc2023/10107/): combine `.current` with a generic image or movie type to avoid requesting a specific encoding; Cinematic movies can be rendered versions.
- [Dragging destinations](https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/DragandDrop/Concepts/dragdestination.html): acceptance must remain valid through drag updates and preparation before a drop is performed.
- [Load a temporary file representation](https://developer.apple.com/documentation/foundation/nsitemprovider/loadfilerepresentation(fortypeidentifier:completionhandler:)): copy within the completion handler.
- [Receive promised files](https://developer.apple.com/documentation/appkit/nsfilepromisereceiver/receivepromisedfiles(atdestination:options:operationqueue:reader:)): shared destination for one drag and one callback per delivered file.
- [Promised filenames](https://developer.apple.com/documentation/appkit/nsfilepromisereceiver/filenames): names become available after requesting delivery.
- [Export original files from Photos](https://support.apple.com/guide/photos/pht6e157c5f/mac).
