#!/usr/bin/env python3
"""YouTube アップロードのバックグラウンドワーカー。

ジョブディレクトリの job.json を読み、
  1. （任意）ffmpeg の二段階 loudnorm で音声を正規化し
  2. YouTube へレジューム対応でアップロードする。
進捗は同じディレクトリの status.json へ逐次書き出す。PHP 側はこれをポーリングする。

音声正規化のパラメータと方式は kits の
TypeScript/media/normalize-mp4-audio.ts（二段階 loudnorm + lookahead limiter）に合わせている。

使い方:
    python worker.py --job-dir /var/www/work/youtube/jobs/<id>
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import traceback


# 検証で許容する目標からのずれ（ffmpeg の測定誤差ぶん）。
LOUDNESS_TOLERANCE_DB = 1.0
TRUE_PEAK_TOLERANCE_DB = 0.3


def log(job_dir: str, message: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    with open(os.path.join(job_dir, "worker.log"), "a", encoding="utf-8") as handle:
        handle.write(line)


def write_status(job_dir: str, **fields) -> None:
    """status.json を原子的に置き換える。"""
    path = os.path.join(job_dir, "status.json")
    current = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                current = json.load(handle)
        except Exception:
            current = {}
    current.update(fields)
    current["updated_at"] = int(time.time())
    tmp_fd, tmp_path = tempfile.mkstemp(dir=job_dir, prefix=".status-", suffix=".json")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            json.dump(current, handle, ensure_ascii=False)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ─────────────────────────────────────────────
# ffmpeg loudnorm（二段階 + limiter）
# ─────────────────────────────────────────────

def format_filter_number(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def build_loudnorm_filter(loudness: float, true_peak: float, lra: float, measured: dict | None) -> str:
    parts = [
        f"I={format_filter_number(loudness)}",
        f"TP={format_filter_number(true_peak)}",
        f"LRA={format_filter_number(lra)}",
    ]
    if measured:
        parts += [
            f"measured_I={measured['input_i']}",
            f"measured_TP={measured['input_tp']}",
            f"measured_LRA={measured['input_lra']}",
            f"measured_thresh={measured['input_thresh']}",
            f"offset={measured['target_offset']}",
            "linear=true",
        ]
    return ":".join(parts)


def run_ffmpeg(ffmpeg: str, args: list[str]) -> str:
    result = subprocess.run(
        [ffmpeg, *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (exit={result.returncode})\n{result.stdout[-4000:]}")
    return result.stdout or ""


def measure_loudness(ffmpeg: str, source: str, loudness: float, true_peak: float, lra: float) -> dict:
    output = run_ffmpeg(ffmpeg, [
        "-hide_banner", "-nostats",
        "-i", source,
        "-map", "0:a:0",
        "-filter:a:0", f"loudnorm={build_loudnorm_filter(loudness, true_peak, lra, None)}:print_format=json",
        "-f", "null", "-",
    ])
    start = output.rfind("{")
    end = output.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise RuntimeError("loudnorm の解析結果を取得できませんでした。")
    parsed = json.loads(output[start:end + 1])
    for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        raw = parsed.get(key)
        if raw is None or not math.isfinite(float(raw)):
            raise RuntimeError(f"loudnorm の実測値 {key} が数値ではありません（無音などの可能性）。")
    return parsed


def limiter_limit(true_peak_db: float) -> float:
    linear = 10 ** (true_peak_db / 20)
    return min(1.0, max(0.0625, linear))


def probe_sample_rate(ffprobe: str, source: str) -> int | None:
    try:
        raw = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", source],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="utf-8",
        ).stdout.strip()
        value = int(raw)
        return value if value > 0 else None
    except Exception:
        return None


def normalize_audio(job_dir: str, ffmpeg: str, ffprobe: str, source: str, dest: str, job: dict) -> None:
    loudness = float(job.get("loudness", -14))
    true_peak = float(job.get("true_peak", -1))
    lra = float(job.get("lra", 11))
    bitrate = str(job.get("audio_bitrate", "192k"))
    peak_headroom = float(job.get("peak_headroom", 0.5))

    write_status(job_dir, state="normalizing", progress=0, message="音声を解析しています…")
    log(job_dir, f"normalize start: I={loudness} TP={true_peak} LRA={lra}")

    measured = measure_loudness(ffmpeg, source, loudness, true_peak, lra)
    log(job_dir, f"measured: I={measured['input_i']} TP={measured['input_tp']} LRA={measured['input_lra']}")
    write_status(job_dir, message="音声を正規化しています…")

    sample_rate = probe_sample_rate(ffprobe, source)
    filter_chain = f"loudnorm={build_loudnorm_filter(loudness, true_peak, lra, measured)}"
    limit = limiter_limit(true_peak - peak_headroom)
    filter_chain += f",alimiter=level=false:limit={format_filter_number(limit)}"

    args = [
        "-y", "-hide_banner", "-nostats",
        "-i", source,
        "-map", "0:v:0", "-map", "0:a:0", "-map_metadata", "0",
        "-c:v", "copy",
        "-filter:a:0", filter_chain,
        "-c:a", "aac", "-b:a", bitrate,
    ]
    if sample_rate:
        args += ["-ar", str(sample_rate)]
    args += ["-movflags", "+faststart", dest]
    run_ffmpeg(ffmpeg, args)

    # 検証パス
    verify = measure_loudness(ffmpeg, dest, loudness, true_peak, lra)
    actual_i = float(verify["input_i"])
    actual_tp = float(verify["input_tp"])
    loud_ok = abs(actual_i - loudness) <= LOUDNESS_TOLERANCE_DB
    tp_ok = actual_tp <= true_peak + TRUE_PEAK_TOLERANCE_DB
    log(job_dir, f"verify: I={actual_i}(target {loudness},{'ok' if loud_ok else 'NG'}) "
                 f"TP={actual_tp}(<= {true_peak},{'ok' if tp_ok else 'NG'})")
    write_status(job_dir, normalized_loudness=actual_i, normalized_true_peak=actual_tp)


# ─────────────────────────────────────────────
# YouTube アップロード
# ─────────────────────────────────────────────

def build_youtube_client(token_path: str):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    with open(token_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("type") != "authorized_user" or not data.get("refresh_token"):
        raise RuntimeError("YouTube のトークンが authorized_user 形式ではありません。")

    credentials = Credentials.from_authorized_user_info(data, scopes=[
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl",
    ])
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def upload_video(job_dir: str, token_path: str, video_path: str, job: dict) -> dict:
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    youtube = build_youtube_client(token_path)

    body = {
        "snippet": {
            "title": job.get("title") or os.path.basename(video_path),
            "description": job.get("description", ""),
            "categoryId": str(job.get("category_id", "22")),
        },
        "status": {
            "privacyStatus": job.get("privacy_status", "private"),
        },
    }
    tags = job.get("tags") or []
    if tags:
        body["snippet"]["tags"] = tags
    if job.get("publish_at"):
        body["status"]["publishAt"] = job["publish_at"]
    if job.get("made_for_kids") is not None:
        body["status"]["selfDeclaredMadeForKids"] = bool(job["made_for_kids"])
    if job.get("synthetic_media") is not None:
        body["status"]["containsSyntheticMedia"] = bool(job["synthetic_media"])

    media = MediaFileUpload(video_path, mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
        notifySubscribers=bool(job.get("notify_subscribers", False)),
    )

    write_status(job_dir, state="uploading", progress=0, message="YouTube へアップロードしています…")
    response = None
    while response is None:
        try:
            progress, response = request.next_chunk()
        except HttpError as error:
            # 5xx は数回リトライ
            if error.resp is not None and int(error.resp.status) in (500, 502, 503, 504):
                log(job_dir, f"upload retry after {error.resp.status}")
                time.sleep(3)
                continue
            raise
        if progress:
            percent = int(progress.progress() * 100)
            write_status(job_dir, progress=percent, message=f"アップロード中… {percent}%")
            log(job_dir, f"upload progress {percent}%")

    video_id = response.get("id", "")
    thumbnail_set = False
    thumbnail_path = job.get("thumbnail_path")
    if thumbnail_path and os.path.isfile(thumbnail_path):
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path),
            ).execute()
            thumbnail_set = True
        except Exception as error:  # サムネ失敗は致命ではない
            log(job_dir, f"thumbnail set failed: {error}")

    return {"video_id": video_id, "thumbnail_set": thumbnail_set}


# ─────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────

def resolve_ffmpeg(name: str) -> str:
    override = os.environ.get(name.upper() + "_PATH")
    if override:
        return override
    return name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    args = parser.parse_args()
    job_dir = os.path.abspath(args.job_dir)

    try:
        with open(os.path.join(job_dir, "job.json"), "r", encoding="utf-8") as handle:
            job = json.load(handle)
    except Exception as error:
        # job.json すら読めない場合でも status を残す
        try:
            write_status(job_dir, state="error", error=f"job.json を読めません: {error}")
        except Exception:
            pass
        return 1

    token_path = job["token_path"]
    source = job["source_path"]

    try:
        if not os.path.isfile(source):
            raise RuntimeError(f"元動画が見つかりません: {source}")

        upload_target = source
        if job.get("normalize", True):
            ffmpeg = resolve_ffmpeg("ffmpeg")
            ffprobe = resolve_ffmpeg("ffprobe")
            normalized = os.path.join(job_dir, "normalized.mp4")
            normalize_audio(job_dir, ffmpeg, ffprobe, source, normalized, job)
            upload_target = normalized
        else:
            log(job_dir, "normalize skipped by job option")

        result = upload_video(job_dir, token_path, upload_target, job)
        watch_url = f"https://www.youtube.com/watch?v={result['video_id']}" if result["video_id"] else ""
        write_status(
            job_dir,
            state="done",
            progress=100,
            message="アップロードが完了しました。",
            video_id=result["video_id"],
            watch_url=watch_url,
            thumbnail_set=result["thumbnail_set"],
        )
        log(job_dir, f"done: {watch_url}")

        # 元動画は容量を食うので、成功したら削除する（正規化ファイルも）。
        if job.get("cleanup_source", True):
            for path in (source, os.path.join(job_dir, "normalized.mp4")):
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except Exception:
                    pass
        return 0
    except Exception as error:
        log(job_dir, "ERROR:\n" + traceback.format_exc())
        write_status(job_dir, state="error", message="失敗しました。", error=str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
