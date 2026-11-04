"""形 2.0 の検証の層を AI に取らせる（doc/hyoka-format-2.md）。

AI には番地の付いた一覧を渡し、値ごとに番地と短い原文を返させる。番地が実在し、原文がその升目の中に
あることを機械で確かめるので、書き写しの誤りをここで落とせる。長い文章は番地だけを返させ、文字は升目から写す。
"""

from __future__ import annotations

import re
import unicodedata

PROMPT_VERSION = "format2-v3"

SYSTEM = """あなたは日本の自治体の事務事業評価シートを読み、税金の使われ方を検証するための値を取り出す係です。
入力はシートの頁の一覧です。各行は「番地: 文字」で、番地は頁・表・行・列（p11t1r3c4）か、表の外の文字（p11l5、[上からの位置,左からの位置] 付き）です。
表を読み取れない頁は「[上からの位置] 番地=文字@左からの位置 | 番地=文字@左からの位置」の形で、同じ高さの文字が 1 行に並びます。表の列は @ の位置で対応させます。値の列の見出しは、上の行で @ が最も近いものです。
●○◎■□☑☐ は PDF の図形や画像の印を字にしたものです。「－」は値が無いことを示す埋め草です。

事業ごとに records に 1 件入れます。値はすべて、番地（ref）と原文（raw）の組で返します。原文はその番地の文字からそのまま抜き出し、言い換えません。

金額（amounts）: シートの金額の表の数字を 1 つずつ全部入れます。
- label: 原文の欄名
- fiscal_year: 西暦の年度。「R6」「令和6」「6年度」「05年度」「H28」などは、シートの年度から考えて西暦にします
- stage: budget（予算）、settlement（決算、実績）、estimate（見込。「決算見込」「決算(見込)」も見込です）、plan（計画、目標、予定）。表に書かれていなければ unstated。推測しません
- scope: direct（人件費を含まない事業費）、personnel（人件費。常勤、非常勤などの内訳も）、total（人件費を含む総費用）、sub_project（細事業の費用）、breakdown（費目などの内訳）
- 「事業費」という語を信用しません。合計の式や欄の並びから、人件費が入るかを判断します。人件費の欄が無ければ direct です
- 平均給与、人件費単価は金額ではなく staff に入れます
- unit: 単位。表の見出し、欄名、表の外の「（単位：千円）」などから、その表の単位を読みます。読めなければ空にします。千円と決めつけません
- unit_source: 単位をどこから読んだか（原文）
- 空欄、斜線、未到来の年度の埋め草の 0 は入れません。「－」と書かれた金額は raw を「－」にして入れます

財源（funding）: 国、県、地方債、基金、受益者負担、その他、一般財源の数字を年度ごとに入れます。source は national、prefecture、bond、fund、user_fee、other_specific、general のどれか。0 と書かれたものも入れます。

人員（staff）: 人数、人工、平均給与、人件費単価を年度ごとに入れます。

指標（indicators）: 目標と実績のある指標を全部入れます。kind は表の見出しや欄の書き方に従い、target（対象）、activity（活動、業績、事業量）、outcome（成果）、unknown のどれか。kind_raw に原文を残します。values に年度ごとの target_raw、actual_raw、rate_raw（原典の達成率）を入れます。想定、計画と書かれた値は target です。実績と見込が同じ列に並ぶ様式では、実績と見込を actual_raw に入れ、note に書きます。空は空文字です。
1 つの升目に複数の指標の値が重なっているときは（「40.22 91.15 0. 0.」）、指標名の順に対応させます。

評価（evaluations）: 評価の段階ごとに 1 件入れます。自己評価、一次、二次、課長、部長、外部、市民、首長、行革担当など、シートの全部の段階です。必要性や有効性などの観点は、段階を分けず、その段階の aspects に入れます。欄があるのに記入が無い段階も status を blank にして入れます。段階の中の一部だけ記入されていれば partial です。
- actor: 担当、課長、部長、行革担当、首長、外部、市民、議会、その他
- direction: expand（拡充）、continue（継続）、maintain（現状維持）、improve（改善、見直し）、reduce（縮小）、integrate（統合）、suspend（休止）、abolish（廃止）、complete（完了）、other、none。「継続（現状維持）」は maintain、「継続（見直し）」は improve です
- 選択肢は、印の付いたものだけを読みます。■☑◎● が付いたものが選ばれたものです。□☐ は選ばれていません
- **印が 1 つも見えない選択肢の並びからは選びません。** 先頭の選択肢を選ばず、direction_raw を空に、direction を none にします
- 方向性の欄が文章と区分に分かれていれば、区分を direction_raw に、文章の番地を reason_refs に入れます
- reason_refs: 理由の文章の番地
- aspects: 必要性、有効性などの観点ごとの評価

反映（reflection）: 評価の結果を翌年度の予算や事業にどう反映したか。文中の「令和8年度当初予算 7,524千円、前年度比 3,762千円 拡充」のような記述も入れます。kind は increase、decrease、new、none、unknown。

事業の識別（project）: 各項目は番地の配列です。無ければ空の配列にします。文字ではなく番地を返します。department は部、課、係の全部の番地です。

evaluation_year はシートの表題の評価の年度（西暦）、target_year は評価の対象になった事業の実施年度（西暦）です。書かれていなければ 0 にします。

原典に無い値は入れません。推測で埋めません。"""


