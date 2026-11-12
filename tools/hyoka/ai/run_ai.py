#!/usr/bin/env python3
"""事務事業評価を AI で書き写す（doc/hyoka-ai-extraction.md）。

段ごとに分けてあり、どの段も途中で止めて同じ命令で続きから流せる。出力は --out の下だけに書く。

    plan      対象の文書を選ぶ（構造化済みの自治体は除く）           → plan.json
    grids     PDF の各頁を升目の一覧にする（並行）                 → <slug>/inputs/<doc_id>.json.gz
    legends   自治体ごとに凡例を作り、凡例あり・なしを比べて使うか決める → <slug>/legend.json
    requests  一括処理の要求ファイルを作る                          → requests/part-NNNN.jsonl
    submit    要求ファイルを送り、一括処理を作る                     → batches.json
    collect   一括処理の状態を見て、終わった結果を受け取る             → <slug>/results/<doc_id>.json
    sync      一括処理を使わずに 1 回ずつ送る（小分けの試し・失敗のやり直し）
    assemble  結果を事業の記録にする                               → <slug>/records.jsonl, summary.json
    status    進み具合を出す
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import json
import os
import random
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WORKSPACE_ROOT = HERE.parents[2]

import anthropic_api  # noqa: E402
import openai_api  # noqa: E402
import prompts  # noqa: E402
import records as rec  # noqa: E402

JST = timezone(timedelta(hours=9))
EXTRACT_MODEL = "gpt-6-luna"
LEGEND_MODEL = "gpt-6.1-sol"
FALLBACK_MODEL = "claude-haiku-5-5"  # OpenAI のクレジットが切れたときの sync 用。比べた型で次に良かったもの
# 100 万トークンあたりの単価（2026-11-01 に公式の料金表で確かめた。一括処理は半額）
PRICES = {"gpt-6-luna": (0.10, 0.50), "gpt-6.1-sol": (2.00, 10.00), FALLBACK_MODEL: (0.10, 0.50)}
CHUNK_PAGES = 6
CHUNK_MAX_CHARS = 40000
PART_MAX_BYTES = 150 * 1024 * 1024
PART_MAX_LINES = 40000
MAX_DRAWINGS = 20000  # 図形がこれより多い頁は表を探さず文字だけにする（極端に遅くなる頁の用心）
OCR_SOURCE = "ocr:ndlocr-lite"
SLUG_RE = re.compile(r"[0-9]{5}-[a-z0-9-]+")
# PDF から取り出した文字に、対の無いサロゲート（U+D800〜U+DFFF）が混ざることがある。UTF-8 で書けないので置換文字にする。
# 2026-11-04 の升目づくり g5 は、これ 1 文書で 90,400 文書目に全体が止まった。
LONE_SURROGATE_RE = re.compile("[" + chr(0xD800) + "-" + chr(0xDFFF) + "]")
REPLACEMENT_CHAR = chr(0xFFFD)


def json_line(payload: object) -> str:
    """UTF-8 で必ず書ける JSON の 1 行にする。"""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return LONE_SURROGATE_RE.sub(REPLACEMENT_CHAR, text)


def now_text() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


def write_json(path: Path, payload: object, *, indent: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=indent)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def cost(model: str, usage: dict, *, batch: bool) -> float:
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)
    usd = tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out
    return usd / 2 if batch else usd


# ---------------------------------------------------------------- plan

def cmd_plan(args) -> int:
    done = set()
    if not args.include_extracted and args.extracted_root.is_dir():
        done = {p.parent.name for p in args.extracted_root.glob("*/records.jsonl") if p.stat().st_size > 0}
    wanted = set(args.slug or [])
    plan = {"created_at": now_text(), "municipalities": {}}
    total_docs = total_pages = 0
    for manifest_path in sorted(args.work_root.glob("*/manifest.json")):
        slug = manifest_path.parent.name
        if not SLUG_RE.fullmatch(slug) or slug in done or (wanted and slug not in wanted):
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        docs = []
        for doc_id, doc in (manifest.get("documents") or {}).items():
            if not isinstance(doc, dict) or doc.get("status") == "gone":
                continue
            if (doc.get("file_type") or "").lower() not in ("pdf", "html") or doc.get("text_status") != "ok":
                continue
            if doc.get("text_source") == OCR_SOURCE:
                continue
            # 調査から集めた文書（collect_from_survey.py）は、選んだ役割（origin の「:」の後）で決める。
            # 以前の収集の kind はリンクの言葉から付けたもので当てにならない
            origin = str(doc.get("origin") or "")
            if ":" in origin:
                if origin.rsplit(":", 1)[1] not in args.roles.split(","):
                    continue
            elif doc.get("kind") != "project_evaluation":
                continue
            relative = doc.get("file") or f"source/{doc_id}.pdf"
            if not (manifest_path.parent / relative).is_file():
                continue
            docs.append({"doc_id": doc_id, "file": relative, "pages": doc.get("pages"),
                         "fiscal_year": doc.get("fiscal_year"), "title": doc.get("title") or "",
                         "source_url": doc.get("source_url") or "", "page_url": doc.get("page_url") or ""})
        if not docs:
            continue
        docs.sort(key=lambda d: (-(d["fiscal_year"] or 0), d["doc_id"]))
        if args.limit_docs:
            docs = docs[:args.limit_docs]
        plan["municipalities"][slug] = {"code": str(manifest.get("jis_code") or ""), "name": str(manifest.get("name") or ""),
                                        "docs": docs}
        total_docs += len(docs)
        total_pages += sum(int(d["pages"] or 0) for d in docs)
    plan["totals"] = {"municipalities": len(plan["municipalities"]), "documents": total_docs, "pages": total_pages,
                      "excluded_extracted": len(done)}
    write_json(args.out / "plan.json", plan, indent=1)
    print(f"[HYOKA-AI] plan 自治体 {len(plan['municipalities'])} 文書 {total_docs} 頁 {total_pages}（構造化済み {len(done)} を除く）")
    return 0


def load_plan(args) -> dict:
    plan = read_json(args.out / "plan.json")
    if not plan:
        raise SystemExit("plan.json が無い。先に plan を流す")
    if args.slug:
        plan["municipalities"] = {s: m for s, m in plan["municipalities"].items() if s in set(args.slug)}
    return plan


# ---------------------------------------------------------------- grids

def inputs_path(out: Path, slug: str, doc_id: str) -> Path:
    return out / slug / "inputs" / f"{doc_id}.json.gz"


def layouts_path(inputs: Path) -> Path:
    """頁の表の構造（全欄の層。画面に全部出すもの）の置き場。升目の一覧の隣に置く。"""
    return inputs.parent.parent / "layouts" / inputs.name


def write_gzip_lines(path: Path, lines: list[dict]) -> None:
    """1 行 1 件で gzip にする。画面が大きな文書の 1 頁だけを読めるように、頁を行に分ける。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as stream:
        for line in lines:
            stream.write(json_line(line) + "\n")
    temporary.replace(path)


