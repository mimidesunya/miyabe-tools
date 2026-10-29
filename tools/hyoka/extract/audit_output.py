#!/usr/bin/env python3
"""保存した記録を原典から再照合する。出力や原典は変更せず監査結果だけ保存する。"""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))

from tools.hyoka.extract.engine import year_value
from tools.hyoka.extract.readers import Source, PDFPage, archive_sources, normalize, pdf_document, read_xlsx
from tools.hyoka.extract.run_extract import input_manifests, source_path, write_json
from tools.hyoka.extract.validation import validate


def all_evidence(record):
    yield from record['record_evidence']
    for field in list(record['fields'].values())+[f['content'] for f in record['raw_fields']]:
        yield from field['evidence']


def check_field_values(record):
    """引用だけ正しく値を改変した記録も拒否する。"""
    for name,field in list(record['fields'].items())+[(f['field_id'],f['content']) for f in record['raw_fields']]:
        if field.get('raw_text') and not any(normalize(field['raw_text']) in normalize(e['quote']) for e in field['evidence']):
            raise ValueError('raw_text_without_evidence:'+name)
        if field.get('raw_label') and not any(normalize(field['raw_label']) in normalize(e['quote']) for e in field['evidence']):
            raise ValueError('raw_label_without_evidence:'+name)
        if field['status']!='present':continue
        value=field['value']
        if name in {'evaluation_year','target_year'}:
            if value!=year_value(field['raw_text']):raise ValueError('year_value_mismatch:'+name)
        elif isinstance(value,str):
            if value!=field['raw_text']:raise ValueError('value_changed:'+name)
        elif isinstance(value,dict) and 'raw_term' in value:
            if value['raw_term']!=field['raw_text']:raise ValueError('raw_term_changed:'+name)


def check_source_records(source,records,selections=()):
    checked=0
    def check(e,text):
        nonlocal checked
        if e['sha256']!=source.digest:raise ValueError('sha256_mismatch')
        for key in ('source_url','page_url'):
            if e[key]!=source.metadata[key]:raise ValueError(key+'_mismatch')
        if not normalize(e['quote']) or normalize(e['quote']) not in normalize(text):
            raise ValueError('source_mismatch')
        checked+=1
    if source.file_type=='pdf':
        by_page=defaultdict(list)
        for record in records:
            for e in all_evidence(record):
                if e['doc_id']!=source.doc_id:raise ValueError('unexpected_evidence_document')
                by_page[e['locator']['page']].append(e)
        with pdf_document(source.data) as pdf:
            for number,entries in sorted(by_page.items()):
                page=PDFPage(pdf[number-1],number)
                try:
                    for e in entries:
                        box=e['locator'].get('bbox')
                        rect=[box[0]/page.width,box[1]/page.height,box[2]/page.width,box[3]/page.height] if box else None
                        check(e,page.region(rect))
                finally:page.close()
            record_index={r['record_id']:r for r in records}
            checked_fields=set()
            for selection in selections:
                if selection['sha256']!=source.digest:raise ValueError('selection_sha256_mismatch')
                record=record_index[selection['record_id']]
                field=record['fields'][selection['field']]
                chosen=[]
                for choice in selection['choices']:
                    page=PDFPage(pdf[choice['page']-1],choice['page'])
                    try:
                        state=page.mark_state(choice['mark_rect'],choice['frame_required'],choice.get('enclosing_text',False))
                        if state['state']=='unknown' or any(choice.get(k)!=v for k,v in state.items()):
                            raise ValueError('selection_mark_mismatch')
                    finally:page.close()
                    page=PDFPage(pdf[choice['text_page']-1],choice['text_page'])
                    try:
                        if normalize(choice['option']) not in normalize(page.region(choice['text_rect'])):
                            raise ValueError('selection_option_mismatch')
                    finally:page.close()
                    if choice['state']=='selected':chosen.append(choice['option'])
                    checked+=1
                value=chosen[0] if len(chosen)==1 else None
                if len(chosen)>1 or value!=selection['selected'] or value!=(field['raw_text'] or None):
                    raise ValueError('selection_value_mismatch')
                checked_fields.add((record['record_id'],selection['field']))
            for record in records:
                for name,field in record['fields'].items():
                    if 'selection_checks' in field.get('note','') and (record['record_id'],name) not in checked_fields:
                        raise ValueError('selection_proof_missing')
    else:
        sheets={s.name:s for s in read_xlsx(source.data)}
        for record in records:
            for e in all_evidence(record):
                if e['doc_id']!=source.doc_id:raise ValueError('unexpected_evidence_document')
                loc=e['locator'];check(e,sheets[loc['sheet']].region(loc['cells']))
    return checked


def audit_municipality(directory,manifest_path,out):
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    folder=out/manifest.get('slug',directory.name)
    records=[json.loads(line) for line in (folder/'records.jsonl').read_text(encoding='utf-8').splitlines()]
    grouped=defaultdict(list);errors=[];checked=0
    selection_groups=defaultdict(list)
    for path in sorted((folder/'documents').glob('*.json')):
        for selection in json.loads(path.read_text(encoding='utf-8')).get('selection_checks',[]):
            selection_groups[selection['doc_id']].append(selection)
    for r in records:
        try:
            validate(r);check_field_values(r)
            if len(r['source_document_ids'])!=1:raise ValueError('unexpected_source_count')
            grouped[r['source_document_ids'][0]].append(r)
        except (ValueError,KeyError) as exc:errors.append({'record_id':r.get('record_id'),'reason':str(exc)})
    documents=manifest['documents']
    if isinstance(documents,list):documents={d['doc_id']:d for d in documents}
    for doc_id,metadata in sorted(documents.items()):
        if not any(key==doc_id or key.startswith(doc_id+'--') for key in grouped):continue
        try:
            path=source_path(directory,doc_id,metadata)
            source=Source(doc_id,path.read_bytes(),metadata.get('file_type',path.suffix[1:]),metadata)
            if metadata.get('sha256') and source.digest!=metadata['sha256']:raise ValueError('manifest_sha256_mismatch')
            sources=archive_sources(source) if source.file_type=='zip' else [source]
            for child in sources:
                entries=grouped.pop(child.doc_id,[])
                if entries:checked+=check_source_records(child,entries,selection_groups[child.doc_id])
        except Exception as exc:errors.append({'doc_id':doc_id,'reason':type(exc).__name__+':'+str(exc)})
    for doc_id in grouped:errors.append({'doc_id':doc_id,'reason':'source_not_found'})
    for entry in json.loads((folder/'source_documents.json').read_text(encoding='utf-8')):
        if entry.get('derived_from'):
            path=folder/entry['file']
            if not path.is_file() or sha256(path.read_bytes()).hexdigest()!=entry['sha256']:
                errors.append({'doc_id':entry['doc_id'],'reason':'rendered_source_mismatch'})
    result={'municipality':directory.name,'records':len(records),'evidence_checked':checked,'errors':errors,'ok':not errors}
    write_json(folder/'audit_report.json',result)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work-dir',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--code')
    args=p.parse_args(argv);results=[]
    for directory,manifest in input_manifests(args.work_dir):
        if args.code and not directory.name.startswith(args.code):continue
        if not (args.out/directory.name/'records.jsonl').exists():continue
        result=audit_municipality(directory,manifest,args.out);results.append(result)
        print(result['municipality'],result['records'],'records',len(result['errors']),'errors',flush=True)
    write_json(args.out/'audit_summary.json',results)
    return 0 if results and all(r['ok'] for r in results) else 1


if __name__=='__main__':raise SystemExit(main())
