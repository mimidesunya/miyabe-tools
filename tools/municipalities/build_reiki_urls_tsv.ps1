param(
    [string]$MasterTsv = (Join-Path $PSScriptRoot '..\..\data\japan_local_governments.tsv'),
    [string]$OutFile = (Join-Path $PSScriptRoot '..\..\data\local_reiki_urls.tsv'),
    [string]$HomepageCsv = (Join-Path $PSScriptRoot '..\..\data\local_government_homepages.csv'),
    [string]$IndexUrl = 'https://www.rilg.or.jp/htdocs/main/zenkoku_reiki/zenkoku_Link.html',
    [int]$DelayMilliseconds = 150
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:DefaultHeaders = @{
    'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
}

function Resolve-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path)
}

function Decode-Html {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)

    $decoded = [System.Net.WebUtility]::HtmlDecode($Value)
    $decoded = [System.Text.RegularExpressions.Regex]::Replace($decoded, '<[^>]+>', ' ')
    $decoded = [System.Text.RegularExpressions.Regex]::Replace($decoded, '\s+', ' ')
    return $decoded.Trim()
}

function Normalize-Name {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)

    $normalized = Decode-Html -Value $Value
    $normalized = $normalized.Replace('　', '')
    $normalized = $normalized.Replace(' ', '')
    return $normalized.Trim()
}

function Get-Html {
    param([Parameter(Mandatory = $true)][string]$Url)
    return [string](Invoke-WebRequest -Uri $Url -Headers $script:DefaultHeaders).Content
}

function Get-ComparableHost {
    param([Parameter(Mandatory = $true)][string]$HostName)

    $normalized = $HostName.Trim().ToLowerInvariant()
    if ($normalized.StartsWith('www.')) {
        return $normalized.Substring(4)
    }

    return $normalized
}

function Get-PageText {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Content)

    $text = Decode-Html -Value $Content
    if ($text.Length -gt 20000) {
        return $text.Substring(0, 20000)
    }

    return $text
}

function Get-WebPage {
    param([Parameter(Mandatory = $true)][string]$Url)

    if ($script:WebPageCache.ContainsKey($Url)) {
        return $script:WebPageCache[$Url]
    }

    try {
        $response = Invoke-WebRequest -Uri $Url -MaximumRedirection 5 -TimeoutSec 30 -Headers $script:DefaultHeaders -SkipHttpErrorCheck
    } catch {
        $result = [PSCustomObject]@{
            reachable = $false
            final_url = ''
            title = ''
            content = ''
            text = ''
        }
        $script:WebPageCache[$Url] = $result
        return $result
    }

    $statusCode = [int]$response.StatusCode
    if ($statusCode -lt 200 -or $statusCode -ge 400) {
        $result = [PSCustomObject]@{
            reachable = $false
            final_url = ''
            title = ''
            content = ''
            text = ''
        }
        $script:WebPageCache[$Url] = $result
        return $result
    }

    $content = [string]$response.Content
    $title = ''
    if ($content -match '<title>(?<t>[^<]+)</title>') {
        $title = Decode-Html -Value $Matches['t']
    }

    $finalUrl = $response.BaseResponse.RequestMessage.RequestUri.AbsoluteUri
    $result = [PSCustomObject]@{
        reachable = $true
        final_url = $finalUrl
        title = $title
        content = $content
        text = (Get-PageText -Content $content)
    }

    $script:WebPageCache[$Url] = $result
    if (-not $script:WebPageCache.ContainsKey($finalUrl)) {
        $script:WebPageCache[$finalUrl] = $result
    }

    return $result
}

function Get-HtmlAttributeValue {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Html,
        [Parameter(Mandatory = $true)][string]$AttributeName
    )

    $pattern = '(?is)\b' + [System.Text.RegularExpressions.Regex]::Escape($AttributeName) + '\s*=\s*(?:"(?<v>[^"]*)"|''(?<v>[^'']*)''|(?<v>[^\s>]+))'
    $match = [System.Text.RegularExpressions.Regex]::Match($Html, $pattern)
    if (-not $match.Success) {
        return ''
    }

    return Decode-Html -Value $match.Groups['v'].Value
}