def text_field(description: str = "") -> dict:
    return {"type": "string", "description": description} if description else {"type": "string"}


def refs_field() -> dict:
    return {"type": "array", "items": {"type": "string"}}


def obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def enum(*values: str) -> dict:
    return {"type": "string", "enum": list(values)}


VALUE = {"label": text_field(), "fiscal_year": {"type": "integer"}, "raw": text_field(), "ref": text_field()}
SCHEMA = obj({"records": {"type": "array", "items": obj({
    "project": obj({key: refs_field() for key in (
        "name", "code", "department", "account", "plan_alignment", "legal_basis", "target", "purpose", "period")}),
    "evaluation_year": {"type": "integer"},
    "target_year": {"type": "integer"},
    "amounts": {"type": "array", "items": obj({
        **VALUE,
        "stage": enum("budget", "settlement", "estimate", "plan", "unstated"),
        "scope": enum("direct", "personnel", "total", "sub_project", "breakdown"),
        "unit": text_field(), "unit_source": text_field()})},
    "funding": {"type": "array", "items": obj({
        **VALUE,
        "stage": enum("budget", "settlement", "estimate", "plan", "unstated"),
        "source": enum("national", "prefecture", "bond", "fund", "user_fee", "other_specific", "general"),
        "unit": text_field()})},
    "staff": {"type": "array", "items": obj({**VALUE, "unit": text_field()})},
    "indicators": {"type": "array", "items": obj({
        "name": text_field(), "name_ref": text_field(), "kind_raw": text_field(),
        "kind": enum("target", "activity", "outcome", "unknown"), "unit": text_field(), "note": text_field(),
        "values": {"type": "array", "items": obj({
            "fiscal_year": {"type": "integer"}, "target_raw": text_field(), "actual_raw": text_field(), "rate_raw": text_field()})}})},
    "evaluations": {"type": "array", "items": obj({
        "stage_raw": text_field(),
        "actor": enum("担当", "課長", "部長", "行革担当", "首長", "外部", "市民", "議会", "その他"),
        "status": enum("done", "blank", "partial"),
        "rating_raw": text_field(), "direction_raw": text_field(),
        "direction": enum("expand", "continue", "maintain", "improve", "reduce", "integrate", "suspend", "abolish", "complete", "other", "none"),
        "reason_refs": refs_field(),
        "aspects": {"type": "array", "items": obj({"aspect_raw": text_field(), "rating_raw": text_field()})}})},
    "reflection": {"type": "array", "items": obj({
        "fiscal_year": {"type": "integer"}, "kind": enum("increase", "decrease", "new", "none", "unknown"),
        "raw": text_field(), "unit": text_field(), "change_raw": text_field(), "ref": text_field()})},
})}})


