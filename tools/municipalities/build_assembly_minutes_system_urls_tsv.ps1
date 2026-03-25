param(
    [string]$MasterTsv = (Join-Path $PSScriptRoot '..\..\data\municipalities\municipality_master.tsv'),
    [string]$OutFile = (Join-Path $PSScriptRoot '..\..\data\municipalities\assembly_minutes_system_urls.tsv'),
    [string]$HomepageCsv = (Join-Path $PSScriptRoot '..\..\data\municipalities\municipality_homepages.csv'),
    [string]$IndexUrl = 'https://app-mints.com/kaigiroku/',
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

function Decode-Html {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)

    $decoded = [System.Net.WebUtility]::HtmlDecode($Value)
    $decoded = [System.Text.RegularExpressions.Regex]::Replace($decoded, '<[^>]+>', ' ')
    $decoded = [System.Text.RegularExpressions.Regex]::Replace($decoded, '\s+', ' ')
    return $decoded.Trim()
}

function Parse-IndexPages {
    param([Parameter(Mandatory = $true)][string]$Html)

    $pattern = '<a class="text-info" href="lg/(?<slug>[^"]+)"><i class="fa fa-caret-right" aria-hidden="true"></i>\s*(?<label>[^<]+)</a>'
    $matches = [System.Text.RegularExpressions.Regex]::Matches($Html, $pattern)
    $pages = New-Object System.Collections.Generic.List[object]

    foreach ($match in $matches) {
        $slug = Decode-Html -Value $match.Groups['slug'].Value
        $label = Decode-Html -Value $match.Groups['label'].Value
        $prefName = if ($slug -like 'hokkaido*') { '北海道' } else { $label }

        $pages.Add([PSCustomObject]@{
                slug = $slug
                label = $label
                pref_name = $prefName
            })
    }

    return $pages
}

function Parse-AssemblyItems {
    param(
        [Parameter(Mandatory = $true)][string]$Html,
        [Parameter(Mandatory = $true)][string]$PrefName
    )

    $itemPattern = '(?s)<li class="lg-list-item p-2">\s*<h2 class="lg-list-item-title pl-2">.*?</i>\s*(?<assembly>[^<]+)</h2>(?<body>.*?)</li>'
    $linkPattern = '(?s)<div class="link-container">.*?<div class="link-label"><span[^>]*>.*?</i>\s*(?<label>[^<]+)</span></div>.*?<div class="link-url[^"]*"><a href="(?<url>[^"]+)"'
    $items = [System.Text.RegularExpressions.Regex]::Matches($Html, $itemPattern)
    $results = New-Object System.Collections.Generic.List[object]

    foreach ($item in $items) {
        $assembly = Decode-Html -Value $item.Groups['assembly'].Value
        $body = [string]$item.Groups['body'].Value
        $links = [System.Text.RegularExpressions.Regex]::Matches($body, $linkPattern)
        $candidates = New-Object System.Collections.Generic.List[object]

        foreach ($link in $links) {
            $candidates.Add([PSCustomObject]@{
                    label = Decode-Html -Value $link.Groups['label'].Value
                    url = Decode-Html -Value $link.Groups['url'].Value
                })
        }

        $providedNone = $body -match '提供なし'

        $results.Add([PSCustomObject]@{
                pref_name = $PrefName
                assembly = $assembly
                provided_none = $providedNone
                links = @($candidates | ForEach-Object { $_ })
            })
    }

    return $results
}

function Select-PreferredUrl {
    param([Parameter(Mandatory = $true)][object[]]$Links)

    $priorities = @(
        '会議録検索（PC版）',
        '会議録検索',
        '議事録検索（PC版）',
        '議事録検索',
        '会議録',
        '議事録',
        '会議録検索（スマートフォン・タブレット版）',
        '議事録検索（スマートフォン・タブレット版）'
    )

    foreach ($priority in $priorities) {
        $matched = $Links | Where-Object { $_.label -eq $priority } | Select-Object -First 1
        if ($matched) {
            return [string]$matched.url
        }
    }

    $fallback = $Links | Select-Object -First 1
    if ($fallback) {
        return [string]$fallback.url
    }

    return ''
}

function Test-UrlReachable {
    param([Parameter(Mandatory = $true)][string]$Url)

    try {
        $response = Invoke-WebRequest -Uri $Url -Method Head -MaximumRedirection 5 -TimeoutSec 20 -Headers $script:DefaultHeaders -SkipHttpErrorCheck
        $statusCode = [int]$response.StatusCode
        if ($statusCode -ge 200 -and $statusCode -lt 400) {
            return $true
        }

        if ($statusCode -eq 405) {
            $response = Invoke-WebRequest -Uri $Url -MaximumRedirection 5 -TimeoutSec 20 -Headers $script:DefaultHeaders -SkipHttpErrorCheck
            $statusCode = [int]$response.StatusCode
            return ($statusCode -ge 200 -and $statusCode -lt 400)
        }

        return $false
    } catch {
        return $false
    }
}

