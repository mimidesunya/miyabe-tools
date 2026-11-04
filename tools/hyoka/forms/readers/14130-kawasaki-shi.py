"""川崎市の公開評価様式（HTML・重点課題表・事業個票）を読む。

1 事業が 2〜4 頁。「事務事業コード」の欄がある頁から次の事業が始まる。
金額は「予決算（単位：千円）」の表で、年度の見出し（R4年度…）の下に予算額、決算額、計画事業費の見出しがある。
"""

from __future__ import annotations

import re

from cells import Cell, Document, Page, marked, norm, western_year, number

STAGES = {"予算額": "budget", "決算額": "settlement", "決算額(見込)": "estimate", "計画事業費": "plan"}
SCOPES = (("事業費", "direct"), ("人件費", "personnel"), ("総コスト", "total"))
SOURCES = {"国庫支出金": "national", "県支出金": "prefecture", "市債": "bond", "その他特財": "other_specific", "一般財源": "general"}
DIRECTIONS = (("拡充", "expand"), ("縮小", "reduce"), ("廃止", "abolish"), ("休止", "suspend"), ("統合", "integrate"),
              ("完了", "complete"), ("改善", "improve"), ("見直し", "improve"), ("現状維持", "maintain"), ("継続", "continue"))
YEAR_HEAD = re.compile(r"^(?:[RH]\d+|令和(?:元|\d+)|平成\d+|20\d{2})年度$")


def values(*found: Cell | None) -> list[dict]:
    return [cell.value() for cell in found if cell is not None and cell.text]


def new_record(page: Page, head: Cell, evaluation_year: int | None) -> dict:
    period = [page.below(page.find(word)) for word in ("事業開始年度", "事業終了年度")]
    basis = page.right(page.find("実施根拠"))
    law = page.right(page.find("法令・要綱等"))
    return {
        "pages": [page.number],
        "evaluation_year": evaluation_year or 0,
        "target_year": 0,
        "project": {
            "code": values(page.below(head)),
            "name": values(page.below(page.find("事務事業名"))),
            "department": values(page.below(page.find("所属名"))),
            # 印の付いた選択肢は原文の升目ごと返す（原文は升目の文字の一部でなければならない）
            "legal_basis": values(law) + (values(basis) if basis and marked(basis.text) else []),
            "period": values(*period),
        },
        "amounts": [], "funding": [], "staff": [], "indicators": [], "evaluations": [], "reflection": [],
    }


