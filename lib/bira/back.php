<?php
declare(strict_types=1);

// 政治活動ビラ（裏面）の組み立てとAI生成。
//
// 裏面は市ごとに事実が違うので、本文はAIに書かせる。ただしページ全体を
// 任せることはしない。ヘッダーと発行者・連絡先はこちらで組み立て、AIには
// 本文エリアの中身だけを書かせる。発行者表示の書き換え忘れは実害が出ると
// 依頼書にあり、そこをAIの出力任せにはできないため。

require_once __DIR__ . DIRECTORY_SEPARATOR . 'runtime.php';

const BIRA_BACK_TEXT_FIELDS = [
    '大見出し' => 60,
    '発行者' => 200,
    '氏名' => 40,
    '住所' => 120,
    '電話' => 40,
    'メール' => 80,
    'ＳＮＳ' => 120,
    '主張したいこと' => 4000,
    'レイアウトの希望' => 1000,
    '根拠資料' => 20000,
];

/** 川崎版の初期値。指示書の原型がそのまま入る。 */
function bira_back_defaults(): array
{
    return [
        '地域' => '川崎',
        '紙名' => '誇れる川崎',
        '号数' => '第1号',
        '年月' => app_now_tokyo('Y年') . (int)app_now_tokyo('n') . '月',
        '大見出し' => '川崎で、これをやります',
        '発行者' => '神奈川県人権啓発センター（宮部龍彦 資金管理団体）',
        '氏名' => '宮部　龍彦',
        '住所' => '〒210-0802　川崎市川崎区大師駅前1-3-11　第2松坂荘101号',
        '電話' => '080-1442-9144',
        'メール' => 'tatsuhiko@miya.be',
        'ＳＮＳ' => 'X @K_JINKEN　youtube.com/@buraku',
        '主張したいこと' => implode("\n", [
            '1. 法人市民税の超過課税をなくす。標準税率は6.0％だが、川崎市は資本金5億円以上10億円未満で7.2％、10億円以上で8.4％を課している。',
            '2. 理念の押し付けをやめさせる。人権・男女共同参画・SDGs・多文化共生の7事業で、令和6年度予算は事業費と人件費の合計 約3億7,900万円。',
            '3. 教育の中立化をすすめる。市の人権尊重教育推進事業は研修や研究推進校への研究支援を通じて教職員の意識と指導力の向上を図るとしている。',
            '4. 外国人参政権の推進をやめさせる。市は外国人への地方参政権について、国に働きかけることを検討すると指針に書いている。',
        ]),
        '根拠資料' => implode("\n", [
            '法人税割の標準税率は6.0％。川崎市は資本金5億円以上10億円未満で7.2％、10億円以上で8.4％を課しています（令和元年10月1日以後に開始する事業年度・川崎市）。',
            '人権・男女共同参画・SDGs・多文化共生の7事業で、令和6年度予算は事業費と人件費の合計 約3億7,900万円（当方が事務事業評価から集計）。',
            '市の人権尊重教育推進事業は「研修や研究推進校・実践推進校等への研究支援」を通じて教職員の意識と指導力の向上を図るとしています（令和6年度事務事業評価）。',
            '「地方参政権の実現については、他の自治体と連携しながら国に働きかけることを検討します」——川崎市多文化共生社会推進指針（令和6年3月改定）24頁。',
        ]),
        'レイアウトの希望' => '赤枠の導入文を上に置き、その下に番号つきの約束を4つ並べる。各約束は見出し・説明・根拠の3行。',
    ];
}

/** 川崎版の完成ページ。AIへの見本であり、AIを呼べないときの代替でもある。 */
function bira_back_sample_html(): string
{
    $html = @file_get_contents(bira_dir() . DIRECTORY_SEPARATOR . '裏_見本.html');
    return is_string($html) ? $html : '';
}

function bira_back_validate(array $input): array
{
    $errors = [];
    foreach (['大見出し', '発行者', '氏名'] as $required) {
        if (trim((string)($input[$required] ?? '')) === '') {
            $errors[$required] = '入力してください。';
        }
    }
    foreach (BIRA_BACK_TEXT_FIELDS as $field => $max) {
        $value = (string)($input[$field] ?? '');
        if (mb_strlen($value, 'UTF-8') > $max) {
            $errors[$field] = $max . '文字以内にしてください。';
        }
    }
    return $errors;
}