function Repair-DbSearchUrl {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][pscustomobject]$Row,
        [Parameter(Mandatory = $true)][hashtable]$PrefSlugByName
    )

    if ($Url -notlike '*db-search.com*') {
        return ''
    }

    $prefSlug = [string]($PrefSlugByName[$Row.pref_name] ?? '')
    if ($prefSlug -eq '') {
        return ''
    }

    $segment = ([System.Uri]$Url).AbsolutePath.Trim('/').Split('/')[0]
    if ($segment -eq '') {
        return ''
    }

    $baseSlug = $segment
    if ($baseSlug -match '-[ctv]$') {
        $baseSlug = $baseSlug.Substring(0, $baseSlug.Length - 2)
    }

    $entityToken = switch -Regex ($Row.entity_type) {
        '^prefecture$' { 'pref'; break }
        default {
            if ($Row.name -match '市$') { 'city' }
            elseif ($Row.name -match '町$') { 'town' }
            elseif ($Row.name -match '村$') { 'vill' }
            else { '' }
        }
    }

    if ($entityToken -eq '') {
        return ''
    }

    $candidate = "https://www.$entityToken.$baseSlug.$prefSlug.dbsr.jp/"
    if (Test-UrlReachable -Url $candidate) {
        return $candidate
    }

    return ''
}