function Extract-HtmlLinks {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Html,
        [Parameter(Mandatory = $true)][string]$BaseUrl
    )

    $pattern = '(?is)<a\b(?<attrs>[^>]*)href\s*=\s*(?:"(?<href>[^"]*)"|''(?<href>[^'']*)''|(?<href>[^\s>]+))(?<rest>[^>]*)>(?<text>.*?)</a>'
    $matches = [System.Text.RegularExpressions.Regex]::Matches($Html, $pattern)
    $results = New-Object System.Collections.Generic.List[object]
    $seen = @{}

    foreach ($match in $matches) {
        $href = Decode-Html -Value $match.Groups['href'].Value
        if ([string]::IsNullOrWhiteSpace($href)) {
            continue
        }

        if ($href.StartsWith('#') -or $href -match '^(javascript|mailto|tel|data):') {
            continue
        }

        try {
            $absoluteUrl = [System.Uri]::new([System.Uri]$BaseUrl, $href).AbsoluteUri
        } catch {
            continue
        }

        if ($seen.ContainsKey($absoluteUrl)) {
            continue
        }
        $seen[$absoluteUrl] = $true

        $rawText = [string]$match.Groups['text'].Value
        $attrs = [string]$match.Groups['attrs'].Value + ' ' + [string]$match.Groups['rest'].Value
        $text = Decode-Html -Value $rawText
        if ($text -eq '') {
            $text = Get-HtmlAttributeValue -Html $attrs -AttributeName 'aria-label'
        }
        if ($text -eq '') {
            $text = Get-HtmlAttributeValue -Html $attrs -AttributeName 'title'
        }
        if ($text -eq '' -and $rawText -match '(?is)<img\b[^>]*alt\s*=\s*(?:"(?<alt>[^"]*)"|''(?<alt>[^'']*)'')') {
            $text = Decode-Html -Value $Matches['alt']
        }

        $uri = [System.Uri]$absoluteUrl
        $results.Add([PSCustomObject]@{
                url = $absoluteUrl
                text = $text
                host = $uri.Host.ToLowerInvariant()
                path = $uri.AbsolutePath.ToLowerInvariant()
            })
    }

    return @($results | ForEach-Object { $_ })
}

function Get-UpdatedUrlFromNotice {
    param(
        [Parameter(Mandatory = $true)][string]$CurrentUrl,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Title,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Content
    )

    if ($Title -notmatch 'URL変更のお知らせ|移転|ページが変わりました' -and $Content -notmatch '以下のURLに変更|URLが変更|移転先|新しいURL') {
        return ''
    }

    $currentUri = [System.Uri]$CurrentUrl
    $matches = [System.Text.RegularExpressions.Regex]::Matches($Content, 'https?://[^"''\s<>]+')
    foreach ($match in $matches) {
        $candidate = Decode-Html -Value $match.Value
        if ($candidate -eq '') {
            continue
        }

        try {
            $candidateUri = [System.Uri]$candidate
        } catch {
            continue
        }

        if ($candidateUri.AbsoluteUri -eq $currentUri.AbsoluteUri) {
            continue
        }

        return $candidateUri.AbsoluteUri
    }

    return ''
}

