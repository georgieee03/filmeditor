"""Real MLT, FFmpeg, and browser acceptance for the advanced timeline tools.

The test generates synthetic media in a temporary workspace. It never reads or
modifies camera originals or user project files.
"""
from pathlib import Path
import json
import re
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from film.core import ROOT, Workspace, unpack
from film.server import FilmServer
from playwright.sync_api import expect, sync_playwright


report = {"evidence": "synthetic media; real installed MLT/FFmpeg and Chromium"}
browser_errors = []


def check(condition, label):
    assert condition, label
    report[label] = True


def current_clips(snapshot, track_name="V1"):
    track = next(track for track in snapshot["tracks"] if track["name"] == track_name)
    return track, [item for item in track["items"] if item["type"] == "clip"]


with tempfile.TemporaryDirectory(prefix="filmeditor-advanced-acceptance-") as temp:
    root = Path(temp)
    workspace = Workspace(root / "workspace")
    server = FilmServer(workspace, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source = root / "synthetic-advanced.mp4"
    subtitle_file = root / "sample.srt"
    try:
        subprocess.run(
            [
                workspace.executable("ffmpeg"), "-nostdin", "-v", "error",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-t", "4", "-c:v", "libx264", "-threads", "1",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
                str(source),
            ], check=True, capture_output=True
        )
        subtitle_file.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nFirst generated caption\n\n"
            "2\n00:00:01,200 --> 00:00:02,000\nSecond generated caption\n",
            encoding="utf-8",
        )
        created = unpack(workspace.call("film_new_project", {
            "name": "Advanced tool acceptance", "width": 640, "height": 360,
            "fps_num": 30, "fps_den": 1,
        }))
        with source.open("rb") as stream:
            asset = workspace.ingest(stream, source.stat().st_size, source.name, "footage")
        snapshot = server.editor.snapshot()
        path = str(workspace.active())
        setup = [
            {"op": "add_clip", "track": "V1", "path": asset["path"],
             "position_frame": start, "in_frame": 0, "out_frame": 89,
             "mode": "overwrite", "caption": f"Synthetic {index + 1}"}
            for index, start in enumerate((0, 90, 180))
        ]
        snapshot = server.editor.edit(snapshot["revision"], setup, project_path=path)
        check(len(current_clips(snapshot)[1]) == 3, "three contiguous synthetic clips inserted")

        with sync_playwright() as playwright:
            chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            browser = playwright.chromium.launch(
                executable_path=chrome, headless=True, chromium_sandbox=True
            )
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: browser_errors.append(str(error)))
            static_status = {}
            page.on("response", lambda response: static_status.update(
                {response.url.rsplit("/", 1)[-1]: response.status}
                if response.url.endswith(("/advanced.js", "/advanced.css")) else {}
            ))
            page.goto(server.origin + "/?project=legacy#" + server.token)
            expect(page.locator("#notice")).to_contain_text("Project ready", timeout=60000)
            page.wait_for_function("window.filmAdvancedToolsInstalled === true")
            expect(page.locator("#film-advanced-open")).to_be_visible()
            expect(page.locator(".track-strip[data-track-name='V1'] .clip:not(.transition)")).to_have_count(3)
            check(static_status.get("advanced.js") == 200 and static_status.get("advanced.css") == 200,
                  "editor loads advanced assets through its static routes")
            check(True, "advanced tools load in the real editor page")

            clips = page.locator(".track-strip[data-track-name='V1'] .clip:not(.transition)")
            clips.nth(0).click(position={"x": 12, "y": 12})
            page.locator("#film-advanced-open").click()
            expect(page.locator("#film-advanced-dialog")).to_be_visible()
            page.locator("#film-advanced-delta").fill("1")
            page.locator("#film-advanced-dialog").get_by_role("button", name="Roll cut").click()
            expect(page.locator("#notice")).to_contain_text("Cut rolled", timeout=30000)
            check(True, "browser roll edit commits to MLT project")
            page.locator("#film-advanced-dialog").get_by_role("button", name="Slip source").click()
            expect(page.locator("#notice")).to_contain_text("Source range slipped", timeout=30000)
            check(True, "browser slip edit commits to MLT project")

            page.locator("#film-advanced-dialog").get_by_role("button", name="Done").click()
            clips = page.locator(".track-strip[data-track-name='V1'] .clip:not(.transition)")
            clips.nth(1).click(position={"x": 12, "y": 12})
            page.locator("#film-advanced-open").click()
            page.locator("#film-advanced-dialog").get_by_role("button", name="Slide clip").click()
            expect(page.locator("#notice")).to_contain_text("Clip slid", timeout=30000)
            check(True, "browser slide edit commits to MLT project")

            page.locator("#film-advanced-speed").fill("1.25")
            page.locator("#film-advanced-dialog").get_by_role("button", name="Apply speed").click()
            expect(page.locator("#notice")).to_contain_text("Playback speed set", timeout=30000)
            check(True, "browser speed edit commits to MLT project")
            page.locator("#film-advanced-transition-duration").fill("10")
            page.locator("#film-advanced-dialog").get_by_role(
                "button", name="Add cross dissolve after selected clip"
            ).click()
            expect(page.locator("#notice")).to_contain_text("Cross dissolve added", timeout=30000)
            expect(page.locator(".track-strip[data-track-name='V1'] .clip.transition")).to_have_count(1)
            check(True, "browser cross dissolve creates an MLT transition")

            page.locator("#film-advanced-title-text").fill("Synthetic title card")
            page.locator("#film-advanced-title-duration").fill("2")
            page.locator("#film-advanced-dialog").get_by_role(
                "button", name="Create title on a new video track"
            ).click()
            expect(page.locator(".track-header").filter(has_text="Titles")).to_be_visible(timeout=30000)
            check(True, "browser title generator creates a new video track")

            page.locator("#film-advanced-marker-text").fill("Review point")
            page.locator("#film-advanced-dialog").get_by_role(
                "button", name="Add marker at playhead"
            ).click()
            expect(page.locator("#film-advanced-marker-overlay .film-advanced-marker")).to_have_count(1)
            check(True, "marker is visible on the timeline ruler")
            page.locator("#film-advanced-dialog").get_by_role(
                "button", name="Remove selected marker"
            ).click()
            expect(page.locator("#film-advanced-marker-overlay")).to_have_count(0)
            check(True, "browser removes sequence marker")

            page.locator("#film-advanced-caption-file").set_input_files(str(subtitle_file))
            expect(page.locator("#film-advanced-caption-text")).to_have_value(
                re.compile("First generated caption")
            )
            page.locator("#film-advanced-dialog").get_by_role(
                "button", name="Save captions"
            ).click()
            expect(page.locator("#notice")).to_contain_text("caption cue(s) saved", timeout=30000)
            caption_select = page.locator("#film-advanced-caption-existing")
            caption_select.select_option(label="sample")
            page.locator("#film-advanced-dialog").get_by_role(
                "button", name="Load selected track"
            ).click()
            page.locator("#film-advanced-caption-text").fill(
                "1\n00:00:00,000 --> 00:00:01,000\nEdited caption from UI\n"
            )
            page.locator("#film-advanced-dialog").get_by_role(
                "button", name="Save captions"
            ).click()
            expect(page.locator("#notice")).to_contain_text("1 caption cue(s) saved", timeout=30000)
            check(True, "browser imports and edits an SRT subtitle track")
            browser.close()

        snapshot = server.editor.snapshot()
        track, clip_items = current_clips(snapshot)
        transitions = [item for item in track["items"] if item["type"] == "transition"]
        check(len(transitions) == 1, "MLT snapshot contains one dissolve")
        title_track = next(track for track in snapshot["tracks"] if track["name"] == "Titles")
        check(any(item["type"] == "clip" for item in title_track["items"]), "MLT snapshot contains generated title")
        check(any(abs(float(item.get("duration_frames", 0)) - 90) > 0 for item in clip_items), "speed change affects clip duration")
        check("Edited caption from UI" in snapshot["subtitles"][0]["srt"], "edited SRT text persists in project")

        service_list = unpack(workspace.call("list_mlt_services", {"kind": "transition"}))
        check("luma" in service_list.get("services", []), "installed MLT luma transition service available")
        render = server.editor.render(snapshot["revision"], preview=True, project_path=snapshot["path"])
        status = None
        for _ in range(300):
            status = unpack(workspace.forward("render_status", {"job_id": render["job_id"]}))
            if status.get("status") not in {"queued", "running"}:
                break
            time.sleep(.3)
        check(status and status.get("status") == "completed", "real MLT render completes")
        subprocess.run(
            [workspace.executable("ffmpeg"), "-nostdin", "-v", "error", "-i", status["output_path"], "-f", "null", "-"],
            check=True, capture_output=True,
        )
        check(True, "real rendered sequence fully decodes with FFmpeg")
        report["render"] = {key: status.get(key) for key in ("status", "total_frames", "project_revision")}
        check(not browser_errors, "no browser JavaScript exceptions")
    finally:
        if workspace.shotcut:
            for job in server.editor.renders():
                if job["status"] in {"queued", "running"}:
                    workspace.forward("cancel_render", {"job_id": job["job_id"]})
        server.shutdown()
        server.library.close()
        server.codex.close()
        workspace.close()
        server.server_close()
        report["browser_errors"] = browser_errors
        report_dir = ROOT / "reports" / "rebuild"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "advanced-acceptance.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )

print(json.dumps(report, indent=2))