def read_gzip_lines(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def build_doc_grid(task: tuple[str, str, str]) -> tuple[str, str, int, str]:
    """1 文書の升目を作る（別の処理で動く）。思わぬ失敗も結果として返し、全体を止めない（書けなかった文書は次の回にやり直す）。"""
    try:
        return build_doc_grid_unchecked(task)
    except Exception as exc:
        return task[2], task[1], 0, f"書けなかった {type(exc).__name__}: {exc}"[:300]


def build_doc_grid_unchecked(task: tuple[str, str, str]) -> tuple[str, str, int, str]:
    """1 文書の升目の一覧と頁の表の構造を作って書く。HTML のシートは 1 頁として扱う。"""
    pdf_path, dest, doc_id = task
    import grid
    pages = []
    layouts = []
    error = ""
    if pdf_path.lower().endswith((".html", ".htm")):
        import html_layout
        try:
            layout = html_layout.html_layout(Path(pdf_path).read_bytes())
            layouts.append({"page": 1, **layout})
            text = grid.layout_text(layout)
            if text.strip():
                pages.append([1, text])
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:300]
        return write_doc_grid(dest, doc_id, pages, layouts, error)
    import fitz
    try:
        with fitz.open(pdf_path) as pdf:
            for index in range(len(pdf)):
                page = pdf[index]
                try:
                    plain = page.get_text("text")
                    if not plain.strip():
                        layouts.append({"page": index + 1, "image_only": True})  # 文字の無い頁（白紙・画像だけ）
                        continue
                    if len(page.get_drawings()) > MAX_DRAWINGS:
                        text = plain
                        layouts.append({"page": index + 1, "plain": plain})
                    else:
                        layout = grid.page_layout(page)
                        text = grid.layout_text(layout)
                        layouts.append({"page": index + 1, **layout})
                except Exception as exc:  # 頁ごとの失敗は書き残して次へ
                    text = f"## この頁は升目にできなかった（{type(exc).__name__}）\n" + page.get_text("text")
                    layouts.append({"page": index + 1, "plain": page.get_text("text")})
                if text.strip():
                    pages.append([index + 1, text])
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:300]
    return write_doc_grid(dest, doc_id, pages, layouts, error)


