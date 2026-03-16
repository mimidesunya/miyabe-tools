param(
    [string]$SourceUrl = 'https://www.gsi.go.jp/KOKUJYOHO/MENCHO/backnumber/R7_10_mencho.csv',
    [string]$SourceCsvPath,
    [string]$OutFile = (Join-Path $PSScriptRoot '..\..\data\japan_local_governments.tsv')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path)
}

function Convert-ReiwaDateToIso {
    param([Parameter(Mandatory = $true)][string]$Label)

    if ($Label -notmatch '^令和(?<year>\d+)年(?<month>\d+)月(?<day>\d+)日') {
        throw "Could not parse source date label: $Label"
    }

    $year = 2018 + [int]$Matches['year']
    $month = [int]$Matches['month']
    $day = [int]$Matches['day']

    return '{0:D4}-{1:D2}-{2:D2}' -f $year, $month, $day
}

function Get-SourceCsvLines {
    param(
        [Parameter(Mandatory = $true)][string]$CsvPath,
        [Parameter(Mandatory = $true)][string]$DownloadUrl
    )

    if (-not (Test-Path -LiteralPath $CsvPath)) {
        $directory = Split-Path -Parent $CsvPath
        if ($directory) {
            [System.IO.Directory]::CreateDirectory($directory) | Out-Null
        }
        Invoke-WebRequest -Uri $DownloadUrl -OutFile $CsvPath
    }

    $encoding = [System.Text.Encoding]::GetEncoding(932)
    return [System.IO.File]::ReadAllLines($CsvPath, $encoding)
}

function Convert-SourceRows {
    param([Parameter(Mandatory = $true)][string[]]$Lines)

    $headerIndex = -1
    for ($i = 0; $i -lt $Lines.Length; $i++) {
        if ($Lines[$i].StartsWith('標準地域コード,')) {
            $headerIndex = $i
            break
        }
    }

    if ($headerIndex -lt 0) {
        throw 'Could not find the CSV header row.'
    }

    $records = $Lines[$headerIndex..($Lines.Length - 1)] | ConvertFrom-Csv
    if (-not $records -or $records.Count -eq 0) {
        throw 'The source CSV did not contain any records.'
    }

    $sourceDateLabel = $records[0].PSObject.Properties.Name |
        Where-Object { $_ -match '^令和\d+年\d+月\d+日\(k㎡\)$' } |
        Select-Object -First 1

    if (-not $sourceDateLabel) {
        throw 'Could not determine the source basis date from the CSV header.'
    }

    $sourceDate = Convert-ReiwaDateToIso -Label $sourceDateLabel
    $rows = New-Object System.Collections.Generic.List[object]

    foreach ($record in $records) {
        $jisCode = [string]$record.'標準地域コード'
        $prefName = [string]$record.'都道府県'
        $districtName = [string]$record.'郡･支庁･振興局等'
        $municipalityName = [string]$record.'市区町村'

        $jisCode = $jisCode.Trim()
        $prefName = $prefName.Trim()
        $districtName = $districtName.Trim()
        $municipalityName = $municipalityName.Trim()

        if ([string]::IsNullOrWhiteSpace($jisCode)) {
            continue
        }

        if ([string]::IsNullOrWhiteSpace($municipalityName) -and $jisCode -notmatch '000$') {
            continue
        }

        if ($municipalityName -match '^\(.+\).+区$') {
            continue
        }

        $entityType = if ([string]::IsNullOrWhiteSpace($municipalityName)) {
            'prefecture'
        } elseif ($prefName -eq '東京都' -and $municipalityName -match '区$') {
            'special_ward'
        } else {
            'municipality'
        }

        $name = if ($entityType -eq 'prefecture') { $prefName } else { $municipalityName }
        $prefCode = $jisCode.Substring(0, 2)
        $parentJisCode = if ($entityType -eq 'prefecture') { '' } else { '{0}000' -f $prefCode }
        $fullName = if ($entityType -eq 'prefecture') { $prefName } else { "$prefName $municipalityName" }

        $rows.Add([PSCustomObject]@{
                source_date = $sourceDate
                entity_type = $entityType
                jis_code = $jisCode
                parent_jis_code = $parentJisCode
                pref_code = $prefCode
                pref_name = $prefName
                district_name = $districtName
                name = $name
                full_name = $fullName
            })
    }

    return $rows
}

function ConvertTo-TsvLine {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [AllowEmptyString()]
        [object[]]$Values
    )

    return (($Values | ForEach-Object {
                ([string]$_).Replace("`t", ' ').Replace("`r", ' ').Replace("`n", ' ')
            }) -join "`t")
}

if (-not $SourceCsvPath) {
    $SourceCsvPath = Join-Path $env:TEMP (Split-Path -Leaf $SourceUrl)
}

$SourceCsvPath = Resolve-FullPath -Path $SourceCsvPath
$OutFile = Resolve-FullPath -Path $OutFile

$sourceLines = Get-SourceCsvLines -CsvPath $SourceCsvPath -DownloadUrl $SourceUrl
$rows = Convert-SourceRows -Lines $sourceLines

$columns = @(
    'source_date',
    'entity_type',
    'jis_code',
    'parent_jis_code',
    'pref_code',
    'pref_name',
    'district_name',
    'name',
    'full_name'
)

$tsvLines = New-Object System.Collections.Generic.List[string]
$tsvLines.Add((ConvertTo-TsvLine -Values $columns))

foreach ($row in ($rows | Sort-Object jis_code)) {
    $values = foreach ($column in $columns) {
        [string]$row.$column
    }
    $tsvLines.Add((ConvertTo-TsvLine -Values $values))
}

[System.IO.Directory]::CreateDirectory((Split-Path -Parent $OutFile)) | Out-Null
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($OutFile, $tsvLines, $utf8NoBom)

Write-Host ("Wrote {0} rows to {1}" -f $rows.Count, $OutFile)
