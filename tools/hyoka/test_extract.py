"""評価表の抽出を、公開してよい小さな合成資料で検証する。ネットワーク不要。"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

from tools.hyoka.extract.engine import Context, ExtractionError, extract_pdf, load_templates, year_value
from tools.hyoka.extract.readers import Source, PDFPage, ReadError, archive_sources, normalize, normalized_match, pdf_document, read_xlsx
from tools.hyoka.extract.validation import SCHEMA_PATH, ValidationError, check_schema_keywords, validate
from tools.hyoka.extract.run_extract import process_source, run_municipality, source_path
from tools.hyoka.extract.audit_output import audit_municipality, check_field_values, check_source_records


MUNI={'slug':'99999-example-shi','code':'99999','name':'例市','identity_source':'document_manifest'}
META={'source_url':'https://example.org/evaluation.pdf','page_url':'https://example.org/evaluation',
      'file_type':'pdf','fiscal_year':2099}


def zip_bytes(files):
    data=BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        for name,value in files.items():z.writestr(name,value)
    return data.getvalue()


def sample_pdf(blank=False,marks=None,outlined=False,framed=False,thick_rules=False):
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    data=BytesIO();c=Canvas(data,pagesize=(600,800),invariant=1)
    if not blank:
        c.setFont('Helvetica',12)
        # 描画順序が視覚上の順序と異なっていても、別欄を混ぜずに読む。
        c.drawString(150,690,'Road maintenance')
        c.drawString(50,690,'Project')
        c.drawString(50,750,'Project evaluation')
        c.drawString(50,650,'Department');c.drawString(150,650,'Road division')
        c.drawString(50,610,'Direction');c.drawString(150,610,'Continue')
        c.drawString(50,570,'Year');c.drawString(150,570,'2025')
        c.drawString(50,530,'Purpose');c.drawString(150,530,'Future change is in this sentence.')
        pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5'))
        c.setFont('HeiseiKakuGo-W5',12)
        c.drawString(50,480,'いっしょに、Kochake! を進める。')
        for y in (420,380,340):
            c.line(50,y,250,y);c.line(250,y,550,y)
        c.setFillColorRGB(.8,.8,.8)
        c.rect(50,390,500,15,stroke=0,fill=1)  # セルの背景は罫線ではない。
        path=c.beginPath();path.rect(50,360,500,.5);path.rect(50,400,500,.5)
        c.drawPath(path,stroke=0,fill=1)  # 複数の罫線をまとめた塗りパス。
        if marks is not None:
            c.setFillColorRGB(0,0,0);c.setFont('Helvetica',12)
            for i,selected in enumerate(marks):
                x=150+i*150;c.rect(x,300,10,10,stroke=1,fill=0)
                c.drawString(x+16,300,['Continue','Stop'][i])
                if selected:
                    if outlined:
                        text_width=pdfmetrics.stringWidth(['Continue','Stop'][i],'Helvetica',12)
                        c.rect(x+14,298,text_width+4,14,stroke=1,fill=0)
                    else:c.rect(x+3,303,4,4,stroke=0,fill=1)
        if framed:
            c.setFillColorRGB(0,0,0)
            path=c.beginPath()
            path.rect(40,600,520,30)
            path.rect(42,602,516,26)
            c.drawPath(path,stroke=0,fill=1,fillMode=0)
        if thick_rules:
            c.setFillColorRGB(0,0,0)
            for y in (600,630):c.rect(40,y,520,3,stroke=0,fill=1)
    c.showPage();c.save();return data.getvalue()


def sample_template():
    def field(label,y):
        return {'label':{'rect':[.08,y,.24,y+.035],'pattern':label},'value':{'rect':[.25,y,.94,y+.035]}}
    fields={'project_name':field('Project',.12),'department':field('Department',.17),
            'future_direction':field('Direction',.22),'evaluation_year':field('Year',.27),
            'target_year':{'absent':'対象年度専用欄なし。manifest を使わない。'},
            'evaluation_actor':{'absent':'主体専用欄なし。担当部署から推測しない。'}}
    return {'id':'synthetic','layout':'single_project_sheet','match':[{'pattern':'Projectevaluation'}],
            'fields':fields,'raw_fields':[field('Purpose',.32)]}


class ReaderTest(unittest.TestCase):
    def test_normalization_preserves_original_slice(self):
        text='見出し： Ａ\nＢ  １２３、終り。'
        self.assertEqual(normalize(text),'見出し:AB123、終り。')
        self.assertEqual(normalized_match(text,r'見出し:(AB123)'), 'Ａ\nＢ  １２３')
        self.assertEqual(normalized_match('③方向性','③方向性'),'③方向性')

    def test_year_is_explicit_not_inferred(self):
        self.assertEqual(year_value('令和 元 年度'),2019)
        self.assertEqual(year_value('平成２８年６月３０日'),2016)
        with self.assertRaises(ExtractionError):year_value('今年度')
        with self.assertRaises(ExtractionError):year_value('6')

    def test_pdf_coordinates_baseline_and_separate_cell_borders(self):
        with pdf_document(sample_pdf()) as doc:
            page=PDFPage(doc[0],1)
            try:
                self.assertEqual(normalize(page.region([.25,.12,.94,.155])),normalize('Road maintenance'))
                self.assertEqual(normalize(page.region([.07,.38,.95,.42])),normalize('いっしょに、Kochake! を進める。'))
                rules=page.horizontal_rules(.1,.90,.45,.60)
                self.assertEqual(len(rules),5)
                self.assertAlmostEqual(rules[0],.475,places=4)
                self.assertEqual(len(page.anchor_rects('Department')),1)
            finally:page.close()

    def test_zip_preserves_member_and_rejects_traversal(self):
        source=Source('archive',zip_bytes({'folder/評価.pdf':sample_pdf(),'Thumbs.db':b'x'}),'zip',META)
        children=list(archive_sources(source))
        self.assertEqual(len(children),1)
        self.assertEqual(children[0].member,'folder/評価.pdf')
        self.assertEqual(children[0].parent_doc_id,'archive')
        self.assertEqual(children[0].digest,sha256(sample_pdf()).hexdigest())
        with self.assertRaisesRegex(ReadError,'unsafe_archive_member'):
            list(archive_sources(Source('bad',zip_bytes({'../escape.pdf':b'x'}),'zip',META)))

    def test_xlsx_shared_inline_phonetics_and_formula_cache(self):
        ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
        data=zip_bytes({
            'xl/workbook.xml':f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="評価" sheetId="1" r:id="r1"/></sheets></workbook>',
            'xl/_rels/workbook.xml.rels':'<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>',
            'xl/sharedStrings.xml':f'<sst xmlns="{ns}"><si><t>事業</t><rPh sb="0" eb="2"><t>ジギョウ</t></rPh></si></sst>',
            'xl/worksheets/sheet1.xml':f'<worksheet xmlns="{ns}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>改善</t><rPh><t>カイゼン</t></rPh></is></c><c r="C1"><f>1+2</f><v>3</v></c><c r="D1"><f>NOW()</f><v/></c><c r="E1"><f>NOW()</f></c></row></sheetData><mergeCells><mergeCell ref="A2:B2"/></mergeCells></worksheet>'})
        sheet=read_xlsx(data)[0]
        self.assertEqual(sheet.region('A1:C1'),'事業\n改善\n3')
        self.assertEqual(sheet.formulas_without_cache,{'D1','E1'})
        self.assertEqual(sheet.merges,['A2:B2'])


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.source=Source('sample',sample_pdf(),'pdf',META)
        self.template=sample_template()

    def test_reproducible_schema_valid_and_no_metadata_inference(self):
        result=extract_pdf(self.source,MUNI,[self.template])
        self.assertEqual(result['failures'],[])
        self.assertEqual(len(result['records']),1)
        record=result['records'][0];validate(record)
        self.assertEqual(record,extract_pdf(self.source,MUNI,[self.template])['records'][0])
        self.assertEqual(record['fields']['evaluation_year']['value'],2025)
        self.assertEqual(record['fields']['target_year']['status'],'not_recorded')
        self.assertEqual(record['fields']['evaluation_actor']['field_kind'],'absent')
        self.assertEqual(normalize(record['raw_fields'][0]['content']['value']),normalize('Future change is in this sentence.'))
        self.assertEqual(record['fields']['future_direction']['value']['normalized'],[])
        check_field_values(record)

    def test_one_mismatched_field_rejects_whole_record(self):
        original=Context.read
        def changed(context,spec):
            value,page,rect=original(context,spec)
            return ('Invented direction' if normalize(value)=='Continue' else value),page,rect
        with patch.object(Context,'read',changed):
            result=extract_pdf(self.source,MUNI,[self.template])
        self.assertEqual(result['records'],[])
        self.assertIn('source_mismatch',result['failures'][0]['detail'])

    def test_missing_label_and_ambiguous_template_reject(self):
        self.template['fields']['department']['label']['pattern']='Unknown heading'
        self.assertEqual(extract_pdf(self.source,MUNI,[self.template])['records'],[])
        self.template=sample_template()
        result=extract_pdf(self.source,MUNI,[self.template,deepcopy(self.template)])
        self.assertEqual(result['records'],[])
        self.assertEqual(result['failures'][0]['reason'],'ambiguous_template')

    def test_serialized_locator_must_still_contain_quote(self):
        original = PDFPage.locator
        def misplaced(page, rect=None):
            locator = original(page, rect)
            locator['bbox'] = [0, 0, 1, 1]
            return locator
        with patch.object(PDFPage, 'locator', misplaced):
            result = extract_pdf(self.source, MUNI, [self.template])
        self.assertEqual(result['records'], [])
        self.assertIn('serialized_source_mismatch', result['failures'][0]['detail'])

    def test_merged_department_cell_is_bounded_by_its_own_rules(self):
        from reportlab.pdfgen.canvas import Canvas
        stream = BytesIO()
        canvas = Canvas(stream, pagesize=(600,800), invariant=1)
        canvas.setFont('Helvetica',12)
        for y in (400,500,600,700):
            canvas.line(200,y,550,y)
        for y in (400,500,700):
            canvas.line(50,y,200,y)
        canvas.drawString(60,590,'Shared department')
        canvas.drawString(60,440,'Other department')
        canvas.save()
        source = Source('merged',stream.getvalue(),'pdf',META)
        with pdf_document(source.data) as pdf:
            page = PDFPage(pdf[0],1)
            try:
                values = []
                for top,bottom in ((.126,.249),(.251,.374),(.376,.499)):
                    ctx = Context(source,[page],0,{'row_top':top,'row_bottom':bottom})
                    values.append(ctx.read({'merged_row':True,'columns':[.085,.332]})[0])
                self.assertEqual([normalize(v) for v in values],
                                 ['Shareddepartment','Shareddepartment','Otherdepartment'])
            finally:
                page.close()

    def test_table_continuation_keeps_previous_heading_page(self):
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        for _ in range(2):
            writer.add_page(PdfReader(BytesIO(sample_pdf())).pages[0])
        stream = BytesIO()
        writer.write(stream)
        first = deepcopy(self.template)
        first['first_page_only'] = True
        continued = deepcopy(self.template)
        continued.update(id='continued',not_first_page=True,lookback_pages=1,
                         previous_match=[{'page':-1,'pattern':'Projectevaluation'}])
        continued['fields']['project_name']['label']['page'] = -1
        source = Source('continued',stream.getvalue(),'pdf',META)
        result = extract_pdf(source,MUNI,[first,continued])
        self.assertEqual(result['failures'],[])
        self.assertEqual(len(result['records']),2)
        evidence = result['records'][1]['fields']['project_name']['evidence']
        self.assertEqual({e['locator']['page'] for e in evidence},{1,2})
        check_source_records(source,result['records'])
        continued['previous_match'][0]['pattern'] = 'Wrong heading'
        result = extract_pdf(source,MUNI,[first,continued])
        self.assertEqual(len(result['records']),1)
        self.assertEqual(result['failures'][0]['detail'],'previous_heading_mismatch')

    def test_field_with_filled_outline_frame(self):
        source=Source('framed',sample_pdf(framed=True),'pdf',META)
        template=deepcopy(self.template)
        template['fields']['future_direction']['value']={
            'same_row':'Direction','rect':[.08,.22,.24,.255],
            'columns':[.25,.92],'enclosing_frame':True}
        result=extract_pdf(source,MUNI,[template])
        self.assertEqual(result['failures'],[])
        self.assertEqual(result['records'][0]['fields']['future_direction']['raw_text'],'Continue')

    def test_separate_thick_filled_rules_preserve_whole_cell(self):
        source=Source('thick',sample_pdf(thick_rules=True),'pdf',META)
        template=deepcopy(self.template)
        value={'same_row':'Direction','rect':[.08,.22,.24,.255],
               'columns':[.25,.92],'row_span':[.1,.9],'rule_thickness':4}
        template['fields']['future_direction']['value']=value
        result=extract_pdf(source,MUNI,[template])
        self.assertEqual(result['failures'],[])
        self.assertEqual(result['records'][0]['fields']['future_direction']['raw_text'],'Continue')
        check_source_records(source,result['records'])
        del value['rule_thickness']
        result=extract_pdf(source,MUNI,[template])
        self.assertEqual(result['records'],[])
        self.assertIn('field_border_not_found',result['failures'][0]['detail'])

    def test_blank_page_needs_ocr_without_guessing(self):
        result=extract_pdf(Source('empty',sample_pdf(True),'pdf',META),MUNI,[self.template])
        self.assertEqual(result['records'],[])
        self.assertEqual(result['failures'][0]['reason'],'needs_ocr')

    def test_exclusion_is_separate_and_evaluation_template_takes_priority(self):
        exclusion={'id':'cover','action':'exclude','category':'cover','match':[{'pattern':'Projectevaluation'}]}
        result=extract_pdf(self.source,MUNI,[exclusion])
        self.assertEqual(result['failures'],[])
        self.assertEqual(len(result['excluded_pages']),1)
        result=extract_pdf(self.source,MUNI,[exclusion,self.template])
        self.assertEqual(len(result['records']),1)
        self.assertEqual(result['excluded_pages'],[])

    def test_checkbox_graphics_select_blank_reject_multiple_and_reaudit(self):
        choices=[]
        for x,term in [(150,'Continue'),(300,'Stop')]:
            choices.append({'text':{'rect':[(x+16)/600,.605,(x+130)/600,.635],'pattern':term},
                            'mark':{'rect':[x/600,.6125,(x+10)/600,.625]}})
        self.template['fields']['future_direction']['value']={'choices':choices}
        for marks,expected in [([True,False],'Continue'),([False,True],'Stop'),([False,False],None)]:
            source=Source('marks',sample_pdf(marks=marks),'pdf',META)
            result=extract_pdf(source,MUNI,[self.template])
            self.assertEqual(result['failures'],[])
            self.assertEqual(result['records'][0]['fields']['future_direction']['raw_text'],expected)
            self.assertGreater(check_source_records(source,result['records'],result['selection_checks']),0)
            with self.assertRaisesRegex(ValueError,'selection_proof_missing'):
                check_source_records(source,result['records'])
            modified=deepcopy(result['selection_checks']);modified[0]['choices'][0]['state']='unknown'
            with self.assertRaisesRegex(ValueError,'selection_mark_mismatch'):
                check_source_records(source,result['records'],modified)
        result=extract_pdf(Source('marks',sample_pdf(marks=[True,True]),'pdf',META),MUNI,[self.template])
        self.assertEqual(result['records'],[])
        self.assertIn('checkbox_multiple_selected',result['failures'][0]['detail'])

    def test_document_first_page_uses_physical_page_and_same_row(self):
        with pdf_document(self.source.data) as pdf:
            first=PDFPage(pdf[0],1)
            try:
                context=Context(self.source,[],0,first_page=first)
                text,page,rect=context.read({'page':'first','same_row':'Department','columns':[.25,.94]})
                self.assertEqual(normalize(text),'Roaddivision')
                self.assertEqual(context.evidence(text,page,rect)['locator']['page'],1)
                with self.assertRaisesRegex(ExtractionError,'anchor_missing'):
                    context.read({'page':'first','same_row':'Unknown','columns':[.25,.94]})
            finally:first.close()

    def test_rotated_pdf_coordinates_match_display_and_reaudit(self):
        from pypdf import PdfReader,PdfWriter,Transformation
        from pypdf.generic import RectangleObject
        writer=PdfWriter();writer.add_page(PdfReader(BytesIO(sample_pdf())).pages[0]);page=writer.pages[0]
        page.add_transformation(Transformation().rotate(90).translate(tx=800,ty=0))
        page.mediabox=RectangleObject([0,0,800,600]);page.rotate(90)
        data=BytesIO();writer.write(data)
        source=Source('rotated',data.getvalue(),'pdf',META)
        result=extract_pdf(source,MUNI,[self.template])
        self.assertEqual(result['failures'],[])
        self.assertEqual(normalize(result['records'][0]['fields']['project_name']['raw_text']),'Roadmaintenance')
        self.assertGreater(check_source_records(source,result['records']),0)

    def test_option_enclosing_rectangle_requires_four_visible_edges(self):
        choices=[]
        for term in ['Continue','Stop']:
            choices.append({'text':{'rect':[.25,.60,.94,.64],'pattern':term},
                            'mark':{'rect':[.25,.60,.94,.64],'glyph_box':term},'enclosing_text':True})
        self.template['fields']['future_direction']['value']={'choices':choices,'require_selection':True}
        source=Source('outline',sample_pdf(marks=[False,True],outlined=True),'pdf',META)
        result=extract_pdf(source,MUNI,[self.template])
        self.assertEqual(result['failures'],[])
        self.assertEqual(result['records'][0]['fields']['future_direction']['raw_text'],'Stop')
        self.assertGreater(check_source_records(source,result['records'],result['selection_checks']),0)
        source=Source('outline',sample_pdf(marks=[False,False],outlined=True),'pdf',META)
        result=extract_pdf(source,MUNI,[self.template])
        self.assertEqual(result['records'],[])
        self.assertIn('checkbox_no_selected',result['failures'][0]['detail'])

    def test_cannot_put_embedded_text_in_present_field(self):
        record=extract_pdf(self.source,MUNI,[self.template])['records'][0]
        record['fields']['future_direction']['field_kind']='embedded'
        with self.assertRaises(ValidationError):validate(record)

    def test_embedded_keeps_the_whole_original_field_without_a_standard_value(self):
        self.template['raw_fields'][0]['embedded_fields']=['future_direction']
        result=extract_pdf(self.source,MUNI,[self.template])
        self.assertEqual(result['failures'],[])
        record=result['records'][0]
        self.assertEqual(record['fields']['future_direction']['status'],'represented_elsewhere')
        self.assertIsNone(record['fields']['future_direction']['value'])
        self.assertEqual(record['fields']['future_direction']['related_fields'],['raw-1'])
        self.assertEqual(record['raw_fields'][0]['content']['field_kind'],'embedded')
        self.assertIsNone(record['raw_fields'][0]['canonical_field'])

    def test_modified_value_cannot_hide_behind_valid_quote(self):
        record=extract_pdf(self.source,MUNI,[self.template])['records'][0]
        record['fields']['project_name']['value']='Something else'
        with self.assertRaisesRegex(ValueError,'value_changed'):check_field_values(record)

    def test_production_and_flat_paths_resume_and_independent_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/MUNI['slug'];folder.mkdir()
            metadata={**META,'file':'source/sample.pdf'}
            (folder/'sample.pdf').write_bytes(self.source.data)
            self.assertEqual(source_path(folder,'sample',metadata),folder/'sample.pdf')
            (folder/'source').mkdir();(folder/'sample.pdf').rename(folder/'source/sample.pdf')
            self.assertEqual(source_path(folder,'sample',metadata),folder/'source/sample.pdf')
            manifest=folder/'manifest.json'
            manifest.write_text(json.dumps({'slug':MUNI['slug'],'name':MUNI['name'],'documents':{'sample':metadata}}),encoding='utf-8')
            definition={'templates':[self.template]};out=root/'output'
            first=run_municipality(folder,manifest,definition,out)
            result_bytes=(out/MUNI['slug']/'records.jsonl').read_bytes()
            with patch('tools.hyoka.extract.run_extract.process_source',side_effect=AssertionError('cache not used')):
                second=run_municipality(folder,manifest,definition,out)
            self.assertEqual(first,second)
            self.assertEqual(result_bytes,(out/MUNI['slug']/'records.jsonl').read_bytes())
            rebuilt=run_municipality(folder,manifest,definition,out,force=True)
            self.assertEqual(first,rebuilt)
            self.assertEqual(result_bytes,(out/MUNI['slug']/'records.jsonl').read_bytes())
            self.assertTrue(audit_municipality(folder,manifest,out)['ok'])
            # 様式を変えればキャッシュを無効化する。
            definition=deepcopy(definition);definition['templates'][0]['fields']['project_name']['label']['pattern']='Unknown'
            self.assertEqual(run_municipality(folder,manifest,definition,out)['records'],0)

    def test_archive_parent_is_kept_in_source_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Source('pack',zip_bytes({'sheet.pdf':self.source.data}),'zip',META)
            result,documents=process_source(source,MUNI,[self.template],Path(tmp))
        self.assertEqual(len(result['records']),1)
        self.assertEqual(documents[0]['doc_id'],'pack')
        self.assertEqual(documents[1]['parent_doc_id'],'pack')


class DefinitionTest(unittest.TestCase):
    def test_all_38_definitions_and_required_fields(self):
        self.assertEqual(len(load_templates()),38)

    def test_schema_keywords_are_supported_and_unknown_constraints_stop(self):
        check_schema_keywords(json.loads(SCHEMA_PATH.read_text(encoding='utf-8')))
        with self.assertRaises(ValidationError):check_schema_keywords({'unevaluatedProperties':False})

    def test_existing_common_format_examples(self):
        for path in (SCHEMA_PATH.parent/'examples').glob('*.json'):
            with self.subTest(path=path.name):validate(json.loads(path.read_text(encoding='utf-8')))


if __name__=='__main__':unittest.main()