def write_doc_grid(dest: str, doc_id: str, pages: list, layouts: list, error: str) -> tuple[str, str, int, str]:
    payload = {"doc_id": doc_id, "grid_version": rec.GRID_VERSION, "pages": pages, "error": error}
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as stream:
        stream.write(json_line(payload))
    temporary.replace(path)
    head = {"doc_id": doc_id, "grid_version": rec.GRID_VERSION, "pages": [p["page"] for p in layouts], "error": error}
    write_gzip_lines(layouts_path(path), [head, *layouts])
    return doc_id, dest, len(pages), error


def cmd_grids(args) -> int:
    plan = load_plan(args)
    tasks = []
    for slug, muni in plan["municipalities"].items():
        for doc in muni["docs"]:
            dest = inputs_path(args.out, slug, doc["doc_id"])
            if not layouts_path(dest).is_file():  # 前の版の升目の一覧には表の構造が無いので作り直す
                tasks.append((str(args.work_root / slug / doc["file"]), str(dest), doc["doc_id"]))
    random.Random(0).shuffle(tasks)  # 大きな文書が固まらないように
    print(f"[HYOKA-AI] grids 残り {len(tasks)} 文書、処理 {args.workers}", flush=True)
    started = time.time()
    done = errors = pages = 0
    # 処理は 50 文書ごとに入れ替える（PyMuPDF のメモリを持ち越さない）。ProcessPoolExecutor の max_tasks_per_child は
    # Python 3.12 で入れ替えが起きずに止まることがあった（10 並列で 500 文書ちょうどで止まった）ので multiprocessing.Pool を使う。
    import multiprocessing
    context = multiprocessing.get_context("spawn" if os.name == "nt" else "fork")
    with context.Pool(processes=args.workers, maxtasksperchild=50) as pool:
        for doc_id, _dest, n, error in pool.imap_unordered(build_doc_grid, tasks, chunksize=1):
            done += 1
            pages += n
            errors += bool(error)
            if error:
                print(f"[HYOKA-AI] grids 失敗 {doc_id}: {error}", flush=True)
            if done % 200 == 0 or done == len(tasks):
                rate = done / max(time.time() - started, 1)
                print(f"[HYOKA-AI] grids {done}/{len(tasks)} 頁 {pages} 失敗 {errors} "
                      f"{rate * 60:.0f} 文書/分 残り約 {(len(tasks) - done) / max(rate, 1e-9) / 3600:.1f} 時間", flush=True)
    return 0


def load_inputs(out: Path, slug: str, doc_id: str) -> dict | None:
    path = inputs_path(out, slug, doc_id)
    if not path.is_file():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def chunk_pages(pages: list) -> list[dict]:
    """頁を 6 頁ずつ（文字が多ければもっと少なく）に分ける。"""
    chunks, current, size = [], [], 0
    for page_no, text in pages:
        if current and (len(current) >= CHUNK_PAGES or size + len(text) > CHUNK_MAX_CHARS):
            chunks.append(current)
            current, size = [], 0
        current.append((page_no, text[:CHUNK_MAX_CHARS]))
        size += min(len(text), CHUNK_MAX_CHARS)
    if current:
        chunks.append(current)
    return [{"index": i, "pages": [p for p, _ in part], "text": "\n\n".join(f"=== 頁 {p} ===\n{t}" for p, t in part)}
            for i, part in enumerate(chunks)]


# ---------------------------------------------------------------- legends

def doc_chunks(out: Path, slug: str, doc: dict) -> list[dict]:
    inputs = load_inputs(out, slug, doc["doc_id"])
    return chunk_pages(inputs["pages"]) if inputs else []


