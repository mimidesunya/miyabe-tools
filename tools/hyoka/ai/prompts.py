"""AI に渡す指示文と答えの形。

書き写しの指示文は抜き取り 49 文書の試し（2026-11-01）で直した第 2 版に、欄名（labels）を足したもの。
凡例の指示文は、6 自治体の試しで「凡例が書き写しの定義と違う解釈を書いて悪化した」ことを受けた第 2 版。
"""

from __future__ import annotations

PROMPT_VERSION = "extract-v2.1"
LEGEND_VERSION = "legend-v2"
FIELDS = ("project_name", "department", "future_direction", "evaluation_rating", "purpose", "year_text")

DIRECTION_RULES = """   - future_direction: 「今後の方向性」「今後の方針」「事業の方向性」「来年度以降の方向性」など、事業を今後どうするかを示す欄の値。
     - 区分（継続・拡充・改善・縮小・廃止・見直しなど）を選ぶ様式なら、選ばれた区分の文字だけを書く。「①計画通りに進める」「現状どおり継続」のように原典の書き方のままにし、選択の印（●○◎■□☑✓など）は書かない。
     - 区分が無く文章だけの欄なら、その文章を書く。
     - そのような欄が無く、「総合評価」「判定」などの欄の区分が今後の扱い（継続・拡充・縮小・廃止・見直し・計画通りなど）を表すときは、その区分を書く。
     - 選択肢が並んでいるだけで、どれが選ばれたかが印や記入から文字として読み取れないときは空にする。選択肢の中から推測で一つ選ばない。
     - 「新規・継続の別」（事業が新しいか前からか）や、妥当性・有効性・効率性などの観点ごとの評価は、方向性ではない。"""

SYSTEM = f"""あなたは日本の自治体の事務事業評価シートから、事業ごとの記録を書き写す係です。
入力は PDF の頁ごとの表を升目の一覧にしたものです。各行は「行,列[+行の幅x列の幅]: 升目の文字」で、表の外の文字は「outside tables」の下に「[上からの位置,左からの位置] 文字」で並びます。●○■□☑☐◯ は PDF の図形や画像の印を字にしたものです。

することは次のとおりです。
1. document_kind を決める。事業ごとの評価の記録（事務事業評価・事業評価・補助金評価など、1 件ずつ事業名と評価がある）なら project_evaluation、施策・政策単位の評価なら policy_evaluation、それ以外（表紙・目次・要領・計画・一覧の説明など）なら other。
2. document_kind に関わらず、この入力に事業ごとの記録（事業名と、その事業の評価・方向性・目的など）が載っていれば、1 件ずつ records に入れる。施策評価の中に事業ごとの評価が並んでいるときも入れる。事業の記録が無ければ records を空にする。1 つの事業が複数頁にまたがるときは 1 件にまとめる。
3. 各欄は原典の文字をそのまま書き写す。言い換え・要約・補い・表記の直し（全角半角・送り仮名・改行の位置以外）をしない。1 つの値が隣り合う升目に分かれているときは、空白 1 つでつないでよい。
   - page: その事業が載っている頁番号（入力の「=== 頁 N ===」の N）。複数頁にまたがるなら最初の頁。
   - project_name: 事業名（事務事業名・予算事業名など）。番号や会計・款項目は含めない。
   - department: 担当部署（部・課・係などをそのまま。複数の升目なら空白でつなぐ）。
{DIRECTION_RULES}
   - evaluation_rating: 評価の区分（A・B・C、要改善、達成度の記号など）。方向性と同じ欄の値でもよい。無ければ空。
   - purpose: 事業の目的の文。無ければ空。
   - year_text: 評価年度・対象年度の記載（表題にあればそれ）。無ければ空。
   - labels: 各値を書き写した欄の、原典の欄名（「事務事業名」「担当課」「今後の方向性」「総合評価」など）。表題から取った年度など欄名が無いもの、値が空のものは空。
4. 原典に無い値は必ず空文字にする。推測で埋めない。文字化けして読めない文字は書き写さず、その欄を空にする。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "document_kind": {"type": "string", "enum": ["project_evaluation", "policy_evaluation", "other"]},
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer"},
                    **{key: {"type": "string"} for key in FIELDS},
                    "labels": {
                        "type": "object",
                        "properties": {key: {"type": "string"} for key in FIELDS},
                        "required": list(FIELDS),
                        "additionalProperties": False,
                    },
                },
                "required": ["page", *FIELDS, "labels"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["document_kind", "records"],
    "additionalProperties": False,
}

LEGEND_SYSTEM = f"""あなたは日本の自治体の事務事業評価シートの様式を調べ、書き写し係のための「凡例」を書く係です。
入力は同じ自治体の見本の文書で、PDF の頁ごとの表を升目の一覧にしたもの（各行「行,列[+行の幅x列の幅]: 升目の文字」、表の外の文字は「[上からの位置,左からの位置] 文字」）と、頁の画像です。
升目の一覧の ●○■□☑☐◯ は、PDF の図形や画像の印を字にしたものです。

