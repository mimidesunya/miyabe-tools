"""列ごとに事業を持つ Excel 評価表。原文セルの確認用 PDF も固定する。

Excel の印刷再現ではなく、参照セルを番地付きでそのまま描く確認用写し。
元ファイルのシート・セルを必ず併記し、図形や計算式の再計算は行わない。
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import re

from .engine import REQUIRED, ExtractionError, year_value
from .readers import cell_key, normalize, normalized_match, read_xlsx
from .validation import validate


def wrap_text(text,font,size,width):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    lines=[]
    for line in text.splitlines() or [""]:
        part=""
        for c in line:
            if part and stringWidth(part+c,font,size)>width:
                lines.append(part);part=""
            part+=c
        lines.append(part)
    return lines


def render_projects(path,projects):
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    font="HeiseiKakuGo-W5"
    pdfmetrics.registerFont(UnicodeCIDFont(font))
    canvas=Canvas(str(path),pagesize=(842,1191),invariant=1,pageCompression=1)
    canvas.setTitle("Excel 原文セルの確認用写し")
    pages={}
    for number, (sheet,col,template) in enumerate(projects,1):
        pages[(sheet.name,col)]=number
        canvas.setFont(font,10)
        canvas.drawString(35,1156,"Excel 原文セルの確認用写し（元の印刷レイアウトの再現ではありません）")
        canvas.drawString(35,1139,f"シート: {sheet.name} / 列: {col} / 頁: {number}")
        rows=[]
        for cell in (template["year_cell"],col+str(template["code_row"]),col+str(template["name_row"])):
            rows.append((cell,sheet.cells.get(cell,""),"",""))
        for n in template["question_rows"]:
            label_col="G" if n>=28 else "C"
            rows.append((label_col+str(n),sheet.cells.get(label_col+str(n),""),col+str(n),sheet.cells.get(col+str(n),"")))
        y=1118
        for label_ref,label,value_ref,value in rows:
            left=wrap_text(label,font,9,520)
            right=wrap_text(value,font,9,210)
            height=max(len(left),len(right))*13+23
            if y-height<25:
                raise ExtractionError("spreadsheet_review_page_overflow")
            canvas.setLineWidth(.4)
            canvas.rect(35,y-height,772,height)
            canvas.line(575,y,575,y-height)
            canvas.setFont(font,7)
            canvas.drawString(39,y-10,label_ref)
            canvas.drawString(579,y-10,value_ref)
            canvas.setFont(font,9)
            for i,line in enumerate(left):canvas.drawString(39,y-23-i*13,line)
            for i,line in enumerate(right):canvas.drawString(579,y-23-i*13,line)
            y-=height
        canvas.showPage()
    canvas.save()
    return pages


def extract_xlsx(source,municipality,templates,out_dir):
    templates=[t for t in templates if t.get("format")=="xlsx"]
    result={"records":[],"failures":[],"warnings":[],"recognized":[],"pages":0,"template_records":{},"rendered_documents":[]}
    projects=[]
    for sheet in read_xlsx(source.data):
        if sheet.hidden:
            result["failures"].append({"sheet":sheet.name,"reason":"hidden_sheet"});continue
        matched=[t for t in templates if all(re.search(m["pattern"],normalize(sheet.cells.get(m["cell"],""))) for m in t["match"])]
        if len(matched)!=1:
            result["failures"].append({"sheet":sheet.name,"reason":"no_template" if not matched else "ambiguous_template"});continue
        template=matched[0]
        if template.get("adapter")!="project_columns":raise ExtractionError("unknown_xlsx_adapter")
        result["recognized"].append({"sheet":sheet.name,"template":template["id"]})
        for col in template["columns"]:
            if sheet.cells.get(col+str(template["name_row"]),"").strip():projects.append((sheet,col,template))
    if not projects:return result
    rendered_id=source.doc_id+"--cell-review"
    rendered=out_dir/"rendered"/(rendered_id+".pdf");rendered.parent.mkdir(parents=True,exist_ok=True)
    pages=render_projects(rendered,projects)
    result["rendered_documents"].append({"doc_id":rendered_id,"file":"rendered/"+rendered.name,
                                        "sha256":sha256(rendered.read_bytes()).hexdigest(),"derived_from":source.doc_id,
                                        "purpose":"参照セルの番地付き確認用写し。Excel 印刷の再現ではない。"})
    counts=Counter()
    for sheet,col,template in projects:
        number=pages[(sheet.name,col)]
        def evidence(text,cells):
            if normalize(text) not in normalize(sheet.region(cells)):
                raise ExtractionError("source_mismatch")
            return {"doc_id":source.doc_id,"source_url":source.metadata["source_url"],"page_url":source.metadata["page_url"],
                    "sha256":source.digest,"locator":{"type":"spreadsheet","sheet":sheet.name,"cells":cells,
                                                         "rendered_doc_id":rendered_id,"rendered_page":number},
                    "quote":text,"methods":["excel"],"confidence":"medium",
                    "confidence_basis":"OOXML の表示文字・保存済み計算値と完全一致。ふりがな情報は本文から除外。",
                    "review":"unreviewed"}
        def field(label,label_ref,value,value_ref,name=None):
            if value_ref in sheet.formulas_without_cache:
                raise ExtractionError("formula_without_cached_value:"+value_ref)
            ev=[evidence(label,label_ref)]
            if value:ev.insert(0,evidence(value,value_ref))
            return {"status":"present" if value else "not_recorded","field_kind":"dedicated",
                    "value":year_value(value) if name=="evaluation_year" else value or None,
                    "raw_text":value or None,"raw_label":label,"evidence":ev,
                    "note":"原文のセルから転記。事業名・番号は各事業の列見出しそのものを raw_label とする。"}
        try:
            name_ref=col+str(template["name_row"]);code_ref=col+str(template["code_row"])
            name=sheet.cells[name_ref];code=sheet.cells.get(code_ref,"")
            year_ref=template["year_cell"];year_label=sheet.cells[year_ref]
            year=normalized_match(year_label,r"(令和[0-9]+年度)")
            if not year:raise ExtractionError("year_not_explicit")
            fields={"project_name":field(name,name_ref,name,name_ref),
                    "project_code":field(code,code_ref,code,code_ref),
                    "evaluation_year":field(year_label,year_ref,year,year_ref,"evaluation_year")}
            record_evidence=[evidence(name,name_ref)]
            for name_key in REQUIRED:
                if name_key not in fields:
                    fields[name_key]={"status":"not_recorded","field_kind":"absent","value":None,"raw_text":None,
                                      "raw_label":None,"evidence":record_evidence,
                                      "note":"3視点評価シートの事業列には専用欄がない。会議名や回答文から推測しない。"}
            raw_fields=[]
            for occurrence,n in enumerate(template["question_rows"],1):
                label_ref=("G" if n>=28 else "C")+str(n);value_ref=col+str(n)
                label=sheet.cells.get(label_ref,"");value=sheet.cells.get(value_ref,"")
                if not label:raise ExtractionError("missing_question_label:"+label_ref)
                raw_fields.append({"field_id":"row-"+str(n),"label":label,"section_path":[],"occurrence":occurrence,
                                   "content":field(label,label_ref,value,value_ref),"canonical_field":None})
            key=sha256((municipality["slug"]+source.doc_id+sheet.name+col).encode()).hexdigest()[:24]
            record={"schema_version":"1.2.0","record_id":"hyoka:"+key,"municipality":municipality,
                    "project_key":municipality["slug"]+":"+sha256(normalize(sheet.name+code+name).encode()).hexdigest()[:20],
                    "evaluation_instance_key":"source:"+key,"record_status":"provisional",
                    "source_document_ids":[source.doc_id],"record_evidence":record_evidence,"layout":"list_only",
                    "fields":fields,"raw_fields":raw_fields,"vocabulary_tables":[],
                    "extraction":{"created_by":"hyoka.extract/0.1.0:"+template["id"],"human_reviewed":False,
                                  "completeness":"partial_example","note":"事業列の質問・回答・点数を原文で保存。図形の施策名・シート外の作業欄は取り出さない。確認用PDFはセルの写し。"}}
            validate(record);result["records"].append(record);counts[template["id"]]+=1
        except (ValueError,KeyError) as exc:
            result["failures"].append({"sheet":sheet.name,"column":col,"reason":"candidate_rejected","detail":str(exc)})
    result["template_records"]=dict(counts);result["pages"]=len(projects)
    return result