function Parse-ReikiIndexItems {
    param(
        [Parameter(Mandatory = $true)][string]$Html,
        [Parameter(Mandatory = $true)][string]$BaseUrl
    )

    $htmlWithoutComments = [System.Text.RegularExpressions.Regex]::Replace($Html, '(?is)<!--.*?-->', '')
    $sectionPattern = '(?is)<span[^>]*>\s*<b>\s*&lt;\s*(?<pref>[^<]+?)\s*&gt;\s*</b>\s*</span>.*?<table\b[^>]*>(?<table>.*?)</table>'
    $cellPattern = '(?is)<td\b[^>]*>(?<body>.*?)</td>'
    $linkPattern = '(?is)<a\b[^>]*href\s*=\s*(?:"(?<href>[^"]*)"|''(?<href>[^'']*)''|(?<href>[^\s>]+))[^>]*>(?<label>.*?)</a>'
    $sections = [System.Text.RegularExpressions.Regex]::Matches($htmlWithoutComments, $sectionPattern)
    $results = New-Object System.Collections.Generic.List[object]
    $categoryNames = @('都道府県', '市', '町', '村', '町村', 'その他')

    foreach ($section in $sections) {
        $prefName = Normalize-Name -Value $section.Groups['pref'].Value
        if ($prefName -eq '') {
            continue
        }

        $tableHtml = [string]$section.Groups['table'].Value
        $cells = [System.Text.RegularExpressions.Regex]::Matches($tableHtml, $cellPattern)
        foreach ($cell in $cells) {
            $body = [string]$cell.Groups['body'].Value
            $text = Normalize-Name -Value $body
            if ($text -eq '' -or $categoryNames -contains $text) {
                continue
            }

            $linkMatch = [System.Text.RegularExpressions.Regex]::Match($body, $linkPattern)
            $name = $text
            $url = ''
            if ($linkMatch.Success) {
                $name = Normalize-Name -Value $linkMatch.Groups['label'].Value
                if ($name -eq '') {
                    continue
                }

                try {
                    $url = [System.Uri]::new([System.Uri]$BaseUrl, (Decode-Html -Value $linkMatch.Groups['href'].Value)).AbsoluteUri
                } catch {
                    $url = ''
                }
            }

            $results.Add([PSCustomObject]@{
                    pref_name = $prefName
                    name = $name
                    url = $url
                })
        }
    }

    return @($results | ForEach-Object { $_ })
}

function Classify-ReikiSystem {
    param(
        [Parameter(Mandatory = $true)][string]$FinalUrl,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Title,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Content
    )

    $uri = [System.Uri]$FinalUrl
    $urlHost = $uri.Host.ToLowerInvariant()
    $path = $uri.AbsolutePath.ToLowerInvariant()
    $signal = "$Title`n$Content`n$FinalUrl"

    if ($urlHost -like '*.d1-law.com' -or $urlHost -eq 'ops-jg.d1-law.com' -or $path -match '/d1w_reiki/' -or $path -match '/opensearch/' -or $signal -match 'd1[- ]law|d1w_reiki|opensearch') {
        return 'd1-law'
    }

    if ($urlHost -eq 'houmu.h-chosonkai.gr.jp' -or $path -match '/~reikidb/') {
        return 'h-chosonkai'
    }

    if ($urlHost -match '(^|\.)(g-reiki\.net|g-reiki\.)' -or $signal -match 'g-reiki') {
        return 'g-reiki'
    }

    if ($urlHost -like '*.joureikun.jp' -or $signal -match 'joureikun') {
        return 'joureikun'
    }

    if ($urlHost -like '*.legal-square.com' -or $path -match '/has-shohin/page/sjsrblogin\.jsf$' -or $signal -match 'legal-square|has-shohin') {
        return 'legal-square'
    }

    if ($urlHost -like '*.legalcrud.com' -or $signal -match 'legalcrud') {
        return 'legalcrud'
    }

    if ($path -match '/reiki_taikei/taikei_default\.html?$' -or $signal -match 'taikei_default') {
        return 'taikei'
    }

    if ($path -match '/joureiv\d+htmlcontents/' -or $signal -match 'joureiv\d+htmlcontents') {
        return 'jourei-v5'
    }

    if ($path -match '/reiki(?:_int)?/reiki_menu\.html?$' -or $signal -match 'reiki_menu\.html') {
        return 'reiki_menu'
    }

    if ($path -match '/reiki\.html?$') {
        return 'reiki.html'
    }

    return '独自'
}

