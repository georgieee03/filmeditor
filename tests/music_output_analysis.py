#!/usr/bin/env python3
"""Independent, bounded audio checks for a rendered soundtrack export.

This tool compares audio decoded from the supplied song and export. It does not
read FilmEditor's beat grid, timeline analysis, or app-produced onset markers.
Its correlations and cut proximity scores are diagnostic evidence, not a
musical-quality or creative-acceptance decision.
"""
from __future__ import annotations

import argparse
from array import array
import bisect
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
from typing import Any


RATE = 16_000
WINDOW = 400                 # 25 ms
HOP = 128                    # 8 ms
ONSET_TIMING_ALLOWANCE_S = WINDOW / RATE / 2.0 + HOP / RATE
MAX_SONG_SECONDS = 600.0
MAX_EXPORT_SECONDS = 60.0
SILENCE_DBFS = -60.0
SILENCE_WINDOW_S = 0.25
CORRELATION_WINDOW_S = 20.0
MIN_CORRELATION_WINDOW_S = 5.0
COARSE_GROUP = 6             # 48 ms for bounded offset search


def _tool_path(env_name: str, name: str) -> str:
    configured = os.environ.get(env_name)
    found = configured if configured and Path(configured).is_file() else shutil.which(name)
    if not found:
        raise RuntimeError(f"Could not find {name}; set {env_name} or add it to PATH")
    return found


def _run(command: list[str], *, timeout: float, what: str) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{what} timed out after {timeout:g} seconds") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        message = detail[-1][:300] if detail else "no diagnostic"
        raise RuntimeError(f"{what} failed (exit {result.returncode}): {message}")
    return result


def _probe(path: Path, ffprobe: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"Input file does not exist: {path}")
    result = _run([
        ffprobe, "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,duration,avg_frame_rate,r_frame_rate,channels,sample_rate",
        "-of", "json", str(path),
    ], timeout=30, what=f"ffprobe {path.name}")
    try:
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration"))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read duration metadata for {path}") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Invalid duration metadata for {path}")
    streams = data.get("streams", [])
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    fps = None
    fps_basis = None
    if video:
        for key in ("avg_frame_rate", "r_frame_rate"):
            value = video.get(key, "")
            try:
                numerator, denominator = (int(part) for part in value.split("/", 1))
                candidate = numerator / denominator
            except (ValueError, ZeroDivisionError, AttributeError):
                continue
            if math.isfinite(candidate) and 0 < candidate <= 240:
                fps, fps_basis = candidate, key
                break
    return {
        "duration_seconds": duration,
        "audio_stream": audio,
        "video_stream": video,
        "frame_rate": fps,
        "frame_rate_basis": fps_basis,
    }


def _decode_mono(path: Path, ffmpeg: str, limit_s: float) -> array:
    result = _run([
        ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-threads", "1",
        "-filter_threads", "1", "-filter_complex_threads", "1", "-i", str(path),
        "-map", "0:a:0", "-vn", "-sn", "-dn", "-t", f"{limit_s:.6f}",
        "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1",
    ], timeout=max(60.0, limit_s * 3.0), what=f"decode audio from {path.name}")
    if len(result.stdout) % 2:
        raise RuntimeError(f"FFmpeg returned an incomplete PCM sample for {path.name}")
    if len(result.stdout) > int((limit_s + 0.05) * RATE * 2):
        raise RuntimeError(f"FFmpeg exceeded the {limit_s:g}-second PCM bound for {path.name}")
    samples = array("h")
    samples.frombytes(result.stdout)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        raise ValueError(f"No decodable audio samples in {path}")
    return samples


def _spectral_flux(path: Path, ffmpeg: str, limit_s: float) -> tuple[array, dict[str, Any]]:
    """Use FFmpeg's spectral-flux filter on 25 ms windows at 8 ms frame steps."""
    filter_chain = (
        f"aresample={RATE},aformat=sample_fmts=fltp:channel_layouts=mono,"
        "asetpts=PTS-STARTPTS,"
        f"asetnsamples=n={HOP}:p=0,"
        f"aspectralstats=win_size={WINDOW}:overlap=0.68:measure=flux,"
        "ametadata=mode=print:file=/dev/stdout:direct=1"
    )
    result = _run([
        ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-threads", "1",
        "-filter_threads", "1", "-filter_complex_threads", "1", "-i", str(path),
        "-map", "0:a:0", "-vn", "-sn", "-dn", "-t", f"{limit_s:.6f}",
        "-ac", "1", "-ar", str(RATE), "-af", filter_chain,
        "-f", "null", os.devnull,
    ], timeout=max(60.0, limit_s * 3.0), what=f"spectral-flux analysis of {path.name}")
    flux = array("f")
    frame_times: list[float] = []
    current_frame_time: float | None = None
    awaiting_flux = False
    for line in result.stdout.decode("ascii", "replace").splitlines():
        if line.startswith("frame:"):
            if awaiting_flux:
                raise RuntimeError(f"Missing spectral-flux value for a frame in {path.name}")
            try:
                timestamp_field = "pts_time=" if "pts_time=" in line else "pts_time:"
                current_frame_time = float(line.split(timestamp_field, 1)[1].split()[0])
            except (IndexError, ValueError) as exc:
                raise RuntimeError(f"Missing frame timestamp in spectral-flux output for {path.name}") from exc
            if not math.isfinite(current_frame_time):
                raise RuntimeError(f"Nonfinite frame timestamp in spectral-flux output for {path.name}")
            awaiting_flux = True
        if line.startswith("lavfi.aspectralstats.1.flux="):
            if not awaiting_flux or current_frame_time is None:
                raise RuntimeError(f"Flux value without a timestamp in spectral-flux output for {path.name}")
            try:
                value = float(line.rsplit("=", 1)[1])
            except ValueError as exc:
                raise RuntimeError(f"Invalid spectral-flux value in output for {path.name}") from exc
            if not math.isfinite(value):
                raise RuntimeError(f"Nonfinite spectral-flux value in output for {path.name}")
            flux.append(value)
            frame_times.append(current_frame_time)
            awaiting_flux = False
    expected_frames = math.ceil(limit_s * RATE / HOP) + 1
    if not flux:
        raise ValueError(f"FFmpeg produced no spectral-flux frames for {path}")
    if awaiting_flux:
        raise RuntimeError(f"Missing final spectral-flux value in output for {path.name}")
    if len(flux) > expected_frames:
        raise RuntimeError(f"FFmpeg exceeded the {limit_s:g}-second spectral-flux bound for {path.name}")
    expected_step = HOP / RATE
    if abs(frame_times[0]) > 1.0 / RATE or any(
            abs((right - left) - expected_step) > 1.0 / RATE
            for left, right in zip(frame_times, frame_times[1:])):
        raise RuntimeError(f"Unexpected spectral-frame timestamps for {path.name}; expected {expected_step:g}s steps from zero")
    steps = [right - left for left, right in zip(frame_times, frame_times[1:])]
    return flux, {
        "status": "verified_regular_frame_grid",
        "sample_rate_hz": RATE,
        "conversion": "aresample to 16 kHz, mono float, reset PTS to decoded stream start, 128 samples per frame",
        "first_pts_seconds": frame_times[0],
        "last_pts_seconds": frame_times[-1],
        "frame_step_seconds_min": min(steps) if steps else None,
        "frame_step_seconds_max": max(steps) if steps else None,
        "frame_count": len(frame_times),
    }


