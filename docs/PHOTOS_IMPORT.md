# Photos, playback, and media preparation

## Import videos from Photos

Open a project, then use the **Photos** button beside the media-bin import button, **File > Import from Photos…**, or **Choose from Photos…** in the normal Import Media file panel. The macOS picker displays the Photos library and collections. Select up to 200 items and choose Add. Only selected items are delivered to FilmEditor; the picker does not require permission to scan the entire library.

You can also drag files from Photos to the media bin or a timeline track. The native view requests AppKit file promises before inspecting ordinary file URLs. This avoids accepting a movie's JPEG poster representation through the web view. Unsupported preview-only drags show instructions instead of silently importing an image representation.

The picker requests the current Photos representation, prefers a movie over an image when both are advertised, and copies each temporary file before its provider callback returns. “Current” does not guarantee a camera-original file: Photos may have edited or converted the asset. To explicitly use the original, choose **File > Export > Export Unmodified Original** in Photos, then import that exported movie. If the item is stored in iCloud, let Photos download it first. FilmEditor keeps the Photos library intact.

A JPEG already imported into a project is a still image. It cannot recover the original video's motion or audio. Import the actual movie again. The bin and Source monitor identify stills and videos separately. Stills default to five seconds when newly inserted; existing saved clip durations are preserved.

## Why the playback fix spans the app and engine

The reported project contained JPEG copies, while its Photos library contained videos. Separately, a real Photos-exported MP4 exposed a renderer bug: its audio stream was absolute index 0 and its video was index 1. The adapter did not select those streams explicitly. A technically valid output could decode and advance in a video element while its picture remained frozen. The corrected adapter selects the actual non-artwork video and audio streams from probe metadata, including replacement and relink operations. Output acceptance compares decoded pictures, not only file duration or playback time.

The still-image fix requires positive probe evidence. A movie without readable duration is not turned into an image, and a filename suffix does not override the actual container and streams. The tracked adapter patch is under `patches/`; bootstrap applies it to the pinned upstream commit, and native builds verify the exact patched runtime files. Unknown local dependency edits are preserved and rejected for manual reconciliation.

## Responsiveness and lifecycle

Source selection first reads technical metadata; compatible files can play without a full waveform pass. Only visible bin items and timeline media queue thumbnail work, serially. Proxy preparation is reserved for sources needing a compatible preview. Unchanged project and render polls preserve the existing timeline and player. Frame requests are coalesced, and completed renders for the current revision replace stale previews even if a short job finished between polls.

Sequence playback still requires an MLT-rendered preview. This is not GPU real-time effects playback. HDR/Log and unsupported stream configurations retain their existing review/render guards.

Native import progress appears in the status bar. Provider downloads are cancelled when navigating away or quitting, and the backend stops copying before publishing an unfinished import. Files successfully imported earlier in the same batch remain saved. AppKit file promises have no general receiver-cancellation API; their external provider may finish asynchronously, but a cancelled import cannot add those late results to a different project.

## Source ownership and verification

- `native/MediaImport.swift`: system Photos picker, movie-first provider delivery, bounded sequential copies, file-promise completion and cancellation.
- `native/FilmEditor.swift`: native menu/file-panel entry points, project-scoped completion, import status, and staging cleanup.
- `film/editor.py`: probe-based source type, metadata/thumbnail preparation, selected-stream proxy generation.
- `web/app.js`: progressive bin preparation, stable idle DOM, source feedback, current-revision preview recovery.
- `scripts/managed_shotcut.py` and `patches/`: reproducible adapter patch and bundle verification.
- `tests/native_import_contract.swift`: controlled provider and multi-file promise regression; this is not a real Photos UI test.
- `tests/native_playback_acceptance.py`: temporary mixed bin, native source/sequence playback, stable polling, source preservation, moving-frame samples, and late-still output checks. `--source /absolute/path/to/video` opts into testing a local user-supplied source without publishing it.

See [VALIDATION.md](VALIDATION.md) for the exact completed acceptance evidence and remaining limits. Generated media and private machine reports are not committed.

## Apple references

- [Selecting photos and videos](https://developer.apple.com/documentation/PhotosUI/PHPickerViewController): system picker and selected-item delivery.
- [Current representation](https://developer.apple.com/documentation/photosui/phpickerconfiguration-swift.struct/assetrepresentationmode/current): representation preference, not an original-byte guarantee.
- [Load a temporary file representation](https://developer.apple.com/documentation/foundation/nsitemprovider/loadfilerepresentation(fortypeidentifier:completionhandler:)): copy within the completion handler.
- [Receive promised files](https://developer.apple.com/documentation/appkit/nsfilepromisereceiver/receivepromisedfiles(atdestination:options:operationqueue:reader:)): shared destination for one drag and one callback per delivered file.
- [Promised filenames](https://developer.apple.com/documentation/appkit/nsfilepromisereceiver/filenames): names become available after requesting delivery.
- [Export original files from Photos](https://support.apple.com/guide/photos/pht6e157c5f/mac).