def norm(text: object) -> str:
    """比べるための形。全角半角、空白、桁の区切りの違いを消す。"""
    return re.sub(r"[\s,]", "", unicodedata.normalize("NFKC", str(text or "")))


def canvas_rows(items: list[dict], tolerance: float = 0.8) -> list[list[dict]]:
    """位置の付いた文字を、同じ高さの行にまとめる（左から右へ）。"""
    rows: list[list[dict]] = []
    for item in sorted(items, key=lambda i: (i["t"], i["l"])):
        if rows and item["t"] - rows[-1][0]["t"] <= tolerance:
            rows[-1].append(item)
        else:
            rows.append([item])
    return [sorted(row, key=lambda i: i["l"]) for row in rows]


def address_pages(pages: list[dict]) -> tuple[str, dict[str, str]]:
    """頁の構造を、番地の付いた一覧と、番地から文字を引く表にする。"""
    lines: list[str] = []
    cells: dict[str, str] = {}

    def add(ref: str, text: str, note: str = "") -> None:
        cells[ref] = text
        lines.append(f"{ref}{note}: {text}")

    for page in pages:
        number = page.get("page")
        lines.append(f"=== 頁 {number} ===")
        if page.get("image_only") or page.get("garbled"):
            lines.append("（この頁は文字を読めない）")
            continue
        if "plain" in page:
            add(f"p{number}l0", page["plain"])
            continue
        if page.get("canvas"):  # 表を読み取れない頁は、同じ高さの文字を 1 行にして列の並びが見えるように渡す
            index = 0
            for row in canvas_rows(page["canvas"]):
                parts = []
                for item in row:
                    ref = f"p{number}l{index}"
                    cells[ref] = item["x"]
                    parts.append(f"{ref}={item['x']}@{item['l']:.0f}")
                    index += 1
                lines.append(f"[{row[0]['t']:.0f}] " + " | ".join(parts))
            continue
        for ti, table in enumerate(page.get("tables") or [], 1):
            for cell in table["cells"]:
                if cell["text"]:
                    span = f" ({cell['rs']}x{cell['cs']})" if (cell["rs"], cell["cs"]) != (1, 1) else ""
                    add(f"p{number}t{ti}r{cell['r']}c{cell['c']}", cell["text"], span)
        for index, line in enumerate(page.get("lines") or []):
            add(f"p{number}l{index}", line["text"], f" [{line['y']:02d},{line['x']:02d}]")
        for index, text in enumerate(page.get("vertical") or []):
            add(f"p{number}v{index}", text)
    return "\n".join(lines), cells


def resolve(record: dict, cells: dict[str, str]) -> dict:
    """番地を確かめ、文字を升目から写す。返すのは確かめた記録と、落とした値の一覧。"""
    problems: list[str] = []

    def kept(rows: list[dict], kind: str) -> list[dict]:
        out = []
        for row in rows:
            source = cells.get(row.get("ref") or "")
            if source is None:
                problems.append(f"{kind}: 番地が無い {row.get('ref')} {row.get('raw')}")
            elif norm(row.get("raw")) not in norm(source):
                problems.append(f"{kind}: 値が升目に無い {row.get('ref')} {row.get('raw')}")
            else:
                out.append(row)
        return out

    def copied(refs: list[str], kind: str) -> str:
        parts = []
        for ref in refs:
            if ref in cells:
                parts.append(cells[ref])
            else:
                problems.append(f"{kind}: 番地が無い {ref}")
        return " ".join(parts)

    result = dict(record)
    result["project"] = {key: copied(refs, key) for key, refs in record.get("project", {}).items()}
    for key in ("amounts", "funding", "staff", "reflection"):
        result[key] = kept([r for r in record.get(key) or [] if r.get("ref") or key != "reflection"], key)
    result["evaluations"] = [
        {**row, "reason": copied(row.get("reason_refs") or [], "評価の理由")} for row in record.get("evaluations") or []
    ]
    return {"record": result, "problems": problems}