def _detect_onsets(flux: array) -> tuple[list[float], dict[str, Any]]:
    """Find local spectral-flux peaks using a robust, local data threshold."""
    block_frames = max(1, round(2.0 * RATE / HOP))
    candidates: list[tuple[int, float]] = []
    thresholds: list[float] = []
    for start in range(0, len(flux), block_frames):
        stop = min(len(flux), start + block_frames)
        block = list(flux[start:stop])
        median = statistics.median(block)
        mad = statistics.median(abs(value - median) for value in block)
        threshold = median + max(2.5 * 1.4826 * mad, 0.005)
        thresholds.append(threshold)
        for index in range(max(start, 1), min(stop, len(flux) - 1)):
            value = flux[index]
            if value >= threshold and value >= flux[index - 1] and value > flux[index + 1]:
                candidates.append((index, value))
    candidates.sort()
    refractory = max(1, round(0.12 * RATE / HOP))
    kept: list[tuple[int, float]] = []
    for candidate in candidates:
        if kept and candidate[0] - kept[-1][0] < refractory:
            if candidate[1] > kept[-1][1]:
                kept[-1] = candidate
        else:
            kept.append(candidate)
    return ([index * HOP / RATE for index, _ in kept], {
        "method": "positive spectral flux local peaks",
        "window_ms": WINDOW * 1000.0 / RATE,
        "hop_ms": HOP * 1000.0 / RATE,
        "timestamp_convention": "onset time is spectral-flux frame index times 8 ms from decoded stream start; FFmpeg PTS are checked at that interval; flux uses a 25 ms spectral window",
        "nominal_onset_timing_allowance_ms": ONSET_TIMING_ALLOWANCE_S * 1000.0,
        "timing_allowance_basis": "half the spectral window plus one hop; not a calibrated error bound",
        "threshold": "per-2-second median + max(2.5 * 1.4826 * MAD, 0.005)",
        "refractory_ms": 120,
        "median_threshold": statistics.median(thresholds) if thresholds else None,
        "detected_count": len(kept),
        "interpretation": "observed spectral changes; not a beat grid or musical transcription",
    })


def _coarsen(values: array, group: int = COARSE_GROUP, mode: str = "mean") -> list[float]:
    output = []
    for start in range(0, len(values), group):
        chunk = values[start:start + group]
        if not chunk:
            break
        output.append(max(chunk) if mode == "max" else sum(chunk) / len(chunk))
    return output


def _pearson(a: list[float] | array, b: list[float] | array) -> float | None:
    n = min(len(a), len(b))
    if n < 10:
        return None
    mean_a = sum(a[:n]) / n
    mean_b = sum(b[:n]) / n
    var_a = sum((value - mean_a) ** 2 for value in a[:n])
    var_b = sum((value - mean_b) ** 2 for value in b[:n])
    if var_a <= 1e-15 or var_b <= 1e-15:
        return None
    covariance = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    return max(-1.0, min(1.0, covariance / math.sqrt(var_a * var_b)))


def _window_pearson(a: list[float] | array, a_start: int,
                    b: list[float] | array, b_start: int, count: int) -> float | None:
    if count < 10 or a_start < 0 or b_start < 0 or a_start + count > len(a) or b_start + count > len(b):
        return None
    sum_a = sum_b = sum_a2 = sum_b2 = sum_ab = 0.0
    for index in range(count):
        av, bv = a[a_start + index], b[b_start + index]
        sum_a += av
        sum_b += bv
        sum_a2 += av * av
        sum_b2 += bv * bv
        sum_ab += av * bv
    var_a = sum_a2 - sum_a * sum_a / count
    var_b = sum_b2 - sum_b * sum_b / count
    if var_a <= 1e-15 or var_b <= 1e-15:
        return None
    covariance = sum_ab - sum_a * sum_b / count
    return max(-1.0, min(1.0, covariance / math.sqrt(var_a * var_b)))


