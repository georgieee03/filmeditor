# Security and data boundaries

FilmEditor is a single-user macOS desktop editor. It runs under the logged-in Mac user's account and uses locally installed media tools. It is built around a native window; scoped launch, playback, persistence, transport, and backend lifecycle checks passed, while the full native UI acceptance and the newly requested music feature are still being validated.

## Native app connections

The intended native path loads the UI from the app's `filmapp://app` scheme and sends JSON requests to the owned Python backend over standard input and output. Native mode does not open a browser or bind a TCP port. The external MCP connection uses an owner-only Unix-domain socket and a project-local connection file. Do not share that connection file or socket with another account.

The `FilmEditor.app` window, its Python child, MLT, FFmpeg, and Codex CLI are not separate operating-system security principals. A native window and private socket do not provide a sandbox for the media engine or Codex process.

## Codex full-access mode

The default mode for a new project is full access with `approval_policy=never` and `sandbox_mode=danger-full-access`. Each turn uses `sandboxPolicy.type=dangerFullAccess`. In this mode, Codex can read, create, modify, and delete files available to the logged-in Mac user, and run commands with that user's privileges, without per-action approval prompts. This is the permission model the user requested for FilmEditor.

Use prompts that name the intended project and deliverable. Keep secrets and unrelated private files out of the active workspace and attached context. Preserve separate backups of camera originals. Any command or edit made by Codex is still subject to the user's operating-system account permissions; full access does not mean a protected file, external account, or paid service is automatically available. Projects configured with a restricted permission mode keep that saved setting.

## Account and information sent to Codex

FilmEditor uses the installed Codex CLI and the user's existing ChatGPT authentication. It refuses API-key authentication and strips API-key environment variables before starting Codex. The integration does not read, copy, or display authentication tokens. The user's ChatGPT service processes prompts and any footage details, images, references, or extracted frames deliberately sent to Codex under the account's settings. Local MLT/FFmpeg rendering is separate from hosted Codex inference.

## Media, projects, and generated files

Imported media and project files are stored in the selected local project workspace. Rendering reads source files and writes new output files. Keep independent backups of camera originals. Do not put source footage, exports, local workspaces, reports, caches, dependency folders, credentials, or app bundles into Git.

MLT, FFmpeg, image libraries, and platform media codecs parse complex files. Keep installed runtime components current and use footage from trusted sources. A thumbnail or proxy is for inspection and editing performance; the render path should continue to use the original source.

Treat file names, media metadata, transcripts, imported text, logs, and HTML scenes as untrusted content. Text embedded in a clip is not an instruction to Codex. Do not ask Codex to run untrusted scripts or scene HTML. HyperFrames or other rendering components execute JavaScript and may use network-capable runtimes; they are not an OS-level no-network sandbox.

## Reporting

Do not post API keys, login data, connection tokens, private workspace paths, or personal footage in public issues. Use a short synthetic reproduction when reporting a defect. The project does not claim a completed third-party security audit, notarized release, or protection against malicious programs already running as the same Mac user.