def read_money(page: Page, record: dict) -> None:
    head = page.find("予決算")
    if head is None:
        return
    unit = re.search(r"単位[:：]\s*([^）)]+)", norm(head.text))
    unit_text = unit.group(1) if unit else ""
    table = page.table_cells(head.table)
    # 開始・終了年度は金額の列見出しではない。予決算付近の「年度」行に限定。
    year_label = next((c for c in table if norm(c.text) == '年度' and abs(c.r-head.r) <= 5), None)
    all_years = [c for c in table if re.match(r'^(?:[RH]\d+|令和(?:元|\d+)|平成\d+|20\d{2})年度', norm(c.text))]
    if year_label:
        years = [c for c in all_years if c.r == year_label.r]
    else:
        nearby = [c for c in all_years if head.r-5 <= c.r <= head.r+2]
        year_row = min((c.r for c in nearby), key=lambda r: abs(r-head.r), default=-1)
        years = [c for c in nearby if c.r == year_row]
    if not years:
        return
    year_row = years[0].r
    if any(re.search(r'予算額|決算額|計画事業費', y.text) for y in years):
        read_merged_money(page, record, head, years, unit_text)
        return
    kinds = {cell.c: cell for cell in table if cell.r == year_row + 1 and cell.text}
    kinds = {c: v for c, v in kinds.items() if any(y.c <= c < y.c+y.cs for y in years)}
    if not kinds:
        return
    first_value = min(kinds)

    def year_of(column: int) -> int | None:
        cell = next((y for y in years if y.c <= column < y.c + y.cs), None)
        return western_year(cell.text) if cell else None

    staff_label = next((c for c in table if c.r > year_row and norm(c.text).startswith('人工')), None)
    last_row = staff_label.r if staff_label else max((c.r for c in table if norm(c.text).startswith(('事業費', '人件費', '総コスト'))), default=year_row+1)
    for cell in sorted(table, key=lambda x: (x.r, x.c)):
        if cell.r > last_row or cell.r <= year_row + 1 or cell.c not in kinds or cell.c < first_value or not cell.text:
            continue
        labels = [x for x in page.row(cell) if x.c < first_value and x.text and norm(x.text) != "財源内訳"]
        if not labels:
            continue
        label = labels[-1]
        stage = STAGES.get(norm(kinds[cell.c].text), "unstated")
        name = norm(label.text)
        stages = re.findall(r'計画事業費|予算額|決算額(?:\(見込\))?', norm(kinds[cell.c].text))
        pieces = re.findall(r'[▲△−-]?\d[\d,]*(?:\.\d+)?|[－―—-]', cell.text)
        if len(stages) > 1 and len(pieces) == len(stages):
            entries = [(raw, STAGES.get(st, 'unstated')) for raw, st in zip(pieces, stages)]
        else:
            entries = [(cell.text, stage)]
        for raw, st in entries:
            row = {"label": label.text, "fiscal_year": year_of(cell.c), "raw": raw, "ref": cell.ref, "stage": st}
            if name.startswith("人工") or any(w in name for w in ('平均給与', '人件費単価')):
                record["staff"].append({**row, "unit": "人" if name.startswith('人工') else unit_text})
            elif name in SOURCES:
                record["funding"].append({**row, "source": SOURCES[name], "unit": unit_text})
            else:
                scope = next((value for word, value in SCOPES if name.startswith(word)), "breakdown")
                # 金額表の範囲を超えた目標、年度、本文を数字扱いしない。
                if number(raw) is None and norm(raw) not in ('-', '－', '―', '—', '#VALUE!', '#DIV/0!', '#REF!', '#N/A', '#NAME?', '#NUM!'):
                    continue
                record["amounts"].append({**row, "scope": scope, "unit": unit_text, "unit_source": head.text})



def read_merged_money(page, record, head, years, unit):
    """年度と段階が同じ升目に結合された表。各年度の数値の順番と個数を照合する。"""
    table = page.table_cells(head.table)
    first = min(y.c for y in years)
    year_row = years[0].r
    end = next((c.r for c in table if c.r > year_row and norm(c.text).startswith('人工')), year_row)
    for row in sorted({c.r for c in table if year_row < c.r <= end}):
        labels = [c for c in table if c.r == row and c.c < first and c.text and norm(c.text) != '財源内訳']
        if not labels:
            continue
        label = max(labels,key=lambda c:c.c)
        name = norm(label.text)
        staff = name.startswith('人工')
        scope = next((v for word,v in SCOPES if name.startswith(word)), None)
        source = SOURCES.get(name)
        if not (staff or scope or source):
            continue
        for y in years:
            found = sorted([c for c in table if c.r == row and y.c <= c.c < y.c+y.cs], key=lambda c:c.c)
            if staff:
                for c in found:
                    if c.text:
                        record['staff'].append({'label':label.text,'fiscal_year':western_year(y.text),**c.value(),'unit':'人'})
                continue
            stages = re.findall(r'計画事業費|予算額|決算額(?:\(見込\))?',norm(y.text))
            entries=[]
            for c in found:
                if not c.text:
                    entries.append((c,''))
                    continue
                parts = re.findall(r'#[A-Z0-9/!?]+|[▲△−-]?\d[\d,]*(?:\.\d+)?|[－―—-]',c.text)
                entries.extend((c,raw) for raw in parts)
            if len(entries) != len(stages):
                continue  # 空の結合欄等で対応が特定できなければ推定しない。
            for (c,raw),stage in zip(entries,stages):
                if not raw:
                    continue
                value={'label':label.text,'fiscal_year':western_year(y.text),'raw':raw,'ref':c.ref,
                       'stage':STAGES.get(stage,'unstated'),'unit':unit}
                if source:
                    record['funding'].append({**value,'source':source})
                else:
                    record['amounts'].append({**value,'scope':scope,'unit_source':head.text})