def _control_song_starts(song_count: int, window: int, selected_start: int,
                         exclusion: int) -> dict[str, int]:
    """Choose distinct early/mid/late source excerpts for false-positive controls."""
    last_start = song_count - window
    if last_start < exclusion * 2:
        return {}
    controls: dict[str, int] = {}
    fractions = (("early", 0.12), ("mid", 0.50), ("late", 0.88))
    for label, fraction in fractions:
        target = round(last_start * fraction)
        if abs(target - selected_start) <= exclusion or any(abs(target - prior) <= exclusion for prior in controls.values()):
            alternatives = [round(last_start * alt) for alt in (0.03, 0.22, 0.38, 0.62, 0.78, 0.97)]
            alternatives = [candidate for candidate in alternatives
                            if abs(candidate - selected_start) > exclusion and
                            all(abs(candidate - prior) > exclusion for prior in controls.values())]
            if alternatives:
                target = min(alternatives, key=lambda candidate: abs(candidate - target))
            else:
                continue
        controls[label] = target
    return controls


def _best_window_for_lag(song: list[float], export: list[float], lag: int,
                         window: int) -> dict[str, Any] | None:
    """Best local match where song_frame = export_frame + lag."""
    export_first = max(0, -lag)
    export_last = min(len(export) - window, len(song) - window - lag)
    if export_last < export_first:
        return None
    best = None
    for export_start in range(export_first, export_last + 1):
        song_start = export_start + lag
        score = _window_pearson(song, song_start, export, export_start, window)
        if score is not None and (best is None or score > best["spectral_flux_correlation"]):
            best = {"song_start_frame": song_start, "export_start_frame": export_start,
                    "lag_frames": lag, "spectral_flux_correlation": score,
                    "window_frames": window}
    return best


def _infer_offset(song_flux: list[float], export_flux: list[float], sample_step: int = 1) -> dict[str, Any]:
    """Search matching local windows, including a delayed song start in the export."""
    base_dt = HOP * COARSE_GROUP / RATE
    search_group = 4
    search_dt = base_dt * search_group
    song_search = song_flux[::search_group]
    export_search = export_flux[::search_group]
    window_base = min(round(CORRELATION_WINDOW_S / base_dt), len(song_flux), len(export_flux))
    min_base = round(MIN_CORRELATION_WINDOW_S / base_dt)
    if window_base < min_base:
        return {"status": "insufficient_overlap", "minimum_overlap_seconds": MIN_CORRELATION_WINDOW_S}
    window_search = max(1, round(window_base / search_group))
    # Keep equal-duration windows so correlation scores remain comparable.
    window_base = window_search * search_group
    min_base = min_base
    if window_base * base_dt < MIN_CORRELATION_WINDOW_S or window_base > len(song_flux) or window_base > len(export_flux):
        return {"status": "insufficient_overlap", "minimum_overlap_seconds": MIN_CORRELATION_WINDOW_S}
    max_song_start = len(song_search) - window_search
    max_export_start = len(export_search) - window_search
    if max_song_start < 0 or max_export_start < 0:
        return {"status": "insufficient_overlap", "minimum_overlap_seconds": MIN_CORRELATION_WINDOW_S}
    out_step = max(1, round(0.96 / search_dt))
    coarse_best: dict[str, Any] | None = None
    coarse_rows: list[tuple[int, float]] = []
    for export_start in range(0, max_export_start + 1, out_step):
        for song_start in range(0, max_song_start + 1):
            score = _window_pearson(song_search, song_start, export_search, export_start, window_search)
            if score is None:
                continue
            lag = song_start - export_start
            coarse_rows.append((lag, score))
            if coarse_best is None or score > coarse_best["spectral_flux_correlation"]:
                coarse_best = {"song_search_start": song_start, "export_search_start": export_start,
                               "lag_search_frames": lag, "spectral_flux_correlation": score}
    if coarse_best is None:
        return {"status": "insufficient_signal", "minimum_overlap_seconds": MIN_CORRELATION_WINDOW_S}
    # Refine both window starts on the 48 ms feature grid.
    song_center = coarse_best["song_search_start"] * search_group
    export_center = coarse_best["export_search_start"] * search_group
    refined = None
    for export_start in range(max(0, export_center - search_group * 5),
                              min(len(export_flux) - window_base, export_center + search_group * 5) + 1):
        for song_start in range(max(0, song_center - search_group),
                                min(len(song_flux) - window_base, song_center + search_group) + 1):
            score = _window_pearson(song_flux, song_start, export_flux, export_start, window_base)
            if score is None:
                continue
            if refined is None or score > refined["spectral_flux_correlation"]:
                refined = {"song_start_frame": song_start, "export_start_frame": export_start,
                           "lag_frames": song_start - export_start,
                           "spectral_flux_correlation": score, "window_frames": window_base}
    if refined is None:
        return {"status": "insufficient_signal", "minimum_overlap_seconds": MIN_CORRELATION_WINDOW_S}
    best_lag = refined["lag_frames"]
    exclusion = max(1, round(1.0 / base_dt))
    runner_up = max((score for lag, score in coarse_rows if abs(lag * search_group - best_lag) > exclusion), default=None)
    controls = {}
    for label, song_start in _control_song_starts(len(song_flux), window_base,
                                                  refined["song_start_frame"], exclusion).items():
        score = _window_pearson(song_flux, song_start, export_flux,
                                refined["export_start_frame"], window_base)
        controls[label] = {"song_excerpt_start_seconds": song_start * base_dt,
                           "lag_seconds": (song_start - refined["export_start_frame"]) * base_dt,
                           "spectral_flux_correlation": score}
    return {
        "status": "estimated",
        "lag_seconds": best_lag * base_dt,
        "song_excerpt_start_seconds": refined["song_start_frame"] * base_dt,
        "export_excerpt_start_seconds": refined["export_start_frame"] * base_dt,
        "lag_frames": best_lag,
        "spectral_flux_correlation": refined["spectral_flux_correlation"],
        "runner_up_correlation_outside_1s": runner_up,
        "runner_up_margin": refined["spectral_flux_correlation"] - runner_up if runner_up is not None else None,
        "overlap_seconds": window_base * base_dt,
        "window_frames": window_base,
        "coarse_search_step_seconds": out_step * search_dt,
        "refinement_step_seconds": base_dt,
        "mismatched_reference_controls": controls,
    }