function Inspect-ReikiUrl {
    param([Parameter(Mandatory = $true)][string]$Url)

    if ($script:ReikiInspectionCache.ContainsKey($Url)) {
        return $script:ReikiInspectionCache[$Url]
    }

    $page = Get-WebPage -Url $Url
    if (-not $page.reachable) {
        $result = [PSCustomObject]@{
            reachable = $false
            final_url = ''
            system_type = ''
            title = ''
            content_text = ''
        }
        $script:ReikiInspectionCache[$Url] = $result
        return $result
    }

    $updatedUrl = Get-UpdatedUrlFromNotice -CurrentUrl $page.final_url -Title $page.title -Content $page.content
    if ($updatedUrl -ne '' -and $updatedUrl -ne $page.final_url) {
        $result = Inspect-ReikiUrl -Url $updatedUrl
        $script:ReikiInspectionCache[$Url] = $result
        return $result
    }

    $result = [PSCustomObject]@{
        reachable = $true
        final_url = $page.final_url
        system_type = (Classify-ReikiSystem -FinalUrl $page.final_url -Title $page.title -Content $page.content)
        title = $page.title
        content_text = $page.text
    }

    $script:ReikiInspectionCache[$Url] = $result
    if (-not $script:ReikiInspectionCache.ContainsKey($result.final_url)) {
        $script:ReikiInspectionCache[$result.final_url] = $result
    }

    return $result
}

function Test-IsLikelyReikiPage {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Inspection,
        [AllowEmptyString()][string]$LinkText = ''
    )

    if (-not $Inspection.reachable) {
        return $false
    }

    if ([string]$Inspection.system_type -ne '独自') {
        return $true
    }

    $signal = "$($Inspection.title)`n$($Inspection.content_text)`n$($Inspection.final_url)"
    if ($signal -notmatch '例規集|例規|条例集|規則集|条例・規則|条例規則|規則|要綱|(?<![a-z])reiki(?![a-z])|(?<![a-z])jourei(?![a-z])|(?<![a-z])jyorei(?![a-z])') {
        return $false
    }

    if ($LinkText -match '例規集|例規|条例・規則|条例規則|法規') {
        return $true
    }

    $strongSignal = "$($Inspection.title)`n$($Inspection.final_url)"
    return ($strongSignal -match '例規集|条例集|規則集|(?<![a-z])reiki(?![a-z])|(?<![a-z])jourei(?![a-z])|(?<![a-z])jyorei(?![a-z])')
}

function Score-ReikiLink {
    param([Parameter(Mandatory = $true)][pscustomobject]$Link)

    if ($Link.path -match '\.(pdf|docx?|xlsx?|pptx?|zip|jpg|jpeg|png|gif)$') {
        return -1000
    }

    $score = 0
    $text = [string]$Link.text
    $url = [string]$Link.url

    if ($url -match 'joureikun|d1-law|legal-square|legalcrud|taikei|h-chosonkai|opensearch|sjsrblogin|(?<![a-z])reiki(?![a-z])|(?<![a-z])jourei(?![a-z])|(?<![a-z])jyorei(?![a-z])') {
        $score += 140
    }

    if ($text -match '例規集') {
        $score += 180
    } elseif ($text -match '例規|条例・規則|条例規則|法規') {
        $score += 120
    } elseif ($text -match '条例|規則|要綱') {
        $score += 80
    }

    if ($text -match '検索|一覧|集') {
        $score += 20
    }

    if ($text -match '制定|改正|改廃|パブリックコメント|意見募集|議案|審議|会議録|議会|告示|公告|入札|採用|計画') {
        $score -= 90
    }

    return $score
}