def read_indicators(page: Page, record: dict) -> None:
    head = page.find("指標分類")
    if head is None or record["indicators"]:
        return
    table = page.table_cells(head.table)
    years = {cell.c: western_year(cell.text) for cell in table if cell.r == head.r and YEAR_HEAD.match(norm(cell.text))}
    for cell in table:
        if cell.r <= head.r or "指標" not in cell.text or cell.rs < 2 or cell.cs != 1 or len(norm(cell.text)) > 6:
            continue
        span = [x for x in table if cell.r <= x.r < cell.r + cell.rs]
        name = page.right(cell)
        unit = next((x for x in table if x.r == cell.r and x.c >= 12 and x.rs >= 2), None)

        def by_year(word: str) -> dict[int, str]:
            label = next((x for x in span if norm(x.text) == word), None)
            return {years[x.c]: x for x in span if label and x.r == label.r and x.c in years}

        target, actual = by_year("目標"), by_year("実績")
        kind = "outcome" if "成果" in cell.text else "activity" if "活動" in cell.text else "unknown"
        record["indicators"].append({
            "name": name.text if name else "", "name_ref": name.ref if name else "", "kind_raw": cell.text, "kind": kind,
            "unit": unit.text if unit else "", "note": "",
            "values": [{"fiscal_year": year, "target_raw": target[year].text if year in target else "", "actual_raw": actual[year].text if year in actual else "", "rate_raw": "",
                        "target_ref": target[year].ref if year in target else "", "actual_ref": actual[year].ref if year in actual else ""}
                       for year in sorted(set(years.values()))],
        })


# 以下は様式の構造に基づく共通処理。文書IDや事業名による分岐はしない。
PROJECT_KEYS = ('name', 'name_budget', 'code', 'department', 'account', 'plan_alignment',
                'legal_basis', 'target', 'purpose', 'period')
ROMAN_DIRECTIONS = {'I': 'continue', 'II': 'improve', 'III': 'expand', 'IV': 'reduce',
                    'V': 'abolish', 'VI': 'complete'}


def empty_record(page, year=0):
    return {'pages': [page.number], 'evaluation_year': year or 0, 'target_year': 0,
            'project': {key: [] for key in PROJECT_KEYS},
            **{key: [] for key in ('amounts', 'funding', 'staff', 'indicators', 'evaluations', 'reflection')}}


def exact(page, text):
    return next((c for c in page.cells if norm(c.text) == norm(text)), None)


def page_year(page):
    # 文章中の過去年ではなく、表題や「評価年度」の明記だけを使う。
    for c in [*page.lines, *page.cells]:
        t = norm(c.text)
        if '評価年度' in t or (any(w in t for w in ('事務事業評価シート', '施策評価シート')) and len(t) < 70):
            y = western_year(t)
            if y:
                return y
    return 0


def selected_direction(raw):
    t = norm(raw)
    if not t:
        return 'none'
    if any(x in raw for x in '□☐☑■●◎◉✔✓'):
        chosen = marked(raw)
        if not chosen:
            return 'none'
        t = norm(' '.join(chosen))
    # 専用の回答欄に記入されたローマ数字。選択肢の列からは選ばない。
    if t in ROMAN_DIRECTIONS:
        return ROMAN_DIRECTIONS[t]
    if sum(word in t for word in ('継続', '改善', '拡大', '縮小', '廃止', '終了')) > 2:
        return 'none'
    for word, direction in (('拡大', 'expand'), ('終了', 'complete'), *DIRECTIONS):
        if word in t:
            return direction
    return 'other'