def _downsample_pcm(samples: array, factor: int = 8) -> array:
    """Box-average PCM to 2 kHz for bounded, codec-tolerant waveform matching."""
    output = array("f")
    stop = len(samples) - (len(samples) % factor)
    scale = 1.0 / (factor * 32768.0)
    for start in range(0, stop, factor):
        total = 0
        for index in range(start, start + factor):
            total += samples[index]
        output.append(total * scale)
    return output


def _best_waveform_match(song: array, export: array, song_start: int,
                         export_start: int, window: int,
                         lag_radius: int = 240) -> dict[str, Any] | None:
    best = None
    first = max(-lag_radius, -song_start)
    last = min(lag_radius, len(song) - window - song_start)
    if export_start < 0 or export_start + window > len(export) or last < first:
        return None
    for delta in range(first, last + 1):
        score = _window_pearson(song, song_start + delta, export, export_start, window)
        if score is not None and (best is None or score > best["waveform_pearson"]):
            best = {"waveform_pearson": score, "lag_delta_samples_at_2khz": delta,
                    "lag_delta_seconds": delta / 2000.0,
                    "song_window_start_seconds": (song_start + delta) / 2000.0,
                    "export_window_start_seconds": export_start / 2000.0,
                    "window_seconds": window / 2000.0}
    return best


def _waveform_checks(song_pcm: array, export_pcm: array, lag_seconds: float,
                     export_span: float, frame_rate: float | None) -> dict[str, Any]:
    """Check source-song waveform similarity in early/middle/late export interiors."""
    song = _downsample_pcm(song_pcm)
    export = _downsample_pcm(export_pcm)
    rate = RATE / 8
    window = round(2.0 * rate)
    radius = round(0.120 * rate)
    safe_span = min(export_span, len(export) / rate)
    if safe_span < 6.0 or len(song) / rate < 2.0:
        return {"status": "insufficient_span", "minimum_export_span_seconds": 6.0,
                "sample_rate_hz": rate, "window_seconds": window / rate}
    phases = {"early": 0.15, "mid": 0.50, "late": 0.85}
    segment_results = {}
    successful = []
    for label, fraction in phases.items():
        export_time = min(max(1.0, fraction * safe_span), safe_span - 3.0)
        predicted_song_time = export_time + lag_seconds
        export_start = round(export_time * rate)
        song_start = round(predicted_song_time * rate)
        primary = _best_waveform_match(song, export, song_start, export_start, window, radius) \
            if predicted_song_time >= 0 else None
        control_starts = []
        max_start = len(song) / rate - 2.0
        for control_fraction in (0.08, 0.50, 0.92):
            candidate = control_fraction * max_start
            if abs(candidate - predicted_song_time) >= 3.0 and all(abs(candidate - old) >= 3.0 for old in control_starts):
                control_starts.append(candidate)
        controls = []
        for candidate in control_starts:
            result = _best_waveform_match(song, export, round(candidate * rate),
                                          export_start, window, radius)
            if result:
                controls.append({"reference_song_time_seconds": candidate,
                                 "waveform_pearson": result["waveform_pearson"],
                                 "lag_delta_seconds": result["lag_delta_seconds"]})
        control_max = max((item["waveform_pearson"] for item in controls), default=None)
        margin = primary["waveform_pearson"] - control_max if primary and control_max is not None else None
        supported = bool(primary and primary["waveform_pearson"] >= 0.10 and margin is not None and margin >= 0.05)
        if supported:
            successful.append(primary["lag_delta_seconds"])
        segment_results[label] = {
            "predicted_song_time_seconds": predicted_song_time,
            "match": primary,
            "mismatched_time_controls": controls,
            "margin_over_best_control": margin,
            "region_supports_source_pattern": supported,
            "status": "scored" if primary else "source_window_outside_audio",
        }
    spread = max(successful) - min(successful) if successful else None
    max_spread = 0.020
    consistent = len(successful) >= 2 and spread is not None and spread <= max_spread
    fine_delta = statistics.median(successful) if consistent else None
    return {
        "status": "distinct_waveform_structure_in_at_least_two_regions" if len(successful) >= 2 else "weak_or_ambiguous",
        "offset_consistency_status": "consistent_across_supported_regions" if consistent else
                                     "insufficient_or_inconsistent_region_offsets",
        "sampled_region_count": len(segment_results),
        "supported_region_count": len(successful),
        "supported_region_labels": [label for label, item in segment_results.items()
                                     if item["region_supports_source_pattern"]],
        "sample_rate_hz": rate,
        "window_seconds": window / rate,
        "bounded_local_lag_search_ms": radius * 1000.0 / rate,
        "lag_resolution_ms": 1000.0 / rate,
        "frame_rate": frame_rate,
        "supported_region_lag_offsets_seconds": successful,
        "supported_region_lag_spread_seconds": spread,
        "maximum_supported_region_lag_spread_seconds": max_spread,
        "downsampling": "eight-sample box average from 16 kHz mono PCM",
        "fine_offset_adjustment_seconds": fine_delta,
        "segments": segment_results,
        "heuristic_guard": "a region supports the source pattern only when waveform correlation >= 0.10 and >= 0.05 above an available mismatched-time control; thresholds are diagnostic, not calibrated",
        "limitation": "2 kHz mono downmix correlation is source-content evidence, not stereo, true-peak, or licensing proof",
    }