function Get-UpdatedUrlFromNotice {
    param(
        [Parameter(Mandatory = $true)][string]$CurrentUrl,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Title,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Content
    )

    if ($Title -notmatch 'URL変更のお知らせ|Url変更のお知らせ' -and $Content -notmatch '以下のURLに変更') {
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

        if ($candidateUri.Host -eq $currentUri.Host -and $candidateUri.AbsolutePath -eq $currentUri.AbsolutePath) {
            continue
        }

        return $candidateUri.AbsoluteUri
    }

    return ''
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

function Classify-MinutesSystem {
    param(
        [Parameter(Mandatory = $true)][string]$FinalUrl,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Title,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Content
    )

    $uri = [System.Uri]$FinalUrl
    $urlHost = $uri.Host.ToLowerInvariant()
    $path = $uri.AbsolutePath.ToLowerInvariant()
    $signal = "$Title`n$Content"

    if ($urlHost -eq 'ssp.kaigiroku.net' -or $path -match '/(sp)?minutesearch\.html$') {
        return 'kaigiroku.net'
    }

    if ($urlHost -like '*.dbsr.jp') {
        return 'dbsr'
    }

    if ($urlHost -eq 'www.db-search.com') {
        return 'db-search'
    }

    if ($urlHost -eq 'www.kensakusystem.jp') {
        return 'kensakusystem'
    }

    if ($urlHost -like '*.gijiroku.com') {
        return 'gijiroku.com'
    }

    if ($urlHost -eq 'ami-search.amivoice.com') {
        return 'amivoice'
    }

    if ($urlHost -eq 'www.voicetechno.net' -or $path -match '/minutessystem/') {
        return 'voicetechno'
    }

    if ($path -match 'msearch\.cgi') {
        return 'msearch'
    }

    if ($urlHost -match '^kaigiroku\.' -and $path -match '/index\.php/?$') {
        return 'kaigiroku-indexphp'
    }

    if ($FinalUrl -match '/voices/' -or $path -match 'g0[78]v_search\.asp' -or $signal -match 'VOICES/Web') {
        return 'voices'
    }

    return '独自'
}

function Inspect-MinutesUrl {
    param([Parameter(Mandatory = $true)][string]$Url)

    if ($script:MinutesInspectionCache.ContainsKey($Url)) {
        return $script:MinutesInspectionCache[$Url]
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
        $script:MinutesInspectionCache[$Url] = $result
        return $result
    }

    $updatedUrl = Get-UpdatedUrlFromNotice -CurrentUrl $page.final_url -Title $page.title -Content $page.content

    if ($updatedUrl -ne '' -and $updatedUrl -ne $page.final_url) {
        $result = Inspect-MinutesUrl -Url $updatedUrl
        $script:MinutesInspectionCache[$Url] = $result
        return $result
    }

    $result = [PSCustomObject]@{
        reachable = $true
        final_url = $page.final_url
        system_type = (Classify-MinutesSystem -FinalUrl $page.final_url -Title $page.title -Content $page.content)
        title = $page.title
        content_text = $page.text
    }

    $script:MinutesInspectionCache[$Url] = $result
    if (-not $script:MinutesInspectionCache.ContainsKey($result.final_url)) {
        $script:MinutesInspectionCache[$result.final_url] = $result
    }

    return $result
}

function Test-IsLikelyMinutesPage {
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
    if ($signal -notmatch '会議録検索|議事録検索|議会会議録|会議録|議事録|MinutesSearch|g0[78]v_search') {
        return $false
    }

    if ($LinkText -match '会議録|議事録|会議の記録|本会議録') {
        return $true
    }

    $strongSignal = "$($Inspection.title)`n$($Inspection.final_url)"
    return ($strongSignal -match '会議録|議事録|kaigiroku|gijiroku|minutes')
}

function Score-MinutesLink {
    param([Parameter(Mandatory = $true)][pscustomobject]$Link)

    if ($Link.path -match '\.(pdf|docx?|xlsx?|pptx?|zip|jpg|jpeg|png|gif)$') {
        return -1000
    }

    $score = 0
    $text = [string]$Link.text
    $url = [string]$Link.url

    if ($url -match 'kaigiroku|gijiroku|dbsr|db-search|kensakusystem|amivoice|voicetechno|msearch|minutessystem|voices|g0[78]v_search|minutesearch') {
        $score += 120
    }

    if ($text -match '会議録検索|議事録検索') {
        $score += 140
    } elseif ($text -match '会議録|議事録') {
        $score += 100
    } elseif ($text -match '会議の記録|本会議録') {
        $score += 80
    }

    if ($text -match '^.+議会 ?会議録$|^会議録$|^議事録$') {
        $score += 40
    }

    if ($text -match '一覧|検索|トップ') {
        $score += 20
    }

    if ($text -match '本会議|委員会|予算|決算|総務|経済|建設|厚生|常任|特別|令和|平成|\d{4}年|\d+月|\d+日') {
        $score -= 15
    }

    if ($text -match '中継|録画|ライブ|YouTube|だより|交際費|政務活動費|議員|会派|請願|陳情|日程|質問通告|議案|審議結果') {
        $score -= 80
    }

    return $score
}

function Score-ExplorationLink {
    param([Parameter(Mandatory = $true)][pscustomobject]$Link)

    if ($Link.path -match '\.(pdf|docx?|xlsx?|pptx?|zip|jpg|jpeg|png|gif)$') {
        return -1000
    }

    $score = 0
    $text = [string]$Link.text
    $url = [string]$Link.url

    if ($text -match '会議録|議事録') {
        $score += 150
    }

    if ($text -match '議会') {
        $score += 120
    }

    if ($url -match '/gikai|/assembly|/council|/shigikai|/kugikai|/chougikai|/songikai|/site/gikai') {
        $score += 100
    }

    if ($text -match 'サイトマップ|組織から探す|組織一覧|分類でさがす|カテゴリから探す|市政情報|行政情報') {
        $score += 40
    }

    if ($url -match '/sitemap|/site-map') {
        $score += 30
    }

    if ($text -match '中継|録画|YouTube|だより|議員|会派|交際費|政務活動費|請願|陳情|入札|採用|観光|子育て|教育|福祉') {
        $score -= 50
    }

    return $score
}

function Find-MinutesUrlFromHomepage {
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
        $minutesCandidates = @(
            $links |
                ForEach-Object {
                    [PSCustomObject]@{
                        link = $_
                        score = (Score-MinutesLink -Link $_)
                    }
                } |
                Where-Object { $_.score -gt 0 } |
                Sort-Object -Property @{ Expression = { $_.score }; Descending = $true }, @{ Expression = { $_.link.text.Length }; Ascending = $true }
        )

        foreach ($candidate in ($minutesCandidates | Select-Object -First 12)) {
            $candidateUrl = [string]$candidate.link.url
            if ($inspectedCandidateUrls.ContainsKey($candidateUrl)) {
                continue
            }
            $inspectedCandidateUrls[$candidateUrl] = $true

            $inspection = Inspect-MinutesUrl -Url $candidateUrl
            if (Test-IsLikelyMinutesPage -Inspection $inspection -LinkText ([string]$candidate.link.text)) {
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
                system_type = (Classify-MinutesSystem -FinalUrl $page.final_url -Title $page.title -Content $page.content)
                title = $page.title
                content_text = $page.text
            }
            if (Test-IsLikelyMinutesPage -Inspection $selfInspection) {
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
                        score = (Score-ExplorationLink -Link $_)
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
$script:MinutesInspectionCache = @{}

$masterRows = Import-Csv -Delimiter "`t" -Path $MasterTsv
$homepageUrlByCode = @{}
if (Test-Path -LiteralPath $HomepageCsv) {
    foreach ($homepageRow in (Import-Csv -LiteralPath $HomepageCsv)) {
        $homepageUrlByCode[[string]$homepageRow.jis_code] = [string]$homepageRow.url
    }
}
$indexHtml = Get-Html -Url $IndexUrl
$pages = Parse-IndexPages -Html $indexHtml
$assemblyUrlByKey = @{}
$prefSlugByName = @{}
$manualUrlOverrides = @{
    '01000' = 'https://pref-hokkaido.gijiroku.com/voices/g07v_search.asp'
    '12000' = 'https://pref-chiba.gijiroku.com/'
    '24000' = 'https://www.kensakusystem.jp/mie/index.html'
    '25000' = 'https://www.shigaken-gikai.jp/voices/index.html'
    '31000' = 'https://www.pref.tottori.dbsr.jp/'
    '37000' = 'https://www.pref.kagawa.dbsr.jp/'
    '41000' = 'https://www.pref.saga.dbsr.jp/'
    '30000' = 'https://www.pref.wakayama.lg.jp/gijiroku/d00203238.html'
}
$reachableUrlByValue = @{}

foreach ($page in $pages) {
    if (-not $prefSlugByName.ContainsKey($page.pref_name) -and $page.slug -notlike 'hokkaido*') {
        $prefSlugByName[$page.pref_name] = $page.slug
    }

    $pageUrl = [System.Uri]::new([System.Uri]$IndexUrl, "lg/$($page.slug)").AbsoluteUri
    $pageHtml = Get-Html -Url $pageUrl
    $items = Parse-AssemblyItems -Html $pageHtml -PrefName $page.pref_name

    foreach ($item in $items) {
        $key = "$($item.pref_name)|$($item.assembly)"
        $url = if ($item.provided_none) { '' } else { Select-PreferredUrl -Links @($item.links) }
        $assemblyUrlByKey[$key] = $url
    }

    Start-Sleep -Milliseconds $DelayMilliseconds
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("jis_code`turl`tsystem_type")
$matchedCount = 0
$blankCount = 0

foreach ($row in ($masterRows | Sort-Object jis_code)) {
    $assemblyName = if ($row.entity_type -eq 'prefecture') {
        "$($row.pref_name)議会"
    } else {
        "$($row.name)議会"
    }

    $key = "$($row.pref_name)|$assemblyName"
    $url = ''
    if ($assemblyUrlByKey.ContainsKey($key)) {
        $url = [string]$assemblyUrlByKey[$key]
    }

    if ($manualUrlOverrides.ContainsKey($row.jis_code)) {
        $url = [string]$manualUrlOverrides[$row.jis_code]
    } elseif ($url -like '*db-search.com*') {
        if (-not $reachableUrlByValue.ContainsKey($url)) {
            $reachableUrlByValue[$url] = Test-UrlReachable -Url $url
        }
        if (-not $reachableUrlByValue[$url]) {
            $repairedUrl = Repair-DbSearchUrl -Url $url -Row $row -PrefSlugByName $prefSlugByName
            $url = $repairedUrl
        }
    }

    $systemType = ''
    if ($url -ne '') {
        $inspection = Inspect-MinutesUrl -Url $url
        if ($inspection.reachable) {
            $url = [string]$inspection.final_url
            $systemType = [string]$inspection.system_type
        } else {
            $url = ''
            $systemType = ''
        }
    }

    if ($url -eq '') {
        $homepageUrl = [string]($homepageUrlByCode[[string]$row.jis_code] ?? '')
        if ($homepageUrl -ne '') {
            $homepageMatch = Find-MinutesUrlFromHomepage -HomepageUrl $homepageUrl -RequestDelayMilliseconds $DelayMilliseconds
            if ($null -ne $homepageMatch) {
                $url = [string]$homepageMatch.url
                $systemType = [string]$homepageMatch.system_type
            }
        }
    }

    if ($url -eq '') {
        $blankCount++
    } else {
        $matchedCount++
    }

    $lines.Add("$($row.jis_code)`t$url`t$systemType")
}

[System.IO.Directory]::CreateDirectory((Split-Path -Parent $OutFile)) | Out-Null
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($OutFile, $lines, $utf8NoBom)

Write-Host ("Wrote {0} matched URLs and {1} blanks to {2}" -f $matchedCount, $blankCount, $OutFile)