def evaluation(stage, rating=None, direction=None, reasons=(), aspects=()):
    raw = direction.text if direction else ''
    return {'stage_raw': stage, 'actor': '担当', 'status': 'done' if raw or (rating and rating.text) else 'blank',
            'rating_raw': rating.text if rating else '', 'direction_raw': raw,
            'direction': selected_direction(raw), 'reason_refs': [c.ref for c in reasons if c and c.text],
            'aspects': list(aspects)}


def read_evaluation(page, record, achieved=None):
    head = page.find(re.compile(r'^今後の方向性(?:\(|$)')) or exact(page, '今後の事業の方向性')
    if head is None:
        return
    answer = page.right(head)
    # 「方向性区分」が上段、候補一覧が下段にある様式。
    if answer and norm(answer.text) == '方向性区分':
        answer = page.below(answer)
    if answer and ('現状のまま継続' in norm(answer.text) and '事業廃止' in norm(answer.text)):
        answer = page.right(answer)
    rating = page.right(page.find('達成度')) or achieved
    aspects, reasons = [], []
    for question in page.find_all(re.compile(r'^【')):
        reply = page.right(question, 2)
        if reply:
            aspects.append({'aspect_raw': question.text.split('】')[0].lstrip('【'), 'rating_raw': reply.text, 'rating_ref': reply.ref})
    for label in page.cells:
        if norm(label.text) == '評価の理由':
            reasons.append(page.right(label))
    if answer:
        reasons.append(page.right(answer))
    ev = evaluation(head.text, rating, answer, reasons, aspects)
    ev['rating_ref'] = rating.ref if rating else ''
    ev['direction_ref'] = answer.ref if answer else ''
    record['evaluations'].append(ev)


def enrich_project(page, record):
    for key, labels in {
        'purpose': ('事業の目的', '目的'), 'target': ('事業の対象',),
        'plan_alignment': ('総合計画と連携する計画等',),
        'name_budget': ('予算上の事業名',), 'account': ('会計', '款', '項', '目'),
    }.items():
        for label in labels:
            c = exact(page, label) or next((x for x in page.cells if norm(x.text).startswith(norm(label)+'(')), None)
            if c:
                v = page.right(c)
                if v and v.text and v.text not in labels:
                    record['project'][key].extend(values(v))


def read_project_sheets(document):
    records, current = [], None
    year = 0
    achieved = None
    for page in document.pages:
        year = page_year(page) or year
        head = exact(page, '事務事業コード')
        # 個票は「所属名」「予決算」を持つ。施策内の事業行は別の関数へ。
        if head and page.find('予決算') and exact(page, '所属名'):
            name = page.below(exact(page, '事務事業名'))
            code = page.below(head)
            if not name or not name.text or not code or not re.fullmatch(r'\d{8}', norm(code.text)):
                current = None
                continue
            current = new_record(page, head, year)
            current['project'] = {**{key: [] for key in PROJECT_KEYS}, **current['project']}
            enrich_project(page, current)
            achieved = page.right(page.find('達成度'))
            # 実施結果（Do）等の明記された年度のみを対象年度とする。
            for c in page.cells:
                m = re.search(r'((?:[RH]\d+|令和(?:元|\d+)|平成\d+|20\d{2})年度).*?(?:実施結果|取組内容)', norm(c.text))
                if m and len(norm(c.text)) < 100:
                    current['target_year'] = western_year(m.group(1)) or 0
                    break
            records.append(current)
        elif current:
            # 個票の続き以外（施策、一覧、目次）は前事業に結合しない。
            if (page.find_line('施策評価シート') or page.find('施策の概要') or
                    not (page.find('評価の理由') or page.find(re.compile(r'^今後の方向性(?:\(|$)')) or
                         exact(page, '今後の事業の方向性') or page.find('指標分類'))):
                current = None
                continue
            current['pages'].append(page.number)
        if current:
            read_money(page, current)
            read_indicators(page, current)
            read_evaluation(page, current, achieved)
            read_reflection(page, current)
    return records