def legend_source_text(out: Path, slug: str, doc: dict) -> tuple[str, int | None]:
    """見本の文字（記録らしい頁 3 頁と、方向性の欄のある頁）と、画像にする頁。"""
    inputs = load_inputs(out, slug, doc["doc_id"]) or {"pages": []}
    pages = {p: t for p, t in inputs["pages"]}
    picked = sorted(pages, key=lambda p: -len(pages[p]))[:3]
    with_direction = [p for p in pages if re.search(r"方向|方針", pages[p])]
    if with_direction and not set(picked) & set(with_direction):
        picked.append(with_direction[0])
    text = "\n\n".join(f"=== 頁 {p} ===\n{pages[p]}" for p in sorted(picked))[:12000]
    image_page = next((p for p in sorted(picked, key=lambda p: -len(pages[p])) if p in with_direction), picked[0] if picked else None)
    return text, image_page


def page_image(pdf_path: Path, page_no: int, width: int = 1000) -> str:
    import base64
    import fitz
    with fitz.open(pdf_path) as pdf:
        page = pdf[page_no - 1]
        zoom = width / max(page.rect.width, 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode("ascii")


def ab_metrics(records: list[dict], options: list[str]) -> dict:
    dirs = [r["fields"]["future_direction"]["raw_text"] for r in records
            if (r["fields"].get("future_direction") or {}).get("status") == "present"]
    # 方向性の欄名が評価の欄で文中の記載にした分も、埋まった数に入れる
    dirs += [f["content"]["raw_text"] for r in records for f in r["raw_fields"] if f.get("standard_key") == "future_direction"]
    values = sum(len(r["fields"]) + len(r["raw_fields"]) for r in records)
    unverified = sum(len(r["unverified"]) for r in records)
    norm_opts = [rec.norm(o) for o in options if rec.norm(o)]
    return {"records": len(records), "directions": len(dirs),
            "fill": len(dirs) / len(records) if records else 0.0,
            "kinds": len({rec.norm(d)[:40] for d in dirs}),
            "verify": values / (values + unverified) if values + unverified else 1.0,
            "options": (sum(1 for d in dirs if any(o == rec.norm(d) or o in rec.norm(d) for o in norm_opts)) / len(dirs))
            if dirs and norm_opts else None}


def accept_legend(v2: dict, v3: dict) -> tuple[bool, str]:
    """凡例を使うか。方向性の埋まり方・原典照合・事業の数が下がらず、書き方が散らばらないときだけ使う。"""
    if v2["records"] == 0 and v3["records"] == 0:
        return False, "比べる記録が無い"
    if v3["records"] < 0.9 * v2["records"]:
        return False, "事業の数が減った"
    if v3["fill"] < v2["fill"] - 0.05:
        return False, "方向性の埋まり方が下がった"
    if v3["verify"] < v2["verify"] - 0.01:
        return False, "原典との照合が下がった"
    improved_options = v3["options"] is not None and v2["options"] is not None and v3["options"] > v2["options"]
    if v3["kinds"] > v2["kinds"] and not improved_options:
        return False, "方向性の書き方が散らばった"
    return True, "悪化なし"


def is_claude(model: str) -> bool:
    return model.startswith("claude-")


def extract_sync(key: str, system: str, chunk: dict, model: str = EXTRACT_MODEL) -> dict:
    result = {"index": chunk["index"], "pages": chunk["pages"], "model": model}
    try:
        if is_claude(model):
            raw, usage, finish = anthropic_api.chat(key, model, system, chunk["text"], prompts.SCHEMA)
        else:
            body = openai_api.chat_body(model, system, chunk["text"], prompts.SCHEMA, "hyoka_records")
            raw, usage, finish = openai_api.chat(key, body)
        result.update(usage=usage, finish=finish, usd=cost(model, usage, batch=False))
        result["parsed"] = json.loads(raw)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:500]
    return result


