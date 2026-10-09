# Third-party components

FilmEditor is an independent integration, not affiliated with Shotcut, HeyGen, GreenSock, Python Software Foundation, or OpenAI. Its MIT license covers original FilmEditor code only.

The repository includes focused patches against the MIT-licensed Shotcut MCP adapter, with its copyright and license retained in [patches/SHOTCUT_MCP_LICENSE.txt](patches/SHOTCUT_MCP_LICENSE.txt). Full dependency checkouts and binaries are excluded. The native app build copies a relocatable CPython runtime from the selected standalone prefix and pinned adapter/rendering dependencies from ignored `.deps/` directories into the local `.app` bundle. Shotcut/MLT, FFmpeg, Node.js, Chrome, and Codex remain installed separately. Third-party components retain their own licenses.

| Component | Selected reference | License/source |
| --- | --- | --- |
| CPython runtime | Relocatable standalone runtime selected by the local app build | Python Software Foundation License Version 2; https://docs.python.org/3/license.html |
| Shotcut MCP | `f1b78d350a9992296f0b7f11a55c04819ea25730` | MIT; https://github.com/matrodrigs/shotcut-mcp |
| HyperFrames CLI | public package `hyperframes@0.8.100` | Apache-2.0; https://github.com/heygen-com/hyperframes |
| Shotcut | User-installed current release | GPL-3.0; https://github.com/mltframework/shotcut |
| MLT / FFmpeg | User-installed with Shotcut | Build-specific licenses; https://www.mltframework.org/ and https://ffmpeg.org/legal.html |
| GSAP | `gsap@3.12.5`, optional title template | GreenSock's version-specific standard license; not relicensed as MIT. https://github.com/greensock/GSAP |
| Codex CLI | User-installed | https://github.com/openai/codex and OpenAI service terms |

The app bundle retains the selected CPython runtime's license file. Direct HyperFrames and GSAP versions are pinned. The first npm install still resolves transitive dependency ranges; the generated local package-lock.json is reused by npm ci. This is not a completely reproducible, security-audited dependency distribution. Inspect upgrades before changing pins.

Internal monorepo metadata names the CLI `@hyperframes/cli`; the public installation documented by its README is `hyperframes`. The installer uses the public name.

Rendered motion assets do not make original HTML/JavaScript editable as native Shotcut controls. Both editable source formats are retained. Review all licenses before redistributing binaries containing the engines or GSAP.
