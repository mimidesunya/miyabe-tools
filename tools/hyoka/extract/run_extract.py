#!/usr/bin/env python3
"""取得済みの work/hyoka/<slug> 又は平らな試行フォルダから評価記録を作る。

python tools/hyoka/extract/run_extract.py --work-dir work/hyoka --all --out work/hyoka-extracted
同じ入力・様式・処理版なら文書単位で再利用。--force で再抽出する。ネットワークは使わない。
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))

from tools.hyoka.extract.engine import REQUIRED, TEMPLATE_DIR, extract_pdf, load_templates
from tools.hyoka.extract.readers import Source, ReadError, archive_sources
from tools.hyoka.extract.validation import SCHEMA_PATH, check_schema_keywords


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    os.replace(temp,path)


def input_manifests(root):
    if (root/"manifest.json").is_file() or (root/"manifest_subset.json").is_file():
        directories=[root]
    else:
        directories=sorted(p for p in root.iterdir() if p.is_dir())
    for directory in directories:
        manifest=directory/"manifest.json"
        if not manifest.exists():manifest=directory/"manifest_subset.json"
        if manifest.is_file():yield directory,manifest


def source_path(directory,doc_id,metadata):
    if not re_safe_id(doc_id): raise ReadError("unsafe_document_id")
    name=metadata.get("file") or "source/"+doc_id+"."+metadata.get("file_type", "pdf")
    original=(directory/name).resolve()
    if not original.is_relative_to(directory.resolve()): raise ReadError("unsafe_source_path")
    if original.is_file():return original
    # 試行資料は source/ と text/ のファイルを同じディレクトリに置く。
    flat=directory/Path(name).name
    if flat.is_file():return flat
    raise ReadError("source_missing")


def re_safe_id(value):
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+",value))


def engine_digest():
    h=sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        h.update(p.name.encode());h.update(p.read_bytes())
    h.update(SCHEMA_PATH.read_bytes())
    return h.hexdigest()


def process_source(source,municipality,templates,out_dir):
    entry={"doc_id":source.doc_id,"sha256":source.digest,"file_type":source.file_type,
           "source_url":source.metadata["source_url"],"page_url":source.metadata["page_url"]}
    if source.parent_doc_id:
        entry.update(parent_doc_id=source.parent_doc_id,archive_member=source.member)
    documents=[entry]
    if source.file_type=="zip":
        combined={"records":[],"failures":[],"warnings":[],"recognized":[],"excluded_pages":[],"selection_checks":[],"template_records":{},"pages":0}
        counts=Counter()
        for child in archive_sources(source):
            if child.file_type=="zip":
                result={"records":[],"failures":[{"reason":"nested_archive_unsupported"}],"warnings":[],"recognized":[],"pages":0,"template_records":{}}
                children=[]
            else:
                result,children=process_source(child,municipality,templates,out_dir)
            documents.extend(children)
            combined["records"].extend(result["records"])
            for key in ("failures","warnings","recognized","excluded_pages","selection_checks"):
                combined[key].extend({"doc_id":child.doc_id,**r} for r in result.get(key,[]))
            combined["pages"]+=result["pages"]
            counts.update(result["template_records"])
        combined["template_records"]=dict(counts)
        if len(documents)==1:combined["failures"].append({"reason":"archive_no_supported_members"})
        return combined,documents
    if source.file_type=="pdf":
        return extract_pdf(source,municipality,templates),documents
    if source.file_type in {"xlsx","xlsm"}:
        from tools.hyoka.extract.xlsx_extract import extract_xlsx
        result=extract_xlsx(source,municipality,templates,out_dir)
        documents.extend(result.pop("rendered_documents",[]))
        return result,documents
    return {"records":[],"failures":[{"reason":"unsupported_file_type","file_type":source.file_type}],
            "warnings":[],"recognized":[],"pages":0,"template_records":{}},documents


def run_municipality(directory,manifest_path,definition,out,force=False,limit=None):
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    slug=manifest.get("slug",directory.name)
    if not re_safe_id(slug): raise ReadError("unsafe_slug")
    municipality={"slug":slug,"code":slug[:5],"name":manifest.get("name") or (definition or {}).get("municipality",{}).get("name",slug),
                  "identity_source":"document_manifest"}
    out_dir=out/slug;out_dir.mkdir(parents=True,exist_ok=True)
    definition=definition or {"templates":[]}
    revision=sha256((engine_digest()+json.dumps(definition,ensure_ascii=False,sort_keys=True)).encode()).hexdigest()
    summaries=[]; all_records=[]; all_sources=[]
    documents=manifest["documents"]
    if isinstance(documents,list):documents={d["doc_id"]:d for d in documents}
    for index,(doc_id,metadata) in enumerate(sorted(documents.items())):
        if limit is not None and index>=limit: break
        cache_path=out_dir/"documents"/(doc_id+".json")
        try:
            path=source_path(directory,doc_id,metadata)
            data=path.read_bytes(); digest=sha256(data).hexdigest()
            fingerprint=sha256((revision+digest+json.dumps(metadata,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()
            cached=None
            if cache_path.exists() and not force:
                try:cached=json.loads(cache_path.read_text(encoding="utf-8"))
                except (ValueError,OSError):pass
            rendered_ok=not cached or all((out_dir/d["file"]).is_file() for d in cached.get("source_documents",[]) if d.get("derived_from"))
            if cached and cached.get("fingerprint")==fingerprint and rendered_ok:
                result=cached
            else:
                if metadata.get("sha256") and metadata["sha256"] != digest:raise ReadError("sha256_mismatch")
                source=Source(doc_id,data,metadata.get("file_type",path.suffix.lstrip(".")),metadata)
                result,source_docs=process_source(source,municipality,definition["templates"],out_dir)
                result.update(doc_id=doc_id,fingerprint=fingerprint,source_documents=source_docs)
                write_json(cache_path,result)
            all_records.extend(result["records"]);all_sources.extend(result["source_documents"])
            reasons=Counter(f["reason"] for f in result["failures"])
            summary={"doc_id":doc_id,"kind":metadata.get("kind"),"pages":result["pages"],
                     "recognized":bool(result["recognized"]),"templates":sorted({x["template"] for x in result["recognized"]}),
                     "records":len(result["records"]),"failures":dict(reasons),"warnings":len(result["warnings"]),
                     "excluded_pages":len(result.get('excluded_pages',[])),
                     "selection_checks":len(result.get('selection_checks',[])),
                     "detail_file":"documents/"+doc_id+".json"}
        except Exception as exc:
            # 文書間を分離し、壊れた一文書で他の自治体を巻き戻さない。
            # 開発時の例外も成功扱いにせず、型と理由を報告する。
            summary={"doc_id":doc_id,"kind":metadata.get("kind"),"pages":0,"recognized":False,"templates":[],
                     "records":0,"failures":{"document_error":1},"warnings":0,
                     "error":type(exc).__name__+":"+str(exc)}
        summaries.append(summary)
        if (index+1)%10==0: print(f"{slug}: {index+1}/{len(documents)} documents, {len(all_records)} records",flush=True)
        # 中断時にも完了した文書の位置を確認できる。
        write_json(out_dir/"progress.json",{"completed_documents":len(summaries),"last_doc_id":doc_id,"records":len(all_records)})
    statuses={name:dict(Counter(r["fields"][name]["status"] for r in all_records)) for name in REQUIRED}
    field_kinds={name:dict(Counter(r["fields"][name]["field_kind"] for r in all_records)) for name in REQUIRED}
    counts=Counter()
    for summary in summaries:counts.update(summary["failures"])
    report={"schema_version":"1.2.0","extractor_version":"0.1.0","municipality":municipality,
            "documents":summaries,"document_count":len(summaries),
            "recognized_documents":sum(s["recognized"] for s in summaries),
            "documents_with_records":sum(s["records"]>0 for s in summaries),
            "records":len(all_records),"field_status_counts":statuses,"field_kind_counts":field_kinds,
            "failure_counts":dict(counts),"warning_count":sum(s["warnings"] for s in summaries),
            "excluded_pages":sum(s.get('excluded_pages',0) for s in summaries),
            "selection_checks":sum(s.get('selection_checks',0) for s in summaries),
            "source_mismatch_count":sum("source_mismatch" in f.get("detail","") for s in summaries if "detail_file" in s
                                        for f in json.loads((out_dir/s["detail_file"]).read_text(encoding="utf-8"))["failures"]),
            "revision":revision,"limited":limit is not None}
    records_path=out_dir/"records.jsonl";tmp=records_path.with_suffix(".jsonl.tmp")
    with tmp.open("w",encoding="utf-8",newline="\n") as fh:
        for record in all_records:fh.write(json.dumps(record,ensure_ascii=False,separators=(",",":"))+"\n")
    os.replace(tmp,records_path)
    write_json(out_dir/"source_documents.json",all_sources)
    write_json(out_dir/"extract_report.json",report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir",type=Path,required=True)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--code")
    group.add_argument("--all",action="store_true")
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--templates",type=Path,default=TEMPLATE_DIR)
    parser.add_argument("--force",action="store_true")
    parser.add_argument("--limit-docs",type=int,help="開発用。限定実行であることを報告に明記する。")
    args=parser.parse_args(argv)
    root=args.work_dir.resolve();out=args.out.resolve()
    if out==root or out.is_relative_to(root) or root.is_relative_to(out):
        parser.error("--out は入力と包含関係のない別ディレクトリにしてください")
    definitions=load_templates(args.templates)
    check_schema_keywords(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    reports=[]
    for directory,manifest in input_manifests(root):
        code=directory.name[:5]
        if args.code and code!=args.code:continue
        report=run_municipality(directory,manifest,definitions.get(code),out,args.force,args.limit_docs)
        reports.append({k:v for k,v in report.items() if k!="documents"})
        write_json(out/"pilot_summary.json",reports)
        print(f"{directory.name}: done, {report['records']} records",flush=True)
    if not reports:parser.error("対象の manifest がありません")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