def make_legend(args, key: str, slug: str, muni: dict) -> dict:
    path = args.out / slug / "legend.json"
    existing = read_json(path)
    if existing:
        return existing
    docs = [d for d in muni["docs"] if inputs_path(args.out, slug, d["doc_id"]).is_file()]
    if len(docs) < 2:
        result = {"slug": slug, "accepted": False, "reason": "文書が 2 本未満で比べられない", "usd": 0.0, "created_at": now_text()}
        write_json(path, result, indent=1)
        return result
    sources = [docs[0], docs[-1]] if len(docs) >= 4 else [docs[0]]
    rest = [d for d in docs if d not in sources]
    tests = [rest[int(i * len(rest) / 3)] for i in range(min(3, len(rest)))] if rest else []
    content = []
    for doc in sources:
        text, image_page = legend_source_text(args.out, slug, doc)
        content.append({"type": "text", "text": f"### 見本 {doc['fiscal_year'] or '年度不明'}年度（{doc.get('title') or ''}）\n{text}"})
        if image_page:
            content.append({"type": "image_url", "image_url": {"url": page_image(args.work_root / slug / doc["file"], image_page)}})
    body = openai_api.chat_body(LEGEND_MODEL, prompts.LEGEND_SYSTEM, content, prompts.LEGEND_SCHEMA, "legend",
                                reasoning_effort="medium")
    usd = 0.0
    try:
        raw, usage, _finish = openai_api.chat(key, body)
        usd += cost(LEGEND_MODEL, usage, batch=False)
        legend = json.loads(raw)
    except Exception as exc:
        result = {"slug": slug, "accepted": False, "reason": f"凡例を作れなかった: {exc}"[:300], "usd": usd, "created_at": now_text()}
        write_json(path, result, indent=1)
        return result
    # 凡例あり・なしで、見本以外の文書（最大 3 本・各文書で文字の最も多い区切り。最初の区切りは表紙や目次のことが多い）を読ませて比べる
    metrics = {}
    for variant, system in (("v2", prompts.SYSTEM), ("v3", prompts.system_with_legend(legend))):
        records = []
        for doc in tests:
            chunks = sorted(doc_chunks(args.out, slug, doc), key=lambda c: -len(c["text"]))[:1]
            results = [extract_sync(key, system, c) for c in chunks]
            usd += sum(r.get("usd", 0.0) for r in results)
            for r, c in zip(results, chunks):
                r["text"] = c["text"]
            got, _summary = rec.assemble_document({"slug": slug, "doc_id": doc["doc_id"]}, results, {})
            records += got
        metrics[variant] = ab_metrics(records, legend.get("direction_options") or [])
    accepted, reason = accept_legend(metrics["v2"], metrics["v3"])
    result = {"slug": slug, "legend_version": prompts.LEGEND_VERSION, "model": LEGEND_MODEL, **legend,
              "sources": [d["doc_id"] for d in sources], "tests": [d["doc_id"] for d in tests],
              "metrics": metrics, "accepted": accepted, "reason": reason, "usd": usd, "created_at": now_text()}
    write_json(path, result, indent=1)
    return result


def cmd_legends(args) -> int:
    plan = load_plan(args)
    key = openai_api.load_key(args.config)
    slugs = list(plan["municipalities"])
    print(f"[HYOKA-AI] legends {len(slugs)} 自治体", flush=True)
    accepted = total = 0
    spent = 0.0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(make_legend, args, key, slug, plan["municipalities"][slug]): slug for slug in slugs}
        for future in cf.as_completed(futures):
            slug = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                print(f"[HYOKA-AI] legend {slug} 失敗 {exc}", flush=True)
                continue
            total += 1
            accepted += bool(result.get("accepted"))
            spent += float(result.get("usd") or 0)
            print(f"[HYOKA-AI] legend {slug} {'使う' if result.get('accepted') else '使わない'}（{result.get('reason')}）", flush=True)
    print(f"[HYOKA-AI] legends 済み {total} 使う {accepted} 今回の費用 ${spent:.3f}", flush=True)
    return 0


def system_for(out: Path, slug: str) -> tuple[str, str | None]:
    legend = read_json(out / slug / "legend.json") or {}
    if legend.get("accepted"):
        return prompts.system_with_legend(legend), legend.get("legend_version")
    return prompts.SYSTEM, None


# ---------------------------------------------------------------- requests / submit / collect

def results_path(out: Path, slug: str, doc_id: str) -> Path:
    return out / slug / "results" / f"{doc_id}.json"