書き写し係は、同じ自治体の別の文書を升目の一覧だけで読み、事業ごとに 事業名・担当部署・今後の方向性・評価区分・目的・年度 を書き写します。
その係が迷わないように、次の見出しで凡例を書いてください。見本で確かめられることだけを書き、推測で書かない。全体で 1,200 字以内。
1. 様式の見分け方（表題や特徴的な欄名。年度で様式が違えばそれぞれ）
2. 記録の単位（1 事業が何頁か、1 頁に何事業か、表紙・目次・説明など記録でない頁の見分け方）
3. 各項目の欄名と場所（事業名・担当部署・今後の方向性・評価区分・目的・年度。升目の一覧でどう現れるか）
4. 今後の方向性の選び方（選択肢の並び、選ばれた印の現れ方。印が文字に出ず画像でしか分からないなら、そう書く）
5. 紛らわしい点（似た欄名、方向性ではない欄、評価区分と方向性の区別など）
direction_options には、今後の方向性の選択肢を見本の書き方のまま挙げる（文章で書く欄なら空）。rating_options には評価区分の選択肢を挙げる（無ければ空）。

書き写し係は次の定義で各項目を書き写します。凡例は、この定義に従ったときにこの様式ではどの欄から取るかを決めて書いてください。定義と違う解釈を勧めない。
{DIRECTION_RULES}
- 評価の段階が複数（一次・二次・最終など）あるときは、最終の段階を使う。どれが最終かを書く。
- 選択肢が番号や記号の記入で選ばれる様式（例: 「1 現状で継続 2 見直しで継続」の横に「1」と記入）なら、記入された番号と選択肢の対応を書く。
- 評価区分: 評価の区分（A・B・C、要改善、達成度の記号など）。"""

LEGEND_SCHEMA = {
    "type": "object",
    "properties": {
        "legend": {"type": "string"},
        "direction_options": {"type": "array", "items": {"type": "string"}},
        "rating_options": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["legend", "direction_options", "rating_options"],
    "additionalProperties": False,
}


def system_with_legend(legend: dict | None) -> str:
    """凡例があれば書き写しの指示文に足す。"""
    if not legend or not legend.get("legend"):
        return SYSTEM
    text = (SYSTEM + "\n\n## この自治体の様式の凡例\n同じ自治体の別の文書を調べて作った凡例です。欄の場所や印の読み方の手がかりにする。"
            "入力の文字と食い違うときは入力を優先し、凡例を理由に入力に無い値を書かない。\n" + legend["legend"])
    if legend.get("direction_options"):
        text += "\n今後の方向性の選択肢（この書き方で書く）: " + " / ".join(legend["direction_options"])
    if legend.get("rating_options"):
        text += "\n評価区分の選択肢: " + " / ".join(legend["rating_options"])
    return text
