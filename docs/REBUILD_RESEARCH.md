# FilmEditor rebuild research

Research reviewed on 2026-10-08. This note records the source-selection decision for the native macOS editor rebuild. Upstream project status can change; recheck the linked repositories before changing the engine or packaging plan.

## Decision

Build FilmEditor's own desktop editing workflow around the MLT and FFmpeg tools installed with Shotcut. Ship a native AppKit application shell and keep FilmEditor's timeline, controls, project library, and Codex integration in this repository. The pinned Shotcut MCP adapter provides the bridge from FilmEditor to Shotcut's installed MLT engine. Do not transplant Shotcut's full application or claim to use Adobe Premiere's internal engine.

The native source includes a Swift AppKit shell and WKWebView interface. It serves the interface as `filmapp://app`, exchanges requests with the Python backend over stdio JSON IPC, and exposes an owner-only Unix-domain socket to an external MCP client. The installed build passed launch/reopen, source and rendered-timeline playback/seek, project persistence, no-TCP, external stdio MCP, and owned-backend shutdown. Manual interaction passed NSOpenPanel import, bin-to-track drag, cross-track move, end trim, native Save-panel export, and full decode of the saved export. A separate manual Codex check inspected the saved project read-only and ran a benign shell command without approvals. The corrected native AI-button acceptance separately authored a 30-frame clip from a synthetic source, accepted MLT preview and render-status MCP calls without failures or shell recovery, fully decoded the H.264 export, recorded no approvals, left the source unchanged, and retained a valid app signature after the live turn. The installed app also opened the six-step quickstart automatically, fitted a local song onto a Music track and restored it after reopen, and opened the advanced-tools dialog. Final cleanup removed owned test media and opened a fresh blank project with the quickstart visible. Finder-originated file drop, actual advanced edits and layout-preset changes in WKWebView, and the full native catalog/credit workflow remain unverified. This records the architecture decision and scoped tested behavior, not a release certification.

## Upstream projects reviewed

| Project | What its upstream repository establishes | Relevance to FilmEditor |
| --- | --- | --- |
| [MLT Multimedia Framework](https://github.com/mltframework/mlt) | MLT describes itself as an LGPL multimedia framework designed for video editing. | Selected as the underlying media and timeline engine through Shotcut's installed tools and the pinned adapter. FilmEditor does not directly link or bundle MLT in the native app. |
| [Shotcut](https://github.com/mltframework/shotcut) | Shotcut describes itself as a cross-platform Qt editor under GPLv3 and lists MLT, Qt, and FFmpeg among its direct dependencies. | Its installed MLT/FFmpeg runtime supplies engine tools. Shotcut's Qt editor is not merged into FilmEditor; the two applications retain separate licenses. |
| [OpenCut](https://github.com/OpenCut-app/OpenCut) | The current `main` README says the app is being rewritten and directs users to [OpenCut Classic](https://github.com/OpenCut-app/opencut-classic). The classic repository says it was archived on 2026-05-17 and is no longer maintained. | Useful for product research. The rewrite is moving and its classic predecessor is archived, so neither is selected as the engine or application base for this rebuild. |
| [Olive](https://github.com/olive-editor/olive) | The repository presents Olive as a nonlinear editor for macOS, Windows, and Linux, and labels its current software alpha and highly unstable. | Useful as a workflow reference, but its upstream warning makes it a poor foundation for this user's current Mac editing workflow. |

The upstream descriptions above are repository statements, not independent performance or security audits. This comparison does not establish codec compatibility, 4K performance, color accuracy, project interchange, or production readiness.

## Why this architecture fits the request

The user asked for a native Mac app instead of a localhost web service and for an editor rebuild with direct timeline editing. A native app shell provides the Finder-facing lifecycle and macOS file dialogs while the custom timeline UI talks to a local media engine. Keeping the engine separate from the UI lets FilmEditor improve its editing workflow without copying a full competing editor or relying on unfinished upstream rewrites.

The desktop app must not open a browser or bind a TCP port in native mode. `filmapp://app` and the app-owned stdio channel provide the in-process UI/backend path; the external Codex MCP route uses the project's private Unix socket. These transport boundaries must remain covered by native acceptance checks.

MLT and FFmpeg are media engines; their availability does not establish that all Premiere features exist in FilmEditor. The current implementation covers core editing operations such as import, timeline placement, move, trim, split, duplicate, delete, undo/redo, rendered previews, effects controls, and rendering. Sequence previews are offline renders, not real-time playback. A feature should be described as implemented only when a real editor path and its corresponding acceptance evidence exist.

## License and distribution notes

FilmEditor's MIT license covers original FilmEditor code. It does not relicense MLT, FFmpeg, Shotcut, HyperFrames, GSAP, or other third-party components. The MLT project identifies its source license as LGPL-2.1; Shotcut identifies itself as GPLv3. FFmpeg is LGPL 2.1-or-later by default, with optional GPL components that can make the resulting build GPL. Preserve the exact third-party notices and inspect the licenses for every library and executable included in a redistributable app bundle. The existing notices are in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

No upstream code from Shotcut, OpenCut, Olive, or Adobe is claimed as FilmEditor code. A binary that bundles those projects or their libraries needs a component-by-component distribution review before release.

## Primary sources

- [MLT repository and README](https://github.com/mltframework/mlt)
- [Shotcut repository and README](https://github.com/mltframework/shotcut)
- [OpenCut current repository and rewrite status](https://github.com/OpenCut-app/OpenCut)
- [OpenCut Classic archived repository](https://github.com/OpenCut-app/opencut-classic)
- [Olive repository and current stability notice](https://github.com/olive-editor/olive)
- [FFmpeg legal and license information](https://ffmpeg.org/legal.html)
- [Codex subagent guidance](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Codex `AGENTS.md` guidance](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