def cmd_requests(args) -> int:
    plan = load_plan(args)
    req_dir = args.out / "requests"
    batches = read_json(args.out / "batches.json", {"batches": []})
    pending = set()
    for batch in batches["batches"]:
        if batch.get("status") not in ("failed", "expired", "cancelled", "collected"):
            pending.update(batch.get("docs") or [])
    existing = sorted(req_dir.glob("part-*.jsonl"))
    number = len(existing) + 1
    part = None
    part_docs: list[str] = []
    size = lines = 0
    written_parts = []

    def close_part():
        nonlocal part
        if part is not None:
            part.close()
            write_json(Path(part.name).with_suffix(".docs.json"), part_docs)
            written_parts.append(part.name)
            part = None

    queued = 0
    for slug, muni in plan["municipalities"].items():
        system, legend_version = system_for(args.out, slug)
        for doc in muni["docs"]:
            doc_key = f"{slug}/{doc['doc_id']}"
            if doc_key in pending or results_path(args.out, slug, doc["doc_id"]).is_file():
                continue
            chunks = doc_chunks(args.out, slug, doc)
            if not chunks:
                continue
            payload = []
            for chunk in chunks:
                body = openai_api.chat_body(EXTRACT_MODEL, system, chunk["text"], prompts.SCHEMA, "hyoka_records")
                payload.append(json.dumps({"custom_id": f"{doc_key}/{chunk['index']}", "method": "POST",
                                           "url": "/v1/chat/completions", "body": body}, ensure_ascii=False))
            blob = ("\n".join(payload) + "\n").encode("utf-8")
            if part is None or size + len(blob) > PART_MAX_BYTES or lines + len(payload) > PART_MAX_LINES:
                close_part()
                req_dir.mkdir(parents=True, exist_ok=True)
                part = open(req_dir / f"part-{number:04d}.jsonl", "wb")
                number += 1
                part_docs, size, lines = [], 0, 0
            part.write(blob)
            part_docs.append(doc_key)
            size += len(blob)
            lines += len(payload)
            queued += len(payload)
            # 文書ごとの区切りと凡例の版を残す（受け取りで使う）
            write_json(args.out / slug / "chunks" / f"{doc['doc_id']}.json",
                       {"chunks": [{"index": c["index"], "pages": c["pages"]} for c in chunks],
                        "prompt_version": prompts.PROMPT_VERSION, "legend_version": legend_version})
    close_part()
    print(f"[HYOKA-AI] requests 要求 {queued} 件、ファイル {len(written_parts)}", flush=True)
    return 0


def cmd_submit(args) -> int:
    key = openai_api.load_key(args.config)
    path = args.out / "batches.json"
    batches = read_json(path, {"batches": []})
    sent = {b["part"] for b in batches["batches"]}
    parts = [p for p in sorted((args.out / "requests").glob("part-*.jsonl")) if p.name not in sent]
    if args.max_parts:
        parts = parts[:args.max_parts]
    for part in parts:
        docs = read_json(part.with_suffix(".docs.json"), [])
        file_id = openai_api.upload_batch_file(key, part)
        batch = openai_api.create_batch(key, file_id, {"part": part.name, "task": "hyoka-ai"})
        batches["batches"].append({"part": part.name, "file_id": file_id, "batch_id": batch["id"], "status": batch.get("status"),
                                   "docs": docs, "submitted_at": now_text()})
        write_json(path, batches, indent=1)
        print(f"[HYOKA-AI] submit {part.name} → {batch['id']}（{len(docs)} 文書）", flush=True)
    return 0


def save_result(out: Path, custom_id: str, result: dict, store: dict) -> None:
    slug, doc_id, index = custom_id.split("/")
    store.setdefault((slug, doc_id), {})[int(index)] = result


def flush_results(out: Path, store: dict) -> None:
    for (slug, doc_id), chunks in store.items():
        path = results_path(out, slug, doc_id)
        existing = read_json(path, {"doc_id": doc_id, "chunks": {}})
        for index, result in chunks.items():
            existing["chunks"][str(index)] = result
        write_json(path, existing)


def parse_batch_line(line: str) -> tuple[str, dict]:
    item = json.loads(line)
    result: dict = {}
    response = item.get("response") or {}
    if item.get("error") or response.get("status_code") != 200:
        result["error"] = json.dumps(item.get("error") or response.get("body"), ensure_ascii=False)[:500]
        return item["custom_id"], result
    body = response.get("body") or {}
    usage = body.get("usage") or {}
    result.update(usage=usage, usd=cost(EXTRACT_MODEL, usage, batch=True))
    choice = (body.get("choices") or [{}])[0]
    result["finish"] = choice.get("finish_reason")
    try:
        result["parsed"] = json.loads((choice.get("message") or {}).get("content") or "")
    except json.JSONDecodeError as exc:
        result["error"] = f"答えが JSON でない: {exc}"
    return item["custom_id"], result