def read_html_blocks(document):
    records = []
    for page in document.pages:
        year = page_year(page)
        heads = [c for c in page.cells if norm(c.text) == '事務事業名']
        for i, head in enumerate(heads):
            name = page.right(head)
            # 一覧表は次の関数で行ごとに読む。
            if exact(page, '事務事業コード') or not name or not name.text or norm(name.text) in ('達成度', '達成状況', '重点', '所管課', '事務事業所管課', '事務事業名'):
                continue
            end = next((h.r for h in heads[i+1:] if h.table == head.table and h.r > head.r), 10**6)
            block = [c for c in page.table_cells(head.table) if head.r <= c.r < end]
            if not any(norm(c.text) == '事務事業の概要' for c in block):
                continue
            rec = empty_record(page, year)
            rec['project']['name'] = values(name)
            for label in block:
                if norm(label.text) in ('事務事業所管課', '所管課'):
                    rec['project']['department'] = values(page.right(label))
            rating = next((page.right(c) for c in block if norm(c.text) in ('達成度', '達成状況') or re.fullmatch(r'20\d{2}年度の達成状況', norm(c.text))), None)
            # 概要は目的だけでなく手法等を含むので purpose へ流用しない。
            years = [c for c in block if re.fullmatch(r'20\d{2}年度', norm(c.text))]
            targets = next((c for c in block if norm(c.text) == '事業目標'), None)
            actuals = next((c for c in block if norm(c.text) == '実績'), None)
            vals = []
            for y in years:
                target = next((c for c in block if targets and c.r == targets.r and c.c == y.c), None)
                actual = next((c for c in block if actuals and c.r == actuals.r and c.c == y.c), None)
                if (target and target.text) or (actual and actual.text):
                    vals.append({'fiscal_year': western_year(y.text), 'target_raw': target.text if target else '',
                                 'actual_raw': actual.text if actual else '', 'rate_raw': '',
                                 'target_ref': target.ref if target else '', 'actual_ref': actual.ref if actual else ''})
            if targets and vals:
                rec['indicators'].append({'name': targets.text, 'name_ref': targets.ref,
                    'kind_raw': targets.text, 'kind': 'unknown', 'unit': '',
                    'note': '事業全体の記述式目標・実績。数値指標への分割はしない。', 'values': vals})
            if not rating or not rating.text:
                # HTMLでは判定だけが次の小表に分かれる。
                following = page.table_cells(head.table + 1)
                check = next((c for c in following if norm(c.text) == '点検結果'), None)
                if check:
                    rating = next((c for c in following if c.c == 0 and c.text), rating)
            ev = evaluation('達成度・点検結果', rating)
            if rating:
                ev['rating_ref'] = rating.ref
            check_cells = page.table_cells(head.table + 1)
            check = next((c for c in check_cells if norm(c.text) == '点検結果'), None)
            if check:
                v = page.right(check)
                if v:
                    ev['aspects'].append({'aspect_raw': '点検結果', 'rating_raw': v.text, 'rating_ref': v.ref})
                    for legend in page.cells:
                        if norm(legend.text) != norm(v.text) or legend.table == check.table:
                            continue
                        above = page.above(legend)
                        while above and not any(w in above.text for w in ('予算', '人員', '事業終了', '事業見直し')):
                            above = page.above(above)
                        if above and any(w in above.text for w in ('予算', '人員', '事業終了', '事業見直し')):
                            ev['direction_raw'] = v.text
                            ev['direction'] = selected_direction(above.text)
                            ev['direction_ref'] = v.ref
                            ev['direction_legend_ref'] = above.ref
                            break
            rec['evaluations'].append(ev)
            # 根拠に明記された対象年度。目標期間から評価年度を推測しない。
            records.append(rec)
    return records


