# 事務事業評価の実地調査

自治体ごとに、事業ごとの評価が公開されているか、どの年度のどの頁にあるか、本当に事務事業評価かを、公式サイトの実物を開いて確かめる調査。

`discover_hyoka_urls.py` はページの言葉で機械的に判定して、取り違えが多かった（[hyoka-url-survey.md](hyoka-url-survey.md)）。文書 125,101 件のうち、予算書、財務書類、案内図、事業名の一覧が「事務事業評価」に分類され、本物の川崎市や浜松市が未確認のまま残っていた。この調査はその台帳の作り直しである。

## 土台

総務省「地方行政サービス改革の取組状況等に関する調査」の団体別の個票（令和7年4月1日現在）に、事務事業の行政評価の導入と公表の自治体の回答がある。全国の実施状況をまとめたオンブズマンや研究機関の一覧は見つからなかった。

- 出典: <https://www.soumu.go.jp/iken/02gyosei07_00006.html>
- 団体ごとの一覧: `data/municipalities/hyoka_census_mic.tsv`（1,788 団体と、対象外の北方領土 6 村）

個票のコードは 7 行が 5 桁で、千代田区は `138016`、武雄市は `41205` と誤っていたので名前で直した。

自己申告なので順番を決めるだけに使う。「廃止」でも過去の公開物が残り、「全て公表」でも見つからないことがあるので、団体を飛ばさない。

## 調べ方

定義と結果の形は `tools/hyoka/survey/brief.md`。担当（Codex）が公式サイトを開いて判定する。

```bash
python tools/hyoka/survey/run_survey.py plan --work WORK --batch 1
python tools/hyoka/survey/run_survey.py run --work WORK --parallel 8 --effort low --model gpt-6.1-sol --max-quota 96
python tools/hyoka/survey/run_survey.py status --work WORK
python tools/hyoka/survey/run_survey.py verify --work WORK
python tools/hyoka/survey/run_survey.py merge --work WORK --out survey.jsonl
```

`run` は残りの団体だけを頼むので、止まっても流し直せる。束が 3 回続けて失敗するか、週の枠が `--max-quota` に達すると新しい束を始める前に止まる。枠は `codex app-server` に尋ねる。`verify` は結果にある URL を実際に開く（`--parallel` はサイトの数で、同じサイトは `--sleep` の間を空けて順に開く）。

## 調べ直し

全量のあと、目的の違う 2 つを別の作業の置き場で流す。

- **見落としの確認**: 総務省の回答が「導入済み・公表あり」か、以前の収集に文書があるのに、事業の情報が見つからなかった団体。前回の結果を添え、別の道で探させる。

  ```bash
  python tools/hyoka/survey/run_survey.py plan --work WORK2 --batch 1 --codes codes.txt --previous survey.jsonl
  python tools/hyoka/survey/run_survey.py run --work WORK2 --parallel 8 --effort medium --model gpt-6.1-sol --preface tools/hyoka/survey/preface_missing.md
  ```

- **独立の抜き取り**: 無作為の団体を、前回の結果を見せずに上位のモデルで調べて突き合わせ、全体の誤りの割合を見る。`--previous` と `--preface` を付けない。

## モデルの選び方

人が実物で確かめた難しい 8 団体で比べた（2026-11-02、依頼書 3 版、1 団体 1 束）。

| 設定 | 判定の粒度が届いた | 実物に届いた | 1 団体のトークン | 週の枠 |
|---|---|---|---|---|
| `gpt-6-astra` medium | 8/8 | 8/8 | 305 万 | 16 団体で 12% |
| `gpt-6.1-sol` low | 8/8 | 8/8 | 259 万 | 8 団体で 1% 前後 |
| `gpt-6.1-terra` medium | 5/8 | 8/8 | 68 万 | 8 団体で 1% 未満 |
| `gpt-6-luna` high | 4/8 | 7/8 | 177 万 | 8 団体で 1% 未満 |
| `gpt-6-luna` medium | 2/8 | 4/8 | 59 万 | 16 団体で 1% 前後 |

安いモデルは「評価なし」と誤る。大仙市は人も含めて 3 回「市民アンケートのみ」と判定したが、Astra と Sol が予算説明書の中の事業ごとの評価（平成24年度〜令和5年度）を見つけた。漏れは収集の失敗に直結するので `gpt-6.1-sol` を使う。束を大きくしても安くならない（4 団体 1 束で 214 万、1 団体 1 束で 325 万だが、後者のほうが深く調べる）。

## 誤りの防ぎ方

試しで、Codex と Claude に同じ 16 自治体を別々に調べさせて突き合わせた。

- 判定の一致は 10/16。割れた分は依頼書の定義の穴（教育委員会の点検評価、1 事業 1 行の一覧）だった。
- 同じ PDF を読んでも一方が欄を見落とした（熊本市）。今の評価を一方だけが見つけ（浜松市）、古い一覧を他方だけが見つけた（土佐清水市）。
- 1 回の調査では漏れる。総務省の回答と食い違う団体、`confidence: low`、無作為の抜き取りは別の担当が調べ直す。

## 判定の決まり

- 名前でなく中身で判定する。
- 金額の欄がないシートも事業シートである。欄の有無は `forms[].fields` に書く。
- 教育委員会の点検評価は法定の別の制度なので別枠にする（`other_sources`）。
- 様式は年度で変わるので、`forms` は様式ごとに分ける。