def cmd_collect(args) -> int:
    key = openai_api.load_key(args.config)
    path = args.out / "batches.json"
    batches = read_json(path, {"batches": []})
    counts: dict[str, int] = {}
    for batch in batches["batches"]:
        if batch.get("status") == "collected":
            counts["collected"] = counts.get("collected", 0) + 1
            continue
        info = openai_api.get_batch(key, batch["batch_id"])
        batch["status"] = info.get("status")
        batch["request_counts"] = info.get("request_counts")
        # 期限切れ・取り消し・失敗でも、終わった分は出力にある（残りは sync でやり直す）
        if info.get("status") in ("completed", "expired", "cancelled", "failed"):
            store: dict = {}
            for file_key in ("output_file_id", "error_file_id"):
                file_id = info.get(file_key)
                if not file_id:
                    continue
                dest = args.out / "batch_results" / f"{batch['batch_id']}.{file_key.split('_')[0]}.jsonl"
                if not dest.is_file():
                    openai_api.download_file(key, file_id, dest)
                with dest.open(encoding="utf-8") as stream:
                    for line in stream:
                        if line.strip():
                            custom_id, result = parse_batch_line(line)
                            save_result(args.out, custom_id, result, store)
            flush_results(args.out, store)
            batch["final_status"] = info.get("status")
            batch["status"] = "collected"
            batch["collected_at"] = now_text()
        counts[batch["status"]] = counts.get(batch["status"], 0) + 1
        write_json(path, batches, indent=1)
    print(f"[HYOKA-AI] collect {counts}", flush=True)
    return 0


def cmd_sync(args) -> int:
    """一括処理を使わずに送る。結果の形は collect と同じ。失敗した区切りだけのやり直しにも使う。"""
    plan = load_plan(args)
    key = (anthropic_api if is_claude(args.model) else openai_api).load_key(args.config)
    jobs = []
    for slug, muni in plan["municipalities"].items():
        system, legend_version = system_for(args.out, slug)
        for doc in muni["docs"]:
            done = read_json(results_path(args.out, slug, doc["doc_id"]), {"chunks": {}})["chunks"]
            chunks = doc_chunks(args.out, slug, doc)
            if not chunks:
                continue
            write_json(args.out / slug / "chunks" / f"{doc['doc_id']}.json",
                       {"chunks": [{"index": c["index"], "pages": c["pages"]} for c in chunks],
                        "prompt_version": prompts.PROMPT_VERSION, "legend_version": legend_version})
            for chunk in chunks:
                previous = done.get(str(chunk["index"]))
                if previous and "parsed" in previous:
                    continue
                jobs.append((slug, doc["doc_id"], system, chunk))
    print(f"[HYOKA-AI] sync {len(jobs)} 件（{args.model}）", flush=True)
    spent = 0.0
    store: dict = {}
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(extract_sync, key, system, chunk, args.model): (slug, doc_id) for slug, doc_id, system, chunk in jobs}
        for n, future in enumerate(cf.as_completed(futures), 1):
            slug, doc_id = futures[future]
            result = future.result()
            spent += result.get("usd", 0.0)
            store.setdefault((slug, doc_id), {})[result["index"]] = {k: v for k, v in result.items() if k not in ("index", "pages")}
            if n % 200 == 0:
                flush_results(args.out, store)
                store = {}
                print(f"[HYOKA-AI] sync {n}/{len(jobs)} ${spent:.3f}", flush=True)
    flush_results(args.out, store)
    print(f"[HYOKA-AI] sync 済み {len(jobs)} 件 ${spent:.3f}", flush=True)
    return 0


# ---------------------------------------------------------------- assemble / status