def read_html_lists(document):
    records = []
    for page in document.pages:
        for head in page.cells:
            if norm(head.text) != '事務事業名':
                continue
            rating_head = page.right(head)
            if not rating_head or not any(w in norm(rating_head.text) for w in ('達成度', '達成状況')):
                continue
            table = page.table_cells(head.table)
            for cell in table:
                if cell.c != head.c or cell.r <= head.r or not cell.text:
                    continue
                rating = page.right(cell)
                if not rating or not re.fullmatch(r'(?:[a-eA-E1-5－-]|I{1,3}|IV|V)', norm(rating.text)):
                    continue
                rec = empty_record(page, page_year(page))
                rec['project']['name'] = values(cell)
                ev = evaluation(rating_head.text, rating)
                ev['rating_ref'] = rating.ref
                rec['evaluations'].append(ev)
                records.append(rec)
    return records


def read_policy_rows(document):
    """施策表の事業行。個票が同一文書にある事業は個票を優先する。"""
    records = []
    for page in document.pages:
        if page.find('予決算'):
            continue
        head = exact(page, '事務事業コード')
        if not head:
            continue
        table = page.table_cells(head.table)
        year_cells = [c for c in table if re.match(r'^[RH]\d+年度', norm(c.text))]
        if not year_cells:
            continue
        name_head = exact(page, '事務事業名')
        budget_head = next((c for c in table if norm(c.text).startswith('予算額')), None)
        settlement_head = exact(page, '決算額')
        money_head = next((c for c in table if '事業費' in norm(c.text) and '千円' in c.text), None)
        if not all((name_head, budget_head, settlement_head, money_head)):
            continue
        if any('⑩事務事業の概要を記載' in norm(c.text) for c in table):
            continue
        year_cells = [c for c in year_cells if c.r >= head.r and c.c == budget_head.c]
        for code in table:
            if code.c != head.c or not re.fullmatch(r'\d{8}', norm(code.text)):
                continue
            name = page.below(code)
            if not name or not name.text:
                continue
            rec = empty_record(page, page_year(page))
            rec['project']['name'], rec['project']['code'] = values(name), values(code)
            for i, yh in enumerate(year_cells):
                for col, stage in ((budget_head.c, 'unstated' if '計画事業費' in budget_head.text else 'budget'), (settlement_head.c, 'settlement')):
                    cell = next((c for c in table if c.r == code.r+i and c.c == col), None)
                    if cell and cell.text:
                        rec['amounts'].append({'label': money_head.text, 'fiscal_year': western_year(yh.text),
                            **cell.value(), 'stage': 'estimate' if stage == 'settlement' and '見込' in yh.text else stage,
                            'scope': 'direct', 'unit': '千円', 'unit_source': money_head.text})
            if not rec['evaluation_year']:
                # 表題に年度の無い施策表の頁。決算が見込の年度が評価した年度
                rec['evaluation_year'] = next((western_year(yh.text) for yh in year_cells if '見込' in yh.text), 0) or 0
            dh = exact(page, '今後の事業の方向性')
            if dh:
                rh = exact(page, '事業の達成度')
                ch = next((c for c in table if '貢献' in norm(c.text) and c.r < code.r), None)

                def at(head, row):
                    return next((c for c in table if head and c.c == head.c and c.r <= row < c.r + c.rs), None)

                # 令和の施策表は年度ごとに達成度、貢献度、方向性の行がある（平成29年度は方向性が事業に 1 つ）。
                # 最初の年度の行だけを読むと、評価した年度と違う年度の判定になる。年度ごとの評価を古い順に並べる
                for i, yh in enumerate(year_cells):
                    rc, cc, dc = at(rh, code.r + i), at(ch, code.r + i), at(dh, code.r + i)
                    if all(norm(cell.text if cell else '') in ('', '－', '-', '―') for cell in (rc, cc, dc)):
                        continue
                    aspects = [{'aspect_raw': ch.text, 'rating_raw': cc.text}] if cc and cc.text and ch else []
                    rec['evaluations'].append(evaluation(f'{norm(yh.text)} {dh.text}', rc, dc, aspects=aspects))
            records.append(rec)
    return records


