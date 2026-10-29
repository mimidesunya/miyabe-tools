"""見出しと欄範囲を宣言した様式を実行し、出典を照合してから記録にする。"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import unicodedata

import yaml

from .readers import PDFPage, ReadError, normalize, normalized_match, pdf_document
from .validation import validate, ValidationError

REQUIRED = ("project_name", "department", "evaluation_year", "target_year", "future_direction", "evaluation_actor")
TEMPLATE_DIR = Path(__file__).parent / "templates"


class ExtractionError(ValueError):
    pass


def load_templates(directory=TEMPLATE_DIR):
    result = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if document.get("version") != 1:
            raise ExtractionError("unsupported_template_version:"+path.name)
        code = str(document["municipality"]["code"])
        if not re.fullmatch(r"[0-9]{5}",code):
            raise ExtractionError("invalid_municipality_code:"+path.name)
        if code in result:
            raise ExtractionError("duplicate_municipality:"+code)
        for template in document["templates"]:
            if not template.get("match") or not template.get("id"):
                raise ExtractionError("template_needs_match_and_id")
            if template.get('action')=='exclude':continue
            for instance in template.get("instances",[{}]):
                field_names = set(template.get("fields",{})) | set(instance.get("fields",{}))
                if set(REQUIRED) - field_names:
                    raise ExtractionError("required_field_rule_missing:"+template["id"])
        result[code] = document
    return result


def year_value(raw):
    value = normalize(raw)
    # 確認した専用年度欄だけで用いる。基準年の補完や +/-1 はしない。
    m = re.search(r"(令和|平成|昭和|R|H|S)(元|[0-9]{1,2})(?:年|年度|$)",value)
    if m:
        base={"令和":2018,"R":2018,"平成":1988,"H":1988,"昭和":1925,"S":1925}[m[1]]
        return base + (1 if m[2]=="元" else int(m[2]))
    m = re.search(r"(?<![0-9])((?:18|19|20|21)[0-9]{2})(?:年|年度|$)",value)
    if m: return int(m[1])
    raise ExtractionError("year_not_explicit")


def resolve_rect(spec, variables):
    rect = spec.get("rect")
    if rect is None:
        return None
    result = [variables[x] if isinstance(x,str) else variables[x['var']]+x.get('offset',0) if isinstance(x,dict) else x for x in rect]
    if len(result)!=4 or not (0<=result[0]<result[2]<=1 and 0<=result[1]<result[3]<=1):
        raise ExtractionError("invalid_rect")
    return result


class Context:
    def __init__(self, source, pages, start, variables=None, first_page=None):
        self.source, self.pages, self.start = source, pages, start
        self.variables = variables or {}
        self.proofs = []
        self.warnings = []
        self.selections = []
        self.first_page = first_page
        self.current_field = None

    def read(self, spec):
        offset = spec.get("page",0)
        if offset=='first':
            if self.first_page is None:raise ExtractionError('missing_document_first_page')
            page=self.first_page
        else:
            index = self.start + offset
            if not 0 <= index < len(self.pages):
                raise ExtractionError("missing_continuation_page")
            page = self.pages[index]
        if "choices" in spec:
            marked=[];checks=[]
            for choice in spec["choices"]:
                text,tp,tr=self.read({'page':offset,**choice['text']})
                if not normalize(text):raise ExtractionError('checkbox_option_empty')
                _,mp,mr=self.read({'page':offset,**choice['mark']})
                state=mp.mark_state(mr,choice.get('frame_required',True),choice.get('enclosing_text',False))
                checks.append({'page':mp.number,'mark_rect':mr,'frame_required':choice.get('frame_required',True),
                               'enclosing_text':choice.get('enclosing_text',False),
                               'option':text,'text_rect':tr,'text_page':tp.number,**state})
                if state['state']=='unknown':raise ExtractionError('checkbox_unreadable')
                if state['state']=='selected':marked.append((text,tp,tr))
            if len(marked)>1:raise ExtractionError("checkbox_multiple_selected")
            if not marked and spec.get('require_selection'):raise ExtractionError('checkbox_no_selected')
            self.selections.append({'field':self.current_field,'doc_id':self.source.doc_id,'sha256':self.source.digest,
                                    'choices':checks,'selected':marked[0][0] if marked else None})
            if not marked:return "",page,resolve_rect(spec,self.variables)
            return marked[0]
        rect = resolve_rect(spec,self.variables)
        if 'merged_row' in spec:
            # 一覧の所属など、複数の事業行にまたがる結合セル。
            left,right=spec['columns']
            mid=(self.variables['row_top']+self.variables['row_bottom'])/2
            rules=page.horizontal_rules(*spec.get('rule_span',[left,right]))
            above=[y for y in rules if y<mid];below=[y for y in rules if y>mid]
            if not above or not below:raise ExtractionError('merged_cell_border_not_found')
            rect=[left,above[-1]+.001,right,below[0]-.001]
        if 'between_headings' in spec:
            start_pattern,end_pattern=spec['between_headings']
            starts=page.anchor_rects(start_pattern,rect)
            if len(starts)!=1:raise ExtractionError('anchor_missing_or_ambiguous:'+start_pattern)
            ends=[a for a in page.anchor_rects(end_pattern,rect) if a[1]>starts[0][3]]
            if not ends:raise ExtractionError('end_heading_missing:'+end_pattern)
            left,right=spec.get('columns',[0,1])
            rect=[left,starts[0][1],right,min(a[1] for a in ends)-.001]
        if 'same_row' in spec:
            found=page.anchor_rects(spec['same_row'],rect)
            if 'anchor_index' in spec:found=found[spec['anchor_index']:spec['anchor_index']+1]
            if len(found)!=1:raise ExtractionError('anchor_missing_or_ambiguous:'+spec['same_row'])
            a=found[0];left,right=spec['columns']
            if left=='anchor_right':left=a[2]+.002
            if spec.get('enclosing_frame'):
                frame=page.enclosing_frame(a,[left,right])
                top,bottom=frame[1]+.001,frame[3]-.001
            elif 'row_span' in spec:
                rules=page.horizontal_rules(*spec['row_span'],max_thickness=spec.get('rule_thickness',1.5))
                above=[y for y in rules if y<a[1]];below=[y for y in rules if y>a[3]]
                if not above or not below:raise ExtractionError('field_border_not_found')
                top,bottom=above[-1]+.001,below[0]-.001
                if bottom-top>spec.get('max_height',.15):raise ExtractionError('field_border_too_far')
            else:
                top=a[1]+spec.get('top_offset',-.002)
                bottom=a[3]+spec.get('bottom_offset',.002)
            rect=[left,top,right,bottom]
        if "below_heading" in spec:
            found=page.anchor_rects(spec["below_heading"],rect)
            if "heading_index" in spec:
                found=found[spec["heading_index"]:spec["heading_index"]+1]
            if len(found)!=1:raise ExtractionError("anchor_missing_or_ambiguous:"+spec["below_heading"])
            l,r=spec["columns"]
            rule_l,rule_r=spec.get('rule_span',[l,r])
            rules=page.horizontal_rules(rule_l,rule_r,found[0][3],spec.get("bottom_limit",.98))
            end=spec.get("end_rule",1)
            if 'bottom_from' in spec:
                _,other_page,other_rect=self.read(spec['bottom_from'])
                if not rules or other_page!=page or other_rect is None:raise ExtractionError('field_border_not_found')
                bottom=other_rect[3]
            else:
                if len(rules)<=end:raise ExtractionError("field_border_not_found")
                bottom=rules[end]-.001
            rect=[l,rules[0]+.001,r,bottom]
            if rect[1]>=rect[3]:raise ExtractionError('invalid_field_borders')
        if "anchor" in spec or 'mark_box_left' in spec or 'glyph_box' in spec:
            found = page.anchor_rects(spec.get('anchor',spec.get('mark_box_left',spec.get('glyph_box'))),rect)
            if "anchor_index" in spec:
                found=found[spec["anchor_index"]:spec["anchor_index"]+1]
            if len(found) != 1:
                raise ExtractionError("anchor_missing_or_ambiguous")
            anchor = found[0]
            if 'mark_box_left' in spec:
                rect=page.box_left_of(anchor)
            elif 'glyph_box' in spec:rect=anchor
            else:
                delta = spec["relative_rect"]
                rect = [anchor[0]+delta[0],anchor[1]+delta[1],anchor[0]+delta[2],anchor[1]+delta[3]]
        text = page.region(rect)
        if "pattern" in spec:
            text = normalized_match(text,spec["pattern"])
            if text is None:
                raise ExtractionError("pattern_not_found:"+spec["pattern"])
        return text, page, rect

    def evidence(self, text, page, rect):
        if not normalize(text):
            raise ExtractionError("empty_evidence")
        # 別の読み出しで再確認。曖昧一致・編集距離・単語の寄せ集めは禁止。
        if normalize(text) not in normalize(page.region(rect)):
            raise ExtractionError("source_mismatch")
        locator = page.locator(rect)
        box = locator.get("bbox")
        stored_rect = [box[0]/page.width, box[1]/page.height,
                       box[2]/page.width, box[3]/page.height] if box else None
        if normalize(text) not in normalize(page.region(stored_rect)):
            raise ExtractionError("serialized_source_mismatch")
        self.proofs.append((text,page,rect))
        return {"doc_id":self.source.doc_id, "source_url":self.source.metadata["source_url"],
                "page_url":self.source.metadata["page_url"], "sha256":self.source.digest,
                "locator":locator, "quote":text, "methods":["pdf_text"],
                "confidence":"medium", "confidence_basis":"様式の見出し・領域を照合し、原典頁の同じ領域に正規化した原文が完全一致。意味の正しさは別途目視点検。",
                "review":"unreviewed"}

    def field(self, name, spec, record_evidence):
        self.current_field=name
        try:
            return self._field(name,spec,record_evidence)
        except ExtractionError as exc:
            raise ExtractionError(name+':'+str(exc)) from exc

    def _field(self, name, spec, record_evidence):
        if "absent" in spec:
            return {"status":"not_recorded","field_kind":"absent","value":None,"raw_text":None,
                    "raw_label":None,"evidence":record_evidence,"note":spec["absent"]}
        label, lp, lr = self.read(spec["label"])
        label_evidence = self.evidence(label,lp,lr)
        value, vp, vr = self.read(spec.get("value",spec["label"]))
        status = spec.get("status", "present" if normalize(value) else "not_recorded")
        if status == "unreadable":
            self.warnings.append({"field":name,"reason":"unreadable","detail":spec["note"]})
        ev = [self.evidence(value,vp,vr)] if normalize(value) else [label_evidence]
        # 値だけの根拠とは別に実際の欄名も保つ（ラベル用の bbox）。
        if label_evidence not in ev:
            ev.append(label_evidence)
        result = {"status":status,"field_kind":"dedicated","value":value if status=="present" else None,
                  "raw_text":value if value else None, "raw_label":label, "evidence":ev,
                  "note":spec.get("note","専用の欄から原文を転記。" if status=="present" else "専用の欄はあるが空欄。")}
        if 'choices' in spec.get('value',{}):
            result['note']+=' 選択印を検証。文書結果の selection_checks に判定根拠を保存。'
        if status == "present":
            if name in ("evaluation_year","target_year"):
                result["value"] = year_value(value)
            elif name == "future_direction":
                result["value"] = {"raw_term":value,"aspect":"unspecified","normalized":[],
                                   "mapping_status":"unmapped","mapping_entry_ids":[],"relation":"unmapped"}
            elif name == "evaluation_rating":
                result["value"] = {"raw_term":value,"dimension":label,"meaning":None,
                                   "mapping_status":"unmapped","mapping_entry_ids":[]}
        return result

    def verify(self):
        for value,page,rect in self.proofs:
            if normalize(value) not in normalize(page.region(rect)):
                raise ExtractionError("source_mismatch")


def template_matches(template, page):
    if template.get("format","pdf") != "pdf": return False
    if "size_ratio" in template:
        low,high=template["size_ratio"]
        if not low<=page.width/page.height<=high: return False
    for match in template["match"]:
        if not re.search(unicodedata.normalize("NFKC",match["pattern"]),normalize(page.region(match.get("rect")))):
            return False
    for match in template.get("exclude",[]):
        if re.search(unicodedata.normalize("NFKC",match["pattern"]),normalize(page.region(match.get("rect")))):
            return False
    return True


def record_from_context(context, municipality, template, instance, row_key):
    specs={**template["fields"],**instance.get("fields",{})}
    title_spec=template.get("record_title",specs["project_name"]["value"])
    title,tp,tr=context.read(title_spec)
    record_evidence=[context.evidence(title,tp,tr)]
    fields={name:context.field(name,spec,record_evidence) for name,spec in specs.items()}
    if fields["project_name"]["status"]!="present":
        raise ExtractionError("project_name_missing")
    raw_fields=[]
    for i,spec in enumerate(template.get("raw_fields",[])+instance.get("raw_fields",[])):
        content=context.field("raw",spec,record_evidence)
        field_id=f"raw-{i+1}"
        raw_fields.append({"field_id":field_id,"label":content["raw_label"],
                           "section_path":[],"occurrence":i+1,"content":content,
                           "canonical_field":spec.get("canonical_field")})
        for name in spec.get("embedded_fields",[]):
            # 様式の文中に当該意味があるかをプログラムでは解釈しない。
            fields[name]={"status":"represented_elsewhere","field_kind":"embedded","value":None,
                          "raw_text":None,"raw_label":None,"evidence":content["evidence"],
                          "note":"専用欄がなく、この原文欄全体を保存。文中の意味は分解しない。",
                          "related_fields":[field_id]}
            content["field_kind"]="embedded"
            raw_fields[-1]["canonical_field"]=None
    attachment=template.get('continuation_attachment')
    if attachment and len(context.pages)>template.get('pages',1):
        offset=template.get('pages',1)
        attached_name,_,_=context.read({'page':offset,**attachment['project_name']})
        if normalize(attached_name)!=normalize(fields['project_name']['raw_text']):
            raise ExtractionError('attachment_project_mismatch')
        content=context.field('attachment',{'label':{'page':offset,**attachment['label']},'value':{'page':offset}},record_evidence)
        raw_fields.append({'field_id':'attachment-1','label':content['raw_label'],'section_path':[],
                           'occurrence':1,'content':content,'canonical_field':None})
    context.verify()
    identity = "\x1f".join((municipality["slug"],context.source.doc_id,str(context.start+1),str(row_key),instance.get("id","default")))
    key = sha256(identity.encode()).hexdigest()[:24]
    project_code=fields.get("project_code",{})
    project_token=(project_code.get("raw_text") if project_code.get("status")=="present" else None) or fields["project_name"]["raw_text"]
    project_token += "\x1f" + fields["project_name"]["raw_text"] + "\x1f" + (fields["department"].get("raw_text") or "")
    project_key=municipality["slug"]+":"+sha256(normalize(project_token).encode()).hexdigest()[:20]
    identified=all(fields[k]["status"]=="present" for k in ("project_name","evaluation_year","evaluation_actor"))
    record={"schema_version":"1.2.0","record_id":"hyoka:"+key,"municipality":municipality,
            "project_key":project_key,"evaluation_instance_key":"source:"+key,
            "record_status":"identified" if identified else "provisional",
            "source_document_ids":[context.source.doc_id],"record_evidence":record_evidence,
            "layout":template["layout"],"fields":fields,"raw_fields":raw_fields,"vocabulary_tables":[],
            "extraction":{"created_by":"hyoka.extract/0.1.0:"+template["id"],"human_reviewed":False,
                          "completeness":"partial_example",
                          "note":"宣言した欄のみを決定的に転記。未対応の欄・表があり、全欄の完全性は保証しない。方向性は原語を保持し読み替えない。"}}
    validate(record)
    return record


def row_ranges(template,page):
    if 'repeat' in template:
        spec=template['repeat'];anchors=page.anchor_rects(spec['anchor'],spec.get('rect'))
        if not anchors:raise ExtractionError('repeat_anchor_missing')
        return [(str(i+1),{'anchor_y':a[1],
                           'anchor_bottom':anchors[i+1][1]-.004 if i+1<len(anchors) else spec.get('bottom',.97)}) for i,a in enumerate(anchors)]
    table=template.get("table")
    if not table:
        return [("single",{})]
    left,right=table["rule_span"]
    ys=page.horizontal_rules(left,right,*table["y_range"])
    rows=[]
    for top,bottom in zip(ys,ys[1:]):
        if bottom-top<table.get("min_height",0.008): continue
        variables={"row_top":top+0.001,"row_bottom":bottom-0.001}
        test=dict(table["row_test"])
        rect=resolve_rect(test,variables)
        if re.fullmatch(test["pattern"],normalize(page.region(rect))):
            rows.append((str(len(rows)+1),variables))
    if not rows: raise ExtractionError("table_rows_not_found")
    return rows


def extract_pdf(source, municipality, templates):
    records=[]; failures=[]; warnings=[]; recognized=[]; counts=Counter();excluded=[];selections=[]
    with pdf_document(source.data) as doc:
        first=PDFPage(doc[0],1) if len(doc) else None
        # 少数頁の窓で処理し、長い報告書を全頁保持しない。
        start=0
        while start<len(doc):
            page=PDFPage(doc[start],start+1)
            pages=[page]
            try:
                if len(normalize(page.text))<20:
                    failures.append({"page":start+1,"reason":"needs_ocr","detail":"頁の文字が無い、又は短すぎる。OCR は未実施。"})
                    start+=1;continue
                matched=[t for t in templates if template_matches(t,page)
                         and (not t.get('first_page_only') or start==0)
                         and (not t.get('not_first_page') or start>0)
                         and (not t.get('document_match') or template_matches({'match':t['document_match']},first))]
                active=[t for t in matched if t.get('action')!='exclude']
                if not active and matched:
                    excluded.append({'page':start+1,'templates':[t['id'] for t in matched],
                                     'categories':sorted({t.get('category','non_project') for t in matched})})
                    start+=1;continue
                matched=active
                if any('next_page_pattern' in t or 'next_page_not_pattern' in t for t in matched):
                    following=''
                    if start+1<len(doc):
                        next_page=PDFPage(doc[start+1],start+2)
                        try:following=normalize(next_page.text)
                        finally:next_page.close()
                    matched=[t for t in matched
                             if ('next_page_pattern' not in t or re.search(t['next_page_pattern'],following))
                             and ('next_page_not_pattern' not in t or not re.search(t['next_page_not_pattern'],following))]
                if not matched:
                    failures.append({"page":start+1,"reason":"no_template"})
                    start+=1;continue
                if len(matched)>1:
                    failures.append({"page":start+1,"reason":"ambiguous_template","templates":[t["id"] for t in matched]})
                    start+=1;continue
                template=matched[0];span=template.get("pages",1)
                recognized.append({"page":start+1,"template":template["id"]})
                for offset in range(1,span):
                    if start+offset>=len(doc): raise ExtractionError("missing_continuation_page")
                    pages.append(PDFPage(doc[start+offset],start+offset+1))
                if template.get('continuation_attachment') and start+span<len(doc):
                    candidate=PDFPage(doc[start+span],start+span+1)
                    if template_matches(template['continuation_attachment'],candidate):
                        pages.append(candidate);span+=1
                    else:candidate.close()
                for match in template.get("continuation_match",[]):
                    p=pages[match["page"]]
                    if not re.search(match["pattern"],normalize(p.region(match.get("rect")))):
                        raise ExtractionError("continuation_mismatch")
                lookback = template.get('lookback_pages',0)
                if lookback:
                    if not isinstance(lookback,int) or not 1<=lookback<=3:
                        raise ExtractionError('invalid_lookback_pages')
                    if start<lookback:raise ExtractionError('missing_previous_page')
                    pages[:0]=[PDFPage(doc[i],i+1) for i in range(start-lookback,start)]
                    for match in template.get('previous_match',[]):
                        prior=pages[lookback+match['page']]
                        if not re.search(match['pattern'],normalize(prior.region(match.get('rect')))):
                            raise ExtractionError('previous_heading_mismatch')
                for row_key,variables in row_ranges(template,page):
                    for instance in template.get("instances",[{}]):
                        context=Context(source,pages,lookback,variables,first_page=first)
                        try:
                            record=record_from_context(context,municipality,template,instance,row_key)
                            # 窓の位置ではなく原典の物理頁で一意にする。
                            identity="\x1f".join((municipality["slug"],source.doc_id,str(start+1),row_key,instance.get("id","default")))
                            key=sha256(identity.encode()).hexdigest()[:24]
                            record["record_id"]="hyoka:"+key
                            record["evaluation_instance_key"]="source:"+key
                            records.append(record);counts[template["id"]]+=1
                            selections.extend({'record_id':record['record_id'],**s} for s in context.selections)
                            warnings.extend({"page":start+1,"record_id":record["record_id"],**w} for w in context.warnings)
                        except (ExtractionError,ValidationError,ReadError) as exc:
                            failures.append({"page":start+1,"row":row_key,"instance":instance.get("id","default"),
                                             "template":template["id"],"reason":"candidate_rejected","detail":str(exc)})
                start+=span
            except (ExtractionError,ValueError) as exc:
                failures.append({"page":start+1,"reason":"page_error","detail":str(exc)})
                start+=1
            finally:
                for p in pages:p.close()
        if first:first.close()
    return {"records":records,"failures":failures,"warnings":warnings,"recognized":recognized,
            "excluded_pages":excluded,'selection_checks':selections,"template_records":dict(counts),"pages":start}