def cmd_assemble(args) -> int:
    plan = load_plan(args)
    totals = {"municipalities": 0, "documents": 0, "records": 0, "chunks": 0, "errors": 0, "unverified_values": 0,
              "tokens_in": 0, "tokens_out": 0, "usd": 0.0, "legends_used": 0}
    for slug, muni in plan["municipalities"].items():
        legend = read_json(args.out / slug / "legend.json") or {}
        totals["usd"] += float(legend.get("usd") or 0)
        all_records, summaries = [], []
        for doc in muni["docs"]:
            results = read_json(results_path(args.out, slug, doc["doc_id"]))
            chunk_info = read_json(args.out / slug / "chunks" / f"{doc['doc_id']}.json")
            if not results or not chunk_info:
                continue
            chunks = doc_chunks(args.out, slug, doc)
            texts = {c["index"]: c["text"] for c in chunks}
            merged = []
            for info in chunk_info["chunks"]:
                result = results["chunks"].get(str(info["index"])) or {"error": "結果が無い"}
                usage = result.get("usage") or {}
                totals["tokens_in"] += int(usage.get("prompt_tokens") or 0)
                totals["tokens_out"] += int(usage.get("completion_tokens") or 0)
                totals["usd"] += float(result.get("usd") or 0)
                merged.append({**result, "index": info["index"], "pages": info["pages"], "text": texts.get(info["index"], "")})
            meta = {"slug": slug, "code": muni["code"], "name": muni["name"], "doc_id": doc["doc_id"],
                    "source_url": doc["source_url"], "page_url": doc["page_url"]}
            extraction = {"model": EXTRACT_MODEL, "prompt_version": chunk_info.get("prompt_version"),
                          "legend_version": chunk_info.get("legend_version")}
            got, summary = rec.assemble_document(meta, merged, extraction)
            all_records += got
            summaries.append(summary)
            for key in ("chunks", "errors", "unverified_values"):
                totals[key] += summary[key]
        if not summaries:
            continue
        lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in all_records)
        target = args.out / slug / "records.jsonl"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(lines, encoding="utf-8")
        temporary.replace(target)
        write_json(args.out / slug / "documents.json", {"slug": slug, "generated_at": now_text(), "documents": summaries})
        totals["municipalities"] += 1
        totals["documents"] += len(summaries)
        totals["records"] += len(all_records)
        totals["legends_used"] += bool(legend.get("accepted"))
    totals["usd"] = round(totals["usd"], 3)
    write_json(args.out / "summary.json", {"generated_at": now_text(), "totals": totals}, indent=1)
    print(f"[HYOKA-AI] assemble {totals}", flush=True)
    return 0


def cmd_status(args) -> int:
    plan = read_json(args.out / "plan.json") or {"municipalities": {}}
    docs = [(s, d["doc_id"]) for s, m in plan["municipalities"].items() for d in m["docs"]]
    grids = sum(1 for s, d in docs if inputs_path(args.out, s, d).is_file())
    results = sum(1 for s, d in docs if results_path(args.out, s, d).is_file())
    legends = [read_json(args.out / s / "legend.json") for s in plan["municipalities"]]
    legends = [x for x in legends if x]
    batches = read_json(args.out / "batches.json", {"batches": []})["batches"]
    by_status: dict[str, int] = {}
    for batch in batches:
        by_status[batch.get("status") or "?"] = by_status.get(batch.get("status") or "?", 0) + 1
    print(f"[HYOKA-AI] status 自治体 {len(plan['municipalities'])} 文書 {len(docs)} 升目 {grids} 結果 {results} "
          f"凡例 {len(legends)}（使う {sum(1 for x in legends if x.get('accepted'))}） 一括処理 {by_status}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="事務事業評価を AI で書き写す")
    parser.add_argument("command", choices=["plan", "grids", "legends", "requests", "submit", "collect", "sync", "assemble", "status"])
    parser.add_argument("--work-root", type=Path, default=WORKSPACE_ROOT / "work" / "hyoka")
    parser.add_argument("--extracted-root", type=Path, default=WORKSPACE_ROOT / "work" / "hyoka-extracted")
    parser.add_argument("--out", type=Path, default=WORKSPACE_ROOT / "work" / "hyoka-ai")
    parser.add_argument("--config", type=Path, default=WORKSPACE_ROOT / "data" / "config.json")
    parser.add_argument("--slug", action="append", help="この自治体だけ（複数可）")
    parser.add_argument("--limit-docs", type=int, default=0, help="plan: 1 自治体あたりの文書の数の上限（試し用）")
    parser.add_argument("--include-extracted", action="store_true", help="plan: 構造化済みの自治体も入れる")
    parser.add_argument("--roles", default="sheet,report,external", help="plan: 調査から集めた文書のうち対象にする役割")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-parts", type=int, default=0, help="submit: 送る要求ファイルの数の上限")
    parser.add_argument("--model", default=EXTRACT_MODEL, choices=[EXTRACT_MODEL, FALLBACK_MODEL], help="sync: 書き写しに使う型")
    args = parser.parse_args(argv)
    if args.out.resolve() == args.work_root.resolve() or args.work_root.resolve() in args.out.resolve().parents:
        parser.error("--out を --work-root の中に置かない")
    commands = {"plan": cmd_plan, "grids": cmd_grids, "legends": cmd_legends, "requests": cmd_requests,
                "submit": cmd_submit, "collect": cmd_collect, "sync": cmd_sync, "assemble": cmd_assemble, "status": cmd_status}
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