def _alignment(cuts: list[float], onsets: list[float], lag_s: float,
               frame_rate: float | None, export_span: float,
               song_span: float) -> dict[str, Any]:
    tolerance = 2.0 / frame_rate if frame_rate else None
    details = []
    distances = []
    in_tolerance = 0
    eligible = 0
    outside_export = 0
    outside_song = 0
    for cut in cuts:
        if cut < 0 or cut > export_span:
            outside_export += 1
            details.append({"cut_seconds": cut, "status": "outside_analyzed_export_span"})
            continue
        song_time = cut + lag_s
        if song_time < 0 or song_time > song_span:
            outside_song += 1
            details.append({"cut_seconds": cut, "song_time_seconds": song_time,
                            "status": "outside_song_audio"})
            continue
        if not onsets:
            details.append({"cut_seconds": cut, "song_time_seconds": song_time,
                            "status": "no_observed_onsets"})
            continue
        index = bisect.bisect_left(onsets, song_time)
        nearby = []
        if index < len(onsets):
            nearby.append(onsets[index])
        if index:
            nearby.append(onsets[index - 1])
        nearest = min(nearby, key=lambda value: abs(value - song_time))
        delta = nearest - song_time
        absolute = abs(delta)
        eligible += 1
        distances.append(absolute)
        matched = bool(tolerance is not None and absolute <= tolerance)
        in_tolerance += int(matched)
        details.append({"cut_seconds": cut, "song_time_seconds": song_time,
                        "nearest_observed_onset_seconds": nearest,
                        "nearest_onset_delta_seconds": delta,
                        "within_two_frames": matched if tolerance is not None else None,
                        "status": "scored"})
    return {
        "status": "scored" if eligible else "no_eligible_cuts",
        "frame_rate": frame_rate,
        "two_frame_tolerance_seconds": tolerance,
        "nominal_onset_timing_allowance_seconds": ONSET_TIMING_ALLOWANCE_S,
        "timing_allowance_basis": "half the spectral window plus one hop; not a calibrated error bound",
        "eligible_cut_count": eligible,
        "cut_count_outside_export_window": outside_export,
        "cut_count_outside_song_audio": outside_song,
        "within_two_frame_count": in_tolerance if tolerance is not None else None,
        "within_two_frame_fraction": in_tolerance / eligible if tolerance is not None and eligible else None,
        "mean_absolute_onset_delta_seconds": sum(distances) / len(distances) if distances else None,
        "median_absolute_onset_delta_seconds": statistics.median(distances) if distances else None,
        "p90_absolute_onset_delta_seconds": sorted(distances)[max(0, math.ceil(0.9 * len(distances)) - 1)] if distances else None,
        "cuts": details,
    }


def _audio_quality(samples: array) -> dict[str, Any]:
    peak = max((abs(value) for value in samples), default=0)
    peak_db = 20.0 * math.log10(peak / 32768.0) if peak else None
    clipped = sum(1 for value in samples if abs(value) >= 32767)
    window_samples = max(1, round(SILENCE_WINDOW_S * RATE))
    rms_windows: list[tuple[float, float, float]] = []
    for start in range(0, len(samples), window_samples):
        chunk = samples[start:start + window_samples]
        rms = math.sqrt(sum(value * value for value in chunk) / len(chunk)) if chunk else 0.0
        db = 20.0 * math.log10(rms / 32768.0) if rms else -120.0
        rms_windows.append((start / RATE, min(len(samples), start + len(chunk)) / RATE, db))
    silent = [db <= SILENCE_DBFS for _, _, db in rms_windows]
    spans = []
    begin = None
    for index, is_silent in enumerate(silent + [False]):
        if is_silent and begin is None:
            begin = index
        elif not is_silent and begin is not None:
            spans.append({"start_seconds": rms_windows[begin][0],
                          "end_seconds": rms_windows[index - 1][1],
                          "duration_seconds": rms_windows[index - 1][1] - rms_windows[begin][0],
                          "region": "leading" if begin == 0 else
                                    "trailing" if index - 1 == len(rms_windows) - 1 else "internal"})
            begin = None
    leading_s = spans[0]["duration_seconds"] if spans and spans[0]["region"] == "leading" else 0.0
    trailing_s = spans[-1]["duration_seconds"] if spans and spans[-1]["region"] == "trailing" else 0.0
    edge_window = max(1, round(0.5 / SILENCE_WINDOW_S))
    fade_candidates = []
    if len(rms_windows) >= edge_window * 2:
        early = statistics.median(item[2] for item in rms_windows[:edge_window])
        next_early = statistics.median(item[2] for item in rms_windows[edge_window:edge_window * 2])
        if next_early - early >= 3.0:
            fade_candidates.append({"edge": "start", "level_change_db": next_early - early,
                                    "interpretation": "possible fade-in or leading quiet"})
        late = statistics.median(item[2] for item in rms_windows[-edge_window:])
        prior_late = statistics.median(item[2] for item in rms_windows[-edge_window * 2:-edge_window])
        if prior_late - late >= 3.0:
            fade_candidates.append({"edge": "end", "level_change_db": prior_late - late,
                                    "interpretation": "possible fade-out or trailing quiet"})
    return {
        "duration_seconds": len(samples) / RATE,
        "measurement_format": "16 kHz mono signed 16-bit PCM diagnostic",
        "peak_dbfs": peak_db,
        "clipped_samples_at_or_above_32767": clipped,
        "clipped_sample_fraction": clipped / len(samples) if samples else None,
        "silence_threshold_dbfs": SILENCE_DBFS,
        "silence_window_seconds": SILENCE_WINDOW_S,
        "silence_window_fraction": sum(silent) / len(silent) if silent else None,
        "rms_timeline_250ms": [
            {"start_seconds": start, "end_seconds": end, "rms_dbfs": db,
             "below_silence_threshold": db <= SILENCE_DBFS}
            for start, end, db in rms_windows
        ],
        "leading_silence_seconds": leading_s,
        "trailing_silence_seconds": trailing_s,
        "silence_spans": spans,
        "edge_fade_candidates": fade_candidates,
        "interpretation": "silence and edge-level changes are reported; intent is not inferred; mono s16 diagnostics do not certify original stereo true peak or clipping",
    }