function Score-ReikiExplorationLink {
    param([Parameter(Mandatory = $true)][pscustomobject]$Link)

    if ($Link.path -match '\.(pdf|docx?|xlsx?|pptx?|zip|jpg|jpeg|png|gif)$') {
        return -1000
    }

    $score = 0
    $text = [string]$Link.text
    $url = [string]$Link.url

    if ($text -match '例規|条例|規則|要綱|法規') {
        $score += 140
    }

    if ($url -match '/reiki|/jourei|/ordinance') {
        $score += 100
    }

    if ($text -match 'サイトマップ|組織から探す|組織一覧|分類でさがす|カテゴリから探す|行政情報|町政情報|市政情報|村政情報') {
        $score += 40
    }

    if ($url -match '/sitemap|/site-map') {
        $score += 30
    }

    if ($text -match '議会|会議録|議事録|議案|入札|採用|観光|イベント|子育て|教育|福祉') {
        $score -= 60
    }

    return $score
}

function Find-ReikiUrlFromHomepage {
    param(
        [Parameter(Mandatory = $true)][string]$HomepageUrl,
        [Parameter(Mandatory = $true)][int]$RequestDelayMilliseconds
    )

    $homeUri = [System.Uri]$HomepageUrl
    $homeHost = Get-ComparableHost -HostName $homeUri.Host
    $queue = [System.Collections.Generic.Queue[object]]::new()
    $queue.Enqueue([PSCustomObject]@{
            url = $HomepageUrl
            depth = 0
        })
    $visited = @{}
    $inspectedCandidateUrls = @{}

    while ($queue.Count -gt 0) {
        $item = $queue.Dequeue()
        if ($visited.ContainsKey($item.url)) {
            continue
        }
        $visited[$item.url] = $true

        $page = Get-WebPage -Url $item.url
        if (-not $page.reachable) {
            continue
        }

        $visited[$page.final_url] = $true
        $links = Extract-HtmlLinks -Html $page.content -BaseUrl $page.final_url
        $reikiCandidates = @(
            $links |
                ForEach-Object {
                    [PSCustomObject]@{
                        link = $_
                        score = (Score-ReikiLink -Link $_)
                    }
                } |
                Where-Object { $_.score -gt 0 } |
                Sort-Object -Property @{ Expression = { $_.score }; Descending = $true }, @{ Expression = { $_.link.text.Length }; Ascending = $true }
        )

        foreach ($candidate in ($reikiCandidates | Select-Object -First 12)) {
            $candidateUrl = [string]$candidate.link.url
            if ($inspectedCandidateUrls.ContainsKey($candidateUrl)) {
                continue
            }
            $inspectedCandidateUrls[$candidateUrl] = $true

            $inspection = Inspect-ReikiUrl -Url $candidateUrl
            if (Test-IsLikelyReikiPage -Inspection $inspection -LinkText ([string]$candidate.link.text)) {
                return [PSCustomObject]@{
                    url = [string]$inspection.final_url
                    system_type = [string]$inspection.system_type
                }
            }
        }

        if ($item.depth -gt 0) {
            $selfInspection = [PSCustomObject]@{
                reachable = $true
                final_url = $page.final_url
                system_type = (Classify-ReikiSystem -FinalUrl $page.final_url -Title $page.title -Content $page.content)
                title = $page.title
                content_text = $page.text
            }
            if (Test-IsLikelyReikiPage -Inspection $selfInspection) {
                return [PSCustomObject]@{
                    url = [string]$selfInspection.final_url
                    system_type = [string]$selfInspection.system_type
                }
            }
        }

        if ($item.depth -ge 3) {
            continue
        }

        $explorationCandidates = @(
            $links |
                Where-Object { (Get-ComparableHost -HostName $_.host) -eq $homeHost } |
                ForEach-Object {
                    [PSCustomObject]@{
                        link = $_
                        score = (Score-ReikiExplorationLink -Link $_)
                    }
                } |
                Where-Object { $_.score -gt 0 } |
                Sort-Object -Property @{ Expression = { $_.score }; Descending = $true }, @{ Expression = { $_.link.text.Length }; Ascending = $true }
        )

        foreach ($candidate in ($explorationCandidates | Select-Object -First 8)) {
            $candidateUrl = [string]$candidate.link.url
            if ($visited.ContainsKey($candidateUrl)) {
                continue
            }
            $queue.Enqueue([PSCustomObject]@{
                    url = $candidateUrl
                    depth = ([int]$item.depth + 1)
                })
        }

        Start-Sleep -Milliseconds $RequestDelayMilliseconds
    }

    return $null
}