def read_priority_rows(document):
    """平成26～27年度。ヘッダのない次頁も予算・決算行の構造で読む。"""
    records = []
    active = False
    goal_headers = {}
    for page in document.pages:
        if page.find_line('帳票イメージ'):
            continue
        for ti in sorted({c.table for c in page.cells}):
            table = page.table_cells(ti)
            head = next((c for c in table if norm(c.text) == '事務事業名・予決算額'), None)
            if head:
                active = True
                goal_headers = {c.c:c for c in table if norm(c.text)=='取組内容・目標'}
            if not active:
                continue
            budgets = [c for c in table if norm(c.text) == '予算' and c.c == 0]
            for budget in budgets:
                cell = next((c for c in table if c.r == budget.r-1 and c.c == 0 and 2 <= c.cs <= 3), None)
                if not cell or not cell.text or norm(cell.text) == '事務事業名・予決算額':
                    continue
                rec = empty_record(page)
                rec['project']['name'] = values(cell)
                rows = [c for c in table if cell.r <= c.r < cell.r+3]
                for c in rows:
                    if norm(c.text) not in ('予算', '決算', '決算見込'):
                        continue
                    v = page.right(c)
                    if v and v.text:
                        unit = re.search(r'(千円|百万円|万円|円)', v.text)
                        raw_number = re.search(r'[▲△−-]?\d[\d,]*(?:\.\d+)?|[－―—-]', v.text)
                        if not raw_number:
                            continue
                        rec['amounts'].append({'label': c.text, 'fiscal_year': 0, 'raw':raw_number.group(), 'ref':v.ref,
                          'stage': {'予算':'budget','決算':'settlement','決算見込':'estimate'}[norm(c.text)],
                          'scope':'direct','unit': unit.group(1) if unit else '', 'unit_source': v.text if unit else ''})
                ratings = sorted([c for c in rows if c.r == cell.r and c.c >= cell.cs and re.fullmatch(r'[1-5]', norm(c.text))], key=lambda c:c.c)
                for ix, rc in enumerate(ratings):
                    col = rc.c
                    reason = page.left(rc)
                    if rc:
                        ev = evaluation('前半' if ix==0 and len(ratings)>1 else '1年間', rc, reasons=[reason])
                        ev['rating_ref'] = rc.ref
                        rec['evaluations'].append(ev)
                        goal = page.left(reason)
                        gh = goal_headers.get(goal.c) if goal else None
                        if gh and goal and reason and (goal.text or reason.text):
                            rec['indicators'].append({'name':gh.text, 'name_ref':gh.ref,
                                'kind_raw':gh.text, 'kind':'unknown', 'unit':'',
                                'note':ev['stage_raw']+'の記述式取組目標・実績。数値指標には分割しない。',
                                'values':[{'fiscal_year':0, 'target_raw':goal.text, 'actual_raw':reason.text,
                                           'rate_raw':'', 'target_ref':goal.ref, 'actual_ref':reason.ref}]})
                records.append(rec)
    return records


def read_reflection(page, record):
    for c in page.cells:
        if norm(c.text) in ('変更後(上記計画上の記載に対する変更点)', '変更後'):
            v = page.right(c)
            if v and v.text:
                raw = v.text
                kind = next((k for word, k in (('増額','increase'),('減額','decrease'),('新規','new'),('変更なし','none')) if word in raw), 'unknown')
                record['reflection'].append({'fiscal_year':0, 'kind':kind, **v.value(), 'unit':'', 'change_raw':''})


def read(document: Document) -> list[dict]:
    sheets = read_project_sheets(document)
    rows = read_policy_rows(document)
    document_year = next((page_year(p) for p in document.pages if page_year(p)), 0)
    for r in rows:
        r['evaluation_year'] = r['evaluation_year'] or document_year
    codes = {norm(v['raw']) for r in sheets for v in r['project']['code']}
    rows = [r for r in rows if not any(norm(v['raw']) in codes for v in r['project']['code'])]
    return sheets + rows + read_priority_rows(document) + read_html_blocks(document) + read_html_lists(document)