def _parse_cuts(path: Path) -> list[float]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read cut-time JSON: {exc}") from exc
    if not isinstance(value, list):
        raise ValueError("--cuts-json must contain a JSON array of cut times in seconds")
    cuts = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or item < 0:
            raise ValueError(f"Cut {index} must be a finite nonnegative number of seconds")
        cuts.append(float(item))
    if any(right <= left for left, right in zip(cuts, cuts[1:])):
        raise ValueError("Cut times must be in strictly increasing order")
    return cuts


def _run_self_check() -> dict[str, Any]:
    """Small deterministic numeric check; it uses no media files or app data."""
    started = time.perf_counter()
    source = [math.sin(i * 0.071) + 0.31 * math.sin(i * 0.017) + 0.13 * math.cos(i * 0.113)
              for i in range(1_600)]
    source_start, export_start = 420, 120
    known_lag = source_start - export_start
    export = [0.17 * math.sin(i * 1.31) for i in range(export_start)] + source[source_start:source_start + 1_000]
    lag_result = _infer_offset(source, export, sample_step=4)
    if lag_result.get("status") != "estimated" or abs(lag_result["lag_frames"] - known_lag) > 1:
        raise AssertionError(f"known-lag correlation failed: {lag_result}")
    controls = lag_result.get("mismatched_reference_controls", {})
    if controls and any(item["spectral_flux_correlation"] is not None and
                        item["spectral_flux_correlation"] >= lag_result["spectral_flux_correlation"]
                        for item in controls.values()):
        raise AssertionError("matched reference did not outrank mismatched controls")
    state = 0x4D595DF4
    waveform = array("f")
    for _ in range(12_000):
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        waveform.append((state / 0xFFFFFFFF) - 0.5)
    export_start = 400
    song_start = 2_400
    exported = array("f", [0.17 * math.sin(index * 1.31) for index in range(export_start)])
    exported.extend(waveform[song_start:song_start + 4_000])
    waveform_match = _best_waveform_match(waveform, exported, song_start,
                                          export_start, 3_000, lag_radius=240)
    wrong_excerpt = _best_waveform_match(waveform, exported, 7_000,
                                         export_start, 3_000, lag_radius=0)
    if not waveform_match or waveform_match["waveform_pearson"] < 0.99 or not wrong_excerpt or \
            waveform_match["waveform_pearson"] <= wrong_excerpt["waveform_pearson"] + 0.2:
        raise AssertionError(f"known waveform excerpt did not outrank a mismatched control: {waveform_match}, {wrong_excerpt}")
    global _run
    original_run = _run
    captured_command: list[str] = []
    synthetic_metadata = (
        "frame:0 pts:0 pts_time:0\n"
        "lavfi.aspectralstats.1.flux=0.01\n"
        "frame:1 pts:128 pts_time:0.008\n"
        "lavfi.aspectralstats.1.flux=0.02\n"
        "frame:2 pts:256 pts_time:0.016\n"
        "lavfi.aspectralstats.1.flux=0.03\n"
    ).encode("ascii")

    def fake_ffmpeg(command: list[str], *, timeout: float, what: str) -> subprocess.CompletedProcess[bytes]:
        captured_command.extend(command)
        return subprocess.CompletedProcess(command, 0, synthetic_metadata, b"")

    try:
        _run = fake_ffmpeg
        parsed_flux, parsed_timing = _spectral_flux(Path("in-memory-synthetic.wav"), "fake-ffmpeg", 1.0)
        if len(parsed_flux) != 3 or parsed_timing["first_pts_seconds"] != 0.0 or \
                parsed_timing["frame_step_seconds_min"] != HOP / RATE or \
                not any("aresample=16000" in argument and "asetnsamples=n=128" in argument
                        for argument in captured_command):
            raise AssertionError(f"spectral-frame conversion/timestamp parsing failed: {parsed_timing}")
        synthetic_metadata = (
            "frame:0 pts:0 pts_time:0\n"
            "lavfi.aspectralstats.1.flux=0.01\n"
            "frame:1 pts:128 pts_time:0.008\n"
            "frame:2 pts:256 pts_time:0.016\n"
            "lavfi.aspectralstats.1.flux=0.03\n"
        ).encode("ascii")
        try:
            _spectral_flux(Path("missing-frame-value.wav"), "fake-ffmpeg", 1.0)
        except RuntimeError as exc:
            if "Missing spectral-flux value" not in str(exc):
                raise
        else:
            raise AssertionError("missing spectral-flux row was not rejected")
    finally:
        _run = original_run
    fixture = array("h", [0] * RATE + [32767, -32768] * (RATE // 2))
    quality = _audio_quality(fixture)
    if quality["clipped_samples_at_or_above_32767"] != RATE or quality["leading_silence_seconds"] < 0.9:
        raise AssertionError(f"silence/clipping metrics failed: {quality}")
    return {"status": "passed", "elapsed_seconds": time.perf_counter() - started,
            "known_lag_frames": known_lag,
            "measured_lag_frames": lag_result["lag_frames"],
            "control_count": len(controls), "clipped_sample_count": quality["clipped_samples_at_or_above_32767"],
            "leading_silence_seconds": quality["leading_silence_seconds"],
            "waveform_control_correlation": waveform_match["waveform_pearson"],
            "mismatched_waveform_correlation": wrong_excerpt["waveform_pearson"],
            "spectral_frame_pts_count": parsed_timing["frame_count"],
            "spectral_timestamp_step_seconds": parsed_timing["frame_step_seconds_min"]}


def _analysis(song_path: Path, export_path: Path, cuts_path: Path, output_path: Path,
              song_offset: float | None, ffmpeg: str, ffprobe: str) -> dict[str, Any]:
    song_probe = _probe(song_path, ffprobe)
    export_probe = _probe(export_path, ffprobe)
    if song_probe["duration_seconds"] > MAX_SONG_SECONDS:
        raise ValueError(f"Song duration exceeds the {MAX_SONG_SECONDS:g}-second limit")
    if not song_probe["audio_stream"]:
        raise ValueError("Song has no audio stream")
    cuts = _parse_cuts(cuts_path)
    song_pcm = _decode_mono(song_path, ffmpeg, MAX_SONG_SECONDS)
    export_samples = array("h")
    export_has_audio = bool(export_probe["audio_stream"])
    if export_has_audio:
        export_samples = _decode_mono(export_path, ffmpeg, MAX_EXPORT_SECONDS)
    song_flux, song_flux_timing = _spectral_flux(song_path, ffmpeg, MAX_SONG_SECONDS)
    song_onsets, onset_method = _detect_onsets(song_flux)
    song_coarse = _coarsen(song_flux, mode="max")
    export_span = min(export_probe["duration_seconds"], MAX_EXPORT_SECONDS)
    if export_has_audio:
        export_flux, export_flux_timing = _spectral_flux(export_path, ffmpeg, MAX_EXPORT_SECONDS)
        export_onsets, export_onset_method = _detect_onsets(export_flux)
        export_coarse = _coarsen(export_flux, mode="max")
        offset_result = _infer_offset(song_coarse, export_coarse, sample_step=4)
    else:
        export_flux = array("f")
        export_flux_timing = {"status": "no_audio_stream"}
        export_onsets, export_onset_method, export_coarse = [], None, []
        offset_result = {"status": "no_export_audio_stream"}
    if song_offset is None:
        selected_offset = offset_result.get("lag_seconds")
        selected_offset_source = "inferred_from_independent_audio_feature_correlation"
    else:
        selected_offset = song_offset
        selected_offset_source = "user_supplied"
    cuts_analysis: dict[str, Any]
    mismatch_controls: dict[str, Any] = {}
    source_match = {"status": "not_evaluated", "reason": "export has no audio stream"}
    alignment_reliability = False
    waveform_result = {"status": "not_evaluated"}
    refined_offset = selected_offset
    if selected_offset is not None and export_has_audio:
        coarse_dt = HOP * COARSE_GROUP / RATE
        minimum_window = round(MIN_CORRELATION_WINDOW_S / coarse_dt)
        window = offset_result.get("window_frames") or min(
            round(CORRELATION_WINDOW_S / coarse_dt), len(song_coarse), len(export_coarse))
        lag_frames = round(selected_offset / coarse_dt)
        if window >= minimum_window and window <= min(len(song_coarse), len(export_coarse)):
            if song_offset is None:
                main_match = {
                    "song_start_frame": round(offset_result["song_excerpt_start_seconds"] / coarse_dt),
                    "export_start_frame": round(offset_result["export_excerpt_start_seconds"] / coarse_dt),
                    "lag_frames": offset_result["lag_frames"],
                    "spectral_flux_correlation": offset_result["spectral_flux_correlation"],
                    "window_frames": window,
                }
            else:
                main_match = _best_window_for_lag(song_coarse, export_coarse, lag_frames, window)
            if main_match is None:
                source_match = {"status": "insufficient_overlap", "selected_song_offset_seconds": selected_offset,
                                "minimum_overlap_seconds": MIN_CORRELATION_WINDOW_S}
            else:
                main_flux_corr = main_match["spectral_flux_correlation"]
                song_start_coarse = main_match["song_start_frame"]
                export_start_coarse = main_match["export_start_frame"]
                exclusion = max(1, round(1.0 / coarse_dt))
                controls = _control_song_starts(len(song_coarse), window, song_start_coarse, exclusion)
                for label, control_start in controls.items():
                    corr = _window_pearson(song_coarse, control_start, export_coarse,
                                           export_start_coarse, window)
                    mismatch_controls[label] = {
                        "song_excerpt_start_seconds": control_start * coarse_dt,
                        "lag_seconds": (control_start - export_start_coarse) * coarse_dt,
                        "spectral_flux_correlation": corr,
                        "overlap_seconds": window * coarse_dt,
                    }
                available_controls = [item["spectral_flux_correlation"] for item in mismatch_controls.values()
                                      if item.get("spectral_flux_correlation") is not None]
                control_max = max(available_controls) if available_controls else None
                margin = main_flux_corr - control_max if main_flux_corr is not None and control_max is not None else None
                waveform_result = _waveform_checks(song_pcm, export_samples, selected_offset,
                                                   export_span, export_probe["frame_rate"])
                fine_delta = waveform_result.get("fine_offset_adjustment_seconds")
                if fine_delta is not None:
                    refined_offset = selected_offset + fine_delta
                wave_supported = (
                    waveform_result.get("status") == "distinct_waveform_structure_in_at_least_two_regions" and
                    waveform_result.get("offset_consistency_status") == "consistent_across_supported_regions"
                )
                # These conservative evidence guards do not rate the edit or song quality.
                alignment_reliability = bool(main_flux_corr is not None and main_flux_corr >= 0.20 and
                                             margin is not None and margin >= 0.05 and
                                             len(available_controls) >= 2 and wave_supported)
                source_match = {
                    "status": "source_pattern_supported_in_sampled_regions_with_consistent_timing" if alignment_reliability else "weak_or_ambiguous",
                    "selected_song_offset_seconds": selected_offset,
                    "refined_song_offset_seconds": refined_offset,
                    "offset_convention": "song source time equals export timeline time plus this signed offset",
                    "selected_offset_source": selected_offset_source,
                    "rhythmic_feature_similarity": main_flux_corr,
                    "rhythmic_feature_window_seconds": window * coarse_dt,
                    "band_profile_match": {"status": "not_computed"},
                    "mismatched_reference_controls": mismatch_controls,
                    "margin_over_best_control": margin,
                    "waveform_content_checks": waveform_result,
                    "heuristic_guard": "cut-to-song timing is scored only with flux correlation >= 0.20, >= 0.05 above at least two mismatched song excerpts, waveform similarity above mismatched-time controls in at least two of three regions, and supported-region offsets agreeing within 20 ms; not a calibrated detection probability",
                    "interpretation": "rhythmic-feature similarity is not song identity; waveform checks support source content in sampled regions only and do not prove continuous presence through the full export",
                    "matched_excerpt_song_start_seconds": song_start_coarse * coarse_dt,
                    "matched_excerpt_export_start_seconds": export_start_coarse * coarse_dt,
                    "overlap_seconds": window * coarse_dt,
                }
        else:
            source_match = {"status": "insufficient_overlap", "selected_song_offset_seconds": selected_offset,
                            "minimum_overlap_seconds": MIN_CORRELATION_WINDOW_S}
    if alignment_reliability and selected_offset is not None:
        cuts_analysis = _alignment(cuts, song_onsets, refined_offset,
                                   export_probe["frame_rate"], export_span,
                                   len(song_pcm) / RATE)
        if mismatch_controls:
            for label, control in mismatch_controls.items():
                control_offset = control["lag_seconds"]
                mismatch_controls[label]["cut_alignment"] = _alignment(
                    cuts, song_onsets, control_offset, export_probe["frame_rate"], export_span,
                    len(song_pcm) / RATE)
    else:
        cuts_analysis = {
            "status": "withheld_no_reliable_song_to_export_alignment",
            "reason": "The supplied song did not produce a distinct enough match in the export; cut-to-song-onset evidence is not reported.",
            "cut_count": len(cuts),
            "frame_rate": export_probe["frame_rate"],
            "two_frame_tolerance_seconds": 2.0 / export_probe["frame_rate"] if export_probe["frame_rate"] else None,
        }
    report = {
        "analyzer": "music_output_analysis.py",
        "version": 2,
        "inputs": {"song": str(song_path), "export": str(export_path), "cuts_json": str(cuts_path),
                   "song_offset_seconds": song_offset},
        "limits": {"pcm_sample_rate_hz": RATE, "song_max_seconds": MAX_SONG_SECONDS,
                   "export_pcm_max_seconds": MAX_EXPORT_SECONDS,
                   "song_samples_decoded": len(song_pcm), "export_samples_decoded": len(export_samples),
                   "analysis_note": "only the first 60 seconds of export audio are decoded; decodes and spectral analysis are sequential and limited to one FFmpeg thread"},
        "song": {"duration_seconds": song_probe["duration_seconds"],
                 "audio_codec": song_probe["audio_stream"].get("codec_name")},
        "export": {"duration_seconds": export_probe["duration_seconds"],
                    "audio_codec": export_probe["audio_stream"].get("codec_name") if export_probe["audio_stream"] else None,
                    "video_frame_rate": export_probe["frame_rate"],
                    "video_frame_rate_basis": export_probe["frame_rate_basis"]},
        "independent_onsets": {"song": onset_method,
                               "export": export_onset_method or {"status": "no_audio_stream", "detected_count": 0}},
        "spectral_flux_timing": {"song": song_flux_timing, "export": export_flux_timing},
        "offset_inference": offset_result,
        "source_song_match": source_match,
        "cut_to_song_onset_alignment": cuts_analysis,
        "cut_to_export_onset_proximity": _alignment(cuts, export_onsets, 0.0,
                                                    export_probe["frame_rate"], export_span,
                                                    export_span) if export_has_audio else {
            "status": "no_export_audio_stream", "cut_count": len(cuts)},
        "export_audio_quality": _audio_quality(export_samples) if export_samples else {
            "status": "no_audio_stream", "peak_dbfs": None, "clipped_samples_at_or_above_32767": None,
            "silence_window_fraction": None, "silence_spans": []},
        "interpretation": "All event, correlation, silence and tolerance values are diagnostics. They do not judge creative quality, prove licensing, or imply a global pass.",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                           encoding="utf-8")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--song", type=Path, help="source song file")
    parser.add_argument("--export", type=Path, help="rendered video export")
    parser.add_argument("--cuts-json", type=Path,
                        help="JSON array of strictly increasing cut times in seconds")
    parser.add_argument("--output", type=Path, help="private JSON report destination")
    parser.add_argument("--song-offset", type=float, default=None,
                        help="signed offset in seconds: song source time = export time + offset; inferred if omitted")
    parser.add_argument("--ffmpeg", default=None, help="FFmpeg executable; defaults to FILM_FFMPEG or PATH")
    parser.add_argument("--ffprobe", default=None, help="FFprobe executable; defaults to FILM_FFPROBE or PATH")
    parser.add_argument("--self-check", action="store_true",
                        help="run deterministic in-memory offset, flux-timestamp parsing, control, silence and clipping checks")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.self_check:
        print(json.dumps(_run_self_check(), indent=2))
        return 0
    missing = [name for name in ("song", "export", "cuts_json", "output")
               if getattr(args, name) is None]
    if missing:
        parser.error("required unless --self-check is used: " + ", ".join(f"--{name.replace('_', '-')}" for name in missing))
    if args.song_offset is not None and not math.isfinite(args.song_offset):
        raise SystemExit("--song-offset must be a finite number")
    if any(args.output.resolve() == input_path.resolve()
           for input_path in (args.song, args.export, args.cuts_json)):
        raise SystemExit("--output must not overwrite the song, export, or cuts JSON")
    try:
        report = _analysis(args.song, args.export, args.cuts_json, args.output,
                           args.song_offset,
                           args.ffmpeg or _tool_path("FILM_FFMPEG", "ffmpeg"),
                           args.ffprobe or _tool_path("FILM_FFPROBE", "ffprobe"))
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"music output analysis: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"report": str(args.output),
                      "source_song_match": report["source_song_match"].get("status"),
                      "cut_alignment": report["cut_to_song_onset_alignment"].get("status"),
                      "decoded_export_seconds": report["limits"]["export_samples_decoded"] / RATE},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
