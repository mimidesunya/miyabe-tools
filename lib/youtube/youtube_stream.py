#!/usr/bin/env python3
"""正規化OFF時の中継ストリーム用ヘルパー。

PHP から同期的に呼ぶ。標準出力に JSON を1行だけ返す（秘密は出さない）。

サブコマンド:
  init        --job-dir DIR
      job.json のメタデータで YouTube のレジューム対応アップロードセッションを開始し、
      セッションURI を job.json に書き込む。PHP はそのURIへチャンクを PUT 中継する。
      出力: {"ok": true, "session_uri": "..."}

  postprocess --job-dir DIR --video-id ID
      アップロード完了後の仕上げ。サムネイルがあれば設定し、status.json を done にする。
      出力: {"ok": true, "video_id": "...", "watch_url": "...", "thumbnail_set": bool}
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("yt_worker", os.path.join(_HERE, "worker.py"))
worker = importlib.util.module_from_spec(_spec)
sys.modules["yt_worker"] = worker
_spec.loader.exec_module(worker)

RESUMABLE_INIT_URL = (
    "https://www.googleapis.com/upload/youtube/v3/videos"
    "?uploadType=resumable&part=snippet,status"
)


def out(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_job(job_dir: str) -> dict:
    with open(os.path.join(job_dir, "job.json"), "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_job(job_dir: str, job: dict) -> None:
    path = os.path.join(job_dir, "job.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(job, handle, ensure_ascii=False)
    os.replace(tmp, path)


def access_token(token_path: str) -> str:
    """refresh_token から短命のアクセストークンを取得する。"""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    with open(token_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    creds = Credentials.from_authorized_user_info(data, scopes=[
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl",
    ])
    creds.refresh(Request())
    return creds.token


def build_metadata(job: dict) -> dict:
    body = {
        "snippet": {
            "title": job.get("title") or "無題",
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
    return body


def cmd_init(job_dir: str) -> int:
    job = read_job(job_dir)
    token = access_token(job["token_path"])
    total = int(job.get("source_size") or 0)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/*",
    }
    if total > 0:
        headers["X-Upload-Content-Length"] = str(total)

    resp = requests.post(RESUMABLE_INIT_URL, headers=headers,
                         data=json.dumps(build_metadata(job)), timeout=30)
    if resp.status_code not in (200, 201):
        worker.write_status(job_dir, state="error", message="アップロード開始に失敗しました。",
                            error=f"resumable init status={resp.status_code}")
        out({"ok": False, "error": f"init status={resp.status_code}"})
        return 1
    session_uri = resp.headers.get("Location", "")
    if not session_uri:
        out({"ok": False, "error": "no session uri"})
        return 1

    job["session_uri"] = session_uri
    write_job(job_dir, job)
    worker.write_status(job_dir, state="uploading", progress=0, message="YouTube へ送信しています…")
    out({"ok": True, "session_uri": session_uri})
    return 0


def cmd_postprocess(job_dir: str, video_id: str) -> int:
    job = read_job(job_dir)
    thumbnail_set = False
    thumb = job.get("thumbnail_path")
    if video_id and thumb and os.path.isfile(thumb):
        try:
            from googleapiclient.http import MediaFileUpload
            youtube = worker.build_youtube_client(job["token_path"])
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumb)).execute()
            thumbnail_set = True
        except Exception as error:  # サムネ失敗は致命ではない
            worker.log(job_dir, f"stream thumbnail set failed: {error}")

    watch_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
    worker.write_status(job_dir, state="done", progress=100,
                        message="アップロードが完了しました。",
                        video_id=video_id, watch_url=watch_url, thumbnail_set=thumbnail_set)
    out({"ok": True, "video_id": video_id, "watch_url": watch_url, "thumbnail_set": thumbnail_set})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("--job-dir", required=True)
    p_post = sub.add_parser("postprocess")
    p_post.add_argument("--job-dir", required=True)
    p_post.add_argument("--video-id", default="")
    args = parser.parse_args()

    job_dir = os.path.abspath(args.job_dir)
    try:
        if args.cmd == "init":
            return cmd_init(job_dir)
        if args.cmd == "postprocess":
            return cmd_postprocess(job_dir, args.video_id)
    except Exception as error:
        out({"ok": False, "error": str(error)})
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