// AIの出力形式を変えたら上げる。古い作り置きが再利用されるのを防ぐ。
const BIRA_BACK_FORMAT_VERSION = 2;

/** 1ページぶんの完成したHTMLかどうか。断片ならスタイルが効かない。 */
function bira_back_is_full_page(string $html): bool
{
    return stripos($html, '<html') !== false && stripos($html, '<style') !== false;
}

function bira_back_h(string $value): string
{
    return htmlspecialchars(trim($value), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

/** 連絡先を改行そのままで出す。住所・電話・メール・SNSは任意。 */
function bira_back_contact_block(array $input): string
{
    $lines = [];
    $address = trim((string)($input['住所'] ?? ''));
    if ($address !== '') {
        $lines[] = '<span class="st">' . bira_back_h($address) . '</span>';
    }
    $phone = trim((string)($input['電話'] ?? ''));
    $mail = trim((string)($input['メール'] ?? ''));
    $contact = [];
    if ($phone !== '') {
        $contact[] = 'TEL ' . bira_back_h($phone);
    }
    if ($mail !== '') {
        $contact[] = 'MAIL ' . bira_back_h($mail);
    }
    if ($contact !== []) {
        $lines[] = implode('　', $contact);
    }
    $sns = trim((string)($input['ＳＮＳ'] ?? ''));
    if ($sns !== '') {
        $lines[] = bira_back_h($sns);
    }
    return implode('<br>', $lines);
}

/** 発行者・連絡先のブロック。法律で要る表示なので、AIには書かせず必ずこれを使う。 */
function bira_back_foot_html(array $input): string
{
    $name = bira_back_h((string)($input['氏名'] ?? ''));
    $publisher = bira_back_h((string)($input['発行者'] ?? ''));
    $contact = bira_back_contact_block($input);

    return implode("\n", [
        '<div class="foot">',
        '    <table class="top"><tr><td>',
        '      <div class="who">' . $name . '</div>',
        '      <div class="ln">' . $contact . '</div>',
        '    </td></tr></table>',
        '    <div class="pub">政治活動用　発行：' . $publisher . '</div>',
        '  </div>',
    ]);
}

/**
 * 裏面のページを仕上げる。
 *
 * AIにはCSSごと1枚のHTMLを書かせる。版面の作り方まで縛ると、下に空白が残る
 * ようなときに直しようがなくなるため。ただし発行者・連絡先だけは差し替える。
 * 依頼書にあるとおり、ここの書き換え忘れは実害が出るのでAI任せにはしない。
 */
function bira_back_render_html(array $input, string $pageHtml): string
{
    // 断片を渡されるとスタイルの無いページになる。その状態で出すくらいなら見本を使う。
    if (!bira_back_is_full_page($pageHtml)) {
        $pageHtml = bira_back_sample_html();
    }
    $foot = bira_back_foot_html($input);
    $count = 0;

    // 見本と同じ「.foot ... </div>（ページ末尾）」の形なら、そこを丸ごと置き換える。
    $replaced = preg_replace(
        '#<div class="foot">.*?</div>\s*</div>#s',
        $foot . "\n</div>",
        $pageHtml,
        1,
        $count
    );
    if (is_string($replaced) && $count === 1) {
        return $replaced;
    }
    // 見つからなければ末尾に足す。発行者表示のないビラは出さない。
    $appended = preg_replace('#</body>#i', $foot . "\n</body>", $pageHtml, 1, $count);
    return (is_string($appended) && $count === 1) ? $appended : $pageHtml . $foot;
}

// ---------------------------------------------------------------------------
// AI による本文生成
// ---------------------------------------------------------------------------

function bira_openai_config(): array
{
    $config = load_config();
    $openai = is_array($config['openai'] ?? null) ? $config['openai'] : [];
    $apiKey = trim((string)($openai['apiKey'] ?? ''));
    if ($apiKey === '') {
        throw new RuntimeException('OpenAI の設定がありません（config.json の openai.apiKey）。');
    }
    return [
        'apiKey' => $apiKey,
        'baseUrl' => trim((string)($openai['baseUrl'] ?? 'https://api.openai.com/v1/chat/completions')),
        'model' => trim((string)($openai['chatModel'] ?? '')) ?: 'gpt-5.4',
        'timeoutMs' => max(10000, (int)($openai['timeoutMs'] ?? 120000)),
    ];
}

/**
 * 文体と出典の条件。依頼書の指定をそのまま渡す。
 * 事実を作らせないことが最優先で、根拠のない項目は空欄で返させる。
 * 版面の作り方は縛らない。縛ると「下に空白が残る」ような不都合を直せなくなる。
 */
function bira_back_system_prompt(): string
{
    $sample = bira_back_sample_html();

    return <<<PROMPT
あなたは政治活動ビラの裏面を組む担当です。日本語で書きます。

## 出力するもの
`<!DOCTYPE html>` から `</html>` までの**完成した1ページのHTML**を返します。
CSSは外部ファイルにせず、`<style>` としてHTMLの中に書いてください。
説明文やコードフェンス（```）は付けず、HTMLだけを返します。

## 見本（川崎版）
これと同じ体裁にそろえてください。CSSも構造も、必要なら変えて構いません。
{$sample}

## 変えてはいけないもの
- ページの寸法。`@page { size: 216mm 303mm; margin: 0; }` と `.page` の 216mm × 303mm。
- 版面は端から16mm。
- 上部の赤いヘッダー（党章・党名・地域名・紙名・号数・年月）。
- 書体は "Noto Sans JP"。色は赤 #F31700、文字 #1A1A1A。
- 党章の画像パス `素材/桜_白.svg`。ほかの画像は追加できません。

## 版面の埋め方（重要）
- **下に空白を残さないでください。** 本文が少なければ、項目の間隔を広げ、
  文字を大きくし、余白を配分して、本文エリアの下端まで自然に埋めます。
- 縦方向に均等配分するときは **table を使ってください。**
  `table { height: 100%; }` として1項目1行にすると、行が高さを分け合います。
  **flex の justify-content: space-between と margin-top: auto は効きません**（実測）。
- 逆に多すぎてはみ出すのも不可です。303mmに収めてください。

## 文体（守ってください）
- 街頭で受け取った人が読むものです。学会発表でも役所文書でもありません。
- 高卒の中高年が前提知識なしで読んで意味が分かること。
- 1文を短く。体言止めを並べない。
- 「〜の推進を図る」「〜に資する」といった役所言葉を使わない。
- 数字を並べて説得しようとしない。数字は根拠として小さく添える。
- 約束の見出しは「○○を、やめさせます」「○○を、すすめます」の形にする。

## 事実の扱い（絶対条件）
- 本文に書く事実は、利用者が渡した資料に書いてあることだけです。
- あなたの知識で数字・条文・施策名を補ってはいけません。
- 根拠が渡されていない項目は、根拠の行を書かずに本文だけにします。作文で埋めないでください。
- 利用者が自分で集計した数字には「（当方が事務事業評価から集計）」のように出所を添えます。

## 書いてはいけないこと
- 「投票をお願いします」など投票を依頼する表現（事前運動にあたります）。
- 候補者の当落や選挙そのものへの言及。
- 発行者名・連絡先。`<div class="foot">` はこちらで差し替えるので、見本のまま残してください。

## そのほかの制約
- 半角数字と日本語の間に自動でアキが入ります。詰めたい箇所は全角数字にしてください。
- 外部のURLは読み込めません。画像・CSS・フォントを外から持ってこないでください。
PROMPT;
}

function bira_back_user_prompt(array $input): string
{
    $region = trim((string)($input['地域'] ?? ''));
    $title = trim((string)($input['大見出し'] ?? ''));
    $claims = trim((string)($input['主張したいこと'] ?? ''));
    $evidence = trim((string)($input['根拠資料'] ?? ''));
    $layout = trim((string)($input['レイアウトの希望'] ?? ''));

    $parts = [];
    $parts[] = "## 地域\n{$region}";
    $parts[] = "## 大見出し（h1.title にそのまま使う）\n{$title}";
    $parts[] = "## 主張したいこと\n" . ($claims !== '' ? $claims : '（未入力）');
    $parts[] = "## 根拠にできる資料（ここに書かれていないことは本文に書かない）\n"
        . ($evidence !== '' ? $evidence : '（未提出。根拠を示せないので p.ev は書かないでください）');
    if ($layout !== '') {
        $parts[] = "## レイアウトの希望\n{$layout}";
    }
    return implode("\n\n", $parts);
}

/** 返ってきたHTMLを検査する。外部通信とスクリプトは通さない。 */
function bira_back_sanitize_content(string $html): string
{
    $html = trim($html);
    // コードフェンスで包んで返してくることがあるので剥がす。
    $html = preg_replace('/\A```(?:html)?\s*|\s*```\z/u', '', $html) ?? $html;
    $html = trim($html);

    if ($html === '') {
        throw new RuntimeException('AIが本文を返しませんでした。もう一度お試しください。');
    }
    // meta は charset に要る。link は外部CSSを引けるので通さない。
    if (preg_match('#<\s*(script|iframe|object|embed|link)\b#i', $html) === 1) {
        throw new RuntimeException('使えないタグが含まれていたため、生成をやり直してください。');
    }
    if (preg_match('#\son[a-z]+\s*=#i', $html) === 1) {
        throw new RuntimeException('使えない属性が含まれていたため、生成をやり直してください。');
    }
    // 外部リソースを読ませない。素材は同梱のものだけ。
    if (preg_match('#(src|href)\s*=\s*["\']?(https?:)?//#i', $html) === 1) {
        throw new RuntimeException('外部の画像やリンクは使えません。生成をやり直してください。');
    }
    if (preg_match('#@import|url\s*\(\s*["\']?(https?:)?//#i', $html) === 1) {
        throw new RuntimeException('外部のスタイルは使えません。生成をやり直してください。');
    }
    // 断片で返ってくるとスタイルが一切効かないページになる。
    if (!bira_back_is_full_page($html)) {
        throw new RuntimeException('AIがページ全体を返しませんでした。もう一度お試しください。');
    }
    return $html;
}

/** OpenAI に本文を書かせる。呼び出し回数はここでは数えない（呼び出し側の責任）。 */
function bira_back_generate_content(array $input): string
{
    $openai = bira_openai_config();

    $payload = json_encode([
        'model' => $openai['model'],
        'messages' => [
            ['role' => 'system', 'content' => bira_back_system_prompt()],
            ['role' => 'user', 'content' => bira_back_user_prompt($input)],
        ],
    ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    if (!is_string($payload)) {
        throw new RuntimeException('AIへの依頼を組み立てられませんでした。');
    }

    $curl = curl_init($openai['baseUrl']);
    curl_setopt_array($curl, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $payload,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT_MS => $openai['timeoutMs'],
        CURLOPT_HTTPHEADER => [
            'Content-Type: application/json',
            'Authorization: Bearer ' . $openai['apiKey'],
        ],
    ]);
    $body = curl_exec($curl);
    $status = (int)curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
    $curlError = curl_error($curl);
    curl_close($curl);

    if (!is_string($body) || $body === '') {
        error_log('[bira] openai request failed: ' . $curlError);
        throw new RuntimeException('AIに接続できませんでした。時間をおいてお試しください。');
    }
    if ($status < 200 || $status >= 300) {
        error_log('[bira] openai HTTP ' . $status . ': ' . substr($body, 0, 400));
        throw new RuntimeException('AIの呼び出しに失敗しました（HTTP ' . $status . '）。');
    }

    $decoded = json_decode($body, true);
    $content = $decoded['choices'][0]['message']['content'] ?? null;
    if (!is_string($content)) {
        error_log('[bira] openai unexpected payload: ' . substr($body, 0, 400));
        throw new RuntimeException('AIの返答を読み取れませんでした。');
    }

    return bira_back_sanitize_content($content);
}

// ---------------------------------------------------------------------------
// 表裏をまとめた1つのPDF
// ---------------------------------------------------------------------------

/**
 * 表と裏を綴じた1つのPDF。
 *
 * 表と裏は .page や .head など同じclass名を別の中身で使っているため、1つの
 * HTMLにまとめるとCSSがぶつかる。CTIP2の continuous モードで別々に変換して
 * から綴じれば、双方のテンプレートに手を入れずに済む。
 */
function bira_both_build_pdf(array $input, string $contentHtml): string
{
    return bira_transcode_documents([
        bira_inline_images(bira_render_html($input)),
        bira_inline_images(bira_back_render_html($input, $contentHtml)),
    ]);
}