$MasterTsv = Resolve-FullPath -Path $MasterTsv
$OutFile = Resolve-FullPath -Path $OutFile
$HomepageCsv = Resolve-FullPath -Path $HomepageCsv
$script:WebPageCache = @{}
$script:ReikiInspectionCache = @{}

$masterRows = Import-Csv -Delimiter "`t" -Path $MasterTsv
$homepageUrlByCode = @{}
if (Test-Path -LiteralPath $HomepageCsv) {
    foreach ($homepageRow in (Import-Csv -LiteralPath $HomepageCsv)) {
        $homepageUrlByCode[[string]$homepageRow.jis_code] = [string]$homepageRow.url
    }
}

$masterIndex = @{}
foreach ($row in $masterRows) {
    $key = "$(Normalize-Name -Value ([string]$row.pref_name))|$(Normalize-Name -Value ([string]$row.name))"
    $masterIndex[$key] = $row
}

$indexHtml = Get-Html -Url $IndexUrl
$items = Parse-ReikiIndexItems -Html $indexHtml -BaseUrl $IndexUrl
$reikiUrlByCode = @{}

foreach ($item in $items) {
    $key = "$($item.pref_name)|$($item.name)"
    if (-not $masterIndex.ContainsKey($key)) {
        continue
    }

    $row = $masterIndex[$key]
    $jisCode = [string]$row.jis_code
    if (-not $reikiUrlByCode.ContainsKey($jisCode)) {
        $reikiUrlByCode[$jisCode] = [string]$item.url
    }
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("jis_code`turl`tsystem_type")
$matchedCount = 0
$blankCount = 0
$homepageFallbackCount = 0

foreach ($row in ($masterRows | Sort-Object jis_code)) {
    $jisCode = [string]$row.jis_code
    $url = [string]($reikiUrlByCode[$jisCode] ?? '')
    $systemType = ''
    $usedHomepageFallback = $false

    if ($url -ne '') {
        $inspection = Inspect-ReikiUrl -Url $url
        if ($inspection.reachable) {
            $url = [string]$inspection.final_url
            $systemType = [string]$inspection.system_type
        } else {
            $url = ''
            $systemType = ''
        }
    }

    if ($url -eq '') {
        $homepageUrl = [string]($homepageUrlByCode[$jisCode] ?? '')
        if ($homepageUrl -ne '') {
            $homepageMatch = Find-ReikiUrlFromHomepage -HomepageUrl $homepageUrl -RequestDelayMilliseconds $DelayMilliseconds
            if ($null -ne $homepageMatch) {
                $url = [string]$homepageMatch.url
                $systemType = [string]$homepageMatch.system_type
                $usedHomepageFallback = $true
            }
        }
    }

    if ($url -eq '') {
        $blankCount++
    } else {
        $matchedCount++
        if ($usedHomepageFallback) {
            $homepageFallbackCount++
        }
    }

    $lines.Add("$jisCode`t$url`t$systemType")
}

[System.IO.Directory]::CreateDirectory((Split-Path -Parent $OutFile)) | Out-Null
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($OutFile, $lines, $utf8NoBom)

Write-Host ("Wrote {0} matched URLs and {1} blanks to {2} (homepage fallback: {3})" -f $matchedCount, $blankCount, $OutFile, $homepageFallbackCount)
