#!/usr/bin/env python3
"""行政文書の公開用の束を作る。

`I:\\行政文書` の案件フォルダから、`tools/gyosei/publish.json` に書いた案件だけを
`work/gyosei/public/<id>/` へ書き出す。本番へは `deploy/sync_gyosei.py` で送る。

- PDF は頁ごとに画像にして作り直す（墨消しの下の文字・文字の層・注釈・作成者などのメタデータを残さない）。
  白黒の頁はグレーの JPEG、色のある頁はカラーの JPEG、200dpi。
- `omit` に書いたファイル（請求者の住所が載った請求書など）と、`_paged.md` 以外の付属物は出さない。
- 文字起こし（`_paged.md`）は頁に分けて `<nn>.json` にする。画面は PHP が読む（nginx は /data/ の json を出さない）。
- 案件の情報は `case.json` のうち公開してよい項目だけを写す（notes・requester・source_dirs は出さない）。

    python tools/gyosei/build_public.py
    python tools/gyosei/build_public.py --source I:/行政文書 --only 14130-2026-10-15-ikegami

元の PDF が変わっていなければ作り直さない（`work/gyosei/build_state.json` に元の SHA-256 を控える）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
import numpy
from PIL import Image

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).resolve().parent / "publish.json"
DEFAULT_SOURCE = Path("I:/行政文書")
DEFAULT_OUTPUT = WORKSPACE_ROOT / "work" / "gyosei" / "public"
STATE_FILE = WORKSPACE_ROOT / "work" / "gyosei" / "build_state.json"

DPI = 200
JPEG_QUALITY = 75
# 画素ごとの (最大 - 最小) の平均がこれ未満ならグレーとみなす。スキャンの白黒の頁は 0〜1 くらい。
GRAY_SATURATION = 4.0

# 種類の並び。I:\行政文書 の README の「種類」の語。
KIND_ORDER = [
    "請求書", "受付通知", "補正", "意思確認", "延長通知", "決定通知", "送付状", "開示文書", "資料",
    "審査請求書", "諮問通知", "弁明書", "理由説明書", "反論書", "意見書", "審理通知", "答申", "裁決",
    "通知", "監査請求書", "監査結果",
]
# case.json から公開する項目。
PUBLIC_CASE_FIELDS = (
    "agency", "agency_code", "body", "route", "request_kind", "legal_basis",
    "requested_on", "title", "request_text", "decisions", "appeals", "status",
)
PAGE_RE = re.compile(r"^### -- Begin Page (\d+) --\s*$", re.M)
END_RE = re.compile(r"^### -- End --\s*$", re.M)
POSTAL_RE = re.compile(r"〒\s*\d{3}\s*-\s*\d{4}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def natural_key(text: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


def parse_name(stem: str) -> dict:
    """`{日付}_{種類}[_{補足}]` を分ける。"""
    parts = stem.split("_", 2)
    date = parts[0] if parts and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]) else ""
    kind = parts[1] if len(parts) > 1 else stem
    detail = parts[2] if len(parts) > 2 else ""
    return {"date": date, "kind": kind, "detail": detail}


def omitted_label(stem: str) -> str:
    parts = parse_name(stem)
    return f"{parts['kind']}（{parts['detail']}）" if parts["detail"] else parts["kind"]


def sort_key(item: dict) -> tuple:
    kind = item["kind"]
    order = KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER)
    # 別紙は本体（決定通知など）の後ろ。
    return (order, item["date"], item["detail"].startswith("別紙"), natural_key(item["detail"]))


def split_pages(markdown: str, page_count: int) -> list[str]:
    pages = [""] * page_count
    matches = list(PAGE_RE.finditer(markdown))
    for index, match in enumerate(matches):
        number = int(match.group(1))
        start = match.end()
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = END_RE.split(markdown[start:stop], 1)[0].strip()
        if 1 <= number <= page_count:
            pages[number - 1] = body
    return pages


def rasterize(source: Path, destination: Path, title: str) -> int:
    src = fitz.open(source)
    out = fitz.open()
    for page in src:
        pix = page.get_pixmap(dpi=DPI, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        sample = numpy.asarray(image.resize((max(1, pix.width // 8), max(1, pix.height // 8))), dtype=numpy.int16)
        if float((sample.max(axis=2) - sample.min(axis=2)).mean()) < GRAY_SATURATION:
            image = image.convert("L")
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
        rect = page.rect
        new_page = out.new_page(width=rect.width, height=rect.height)
        new_page.insert_image(new_page.rect, stream=buffer.getvalue())
    count = src.page_count
    src.close()
    out.set_metadata({"title": title, "author": "", "subject": "", "keywords": "", "creator": "", "producer": ""})
    out.del_xml_metadata()
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(".tmp")
    out.save(tmp, garbage=4, deflate=True, no_new_id=True)
    out.close()
    tmp.replace(destination)
    return count


def check_published(pdf: Path) -> None:
    doc = fitz.open(pdf)
    try:
        for page in doc:
            if page.get_text().strip():
                raise SystemExit(f"[ERROR] 文字の層が残っています: {pdf}")
            if list(page.annots()):
                raise SystemExit(f"[ERROR] 注釈が残っています: {pdf}")
    finally:
        doc.close()


def build_case(entry: dict, source_root: Path, output_root: Path, state: dict) -> None:
    case_id = entry["id"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", case_id):
        raise SystemExit(f"[ERROR] id は英小文字・数字・ハイフンだけ: {case_id}")
    case_dir = source_root / entry["case_dir"]
    meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    omit = entry.get("omit", {})
    for name in omit:
        if not (case_dir / name).is_file():
            raise SystemExit(f"[ERROR] omit のファイルがありません: {case_dir / name}")

    items = []
    for pdf in sorted(case_dir.glob("*.pdf")):
        if pdf.name in omit:
            continue
        items.append({"source": pdf, **parse_name(pdf.stem)})
    items.sort(key=sort_key)

    out_dir = output_root / case_id
    out_dir.mkdir(parents=True, exist_ok=True)
    case_state = state.setdefault(case_id, {})
    documents = []
    keep = {"case.json"}
    for number, item in enumerate(items, start=1):
        stem = f"{number:02d}"
        pdf_out = out_dir / f"{stem}.pdf"
        text_out = out_dir / f"{stem}.json"
        keep.update({pdf_out.name, text_out.name})
        source_hash = sha256_file(item["source"])
        label = item["source"].stem
        title = f"{meta.get('agency', '')} {meta.get('title', '')} {label}".strip()
        previous = case_state.get(stem, {})
        if previous.get("source_sha256") != source_hash or not pdf_out.is_file():
            print(f"  {stem} 作り直す: {item['source'].name}", flush=True)
            pages = rasterize(item["source"], pdf_out, title)
        else:
            pages = previous.get("pages", 0)
        check_published(pdf_out)
        case_state[stem] = {"source_sha256": source_hash, "pages": pages, "source": item["source"].name}

        paged = item["source"].with_name(item["source"].stem + "_paged.md")
        page_texts = split_pages(paged.read_text(encoding="utf-8"), pages) if paged.is_file() else [""] * pages
        for page_number, text in enumerate(page_texts, start=1):
            if POSTAL_RE.search(text):
                print(f"  [確認] {stem} p.{page_number} に郵便番号: {POSTAL_RE.search(text).group(0)}（請求者の住所でないか見る）")
        text_out.write_text(
            json.dumps({"pages": page_texts, "transcribed": paged.is_file()}, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        documents.append({
            "file": pdf_out.name,
            "text": text_out.name,
            "name": label,
            "date": item["date"],
            "kind": item["kind"],
            "detail": item["detail"],
            "pages": pages,
            "bytes": pdf_out.stat().st_size,
            "sha256": sha256_file(pdf_out),
        })

    for stale in out_dir.iterdir():
        if stale.name not in keep:
            print(f"  消す: {stale.name}")
            stale.unlink()
    for stale_key in [key for key in case_state if f"{key}.pdf" not in keep]:
        del case_state[stale_key]

    public = {"id": case_id, **{key: meta[key] for key in PUBLIC_CASE_FIELDS if key in meta}}
    public["summary"] = entry.get("summary", "")
    public["documents"] = documents
    public["omitted"] = [{"name": omitted_label(Path(name).stem), "reason": reason} for name, reason in omit.items()]
    public["published_on"] = case_state.get("_published_on") or dt.date.today().isoformat()
    case_state["_published_on"] = public["published_on"]
    (out_dir / "case.json").write_text(json.dumps(public, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    total_pages = sum(doc["pages"] for doc in documents)
    total_bytes = sum(doc["bytes"] for doc in documents)
    print(f"{case_id}: {len(documents)} 件・{total_pages} 頁・{total_bytes / 1e6:.1f}MB（出さない物 {len(omit)} 件）")


def main() -> int:
    parser = argparse.ArgumentParser(description="行政文書の公開用の束を作る。")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="I:\\行政文書 の場所")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="書き出し先")
    parser.add_argument("--only", default="", help="この id の案件だけ作る")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    output_root = Path(args.output)
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.is_file() else {}
    ids = {entry["id"] for entry in manifest["cases"]}
    for entry in manifest["cases"]:
        if args.only and entry["id"] != args.only:
            continue
        build_case(entry, Path(args.source), output_root, state)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if not args.only and output_root.is_dir():
        for stale in output_root.iterdir():
            if stale.is_dir() and stale.name not in ids:
                print(f"[注意] publish.json に無い案件が書き出し先に残っています: {stale}（手で消す）")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
