param(
    [string]$MasterTsv = (Join-Path $PSScriptRoot '..\..\data\municipalities\municipality_master.tsv'),
    [string]$MunicipalitySourceUrl = 'https://raw.githubusercontent.com/code4fukui/localgovjp/master/localgovjp-utf8.csv',
    [string]$PrefectureSourceUrl = 'https://raw.githubusercontent.com/code4fukui/localgovjp/master/prefjp-utf8.csv',
    [string]$MunicipalitySourceCsvPath,
    [string]$PrefectureSourceCsvPath,
    [string]$OutFile = (Join-Path $PSScriptRoot '..\..\data\municipalities\municipality_homepages.csv')
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

function Get-SourceRows {
    param(
        [Parameter(Mandatory = $true)][string]$CsvPath,
        [Parameter(Mandatory = $true)][string]$DownloadUrl
    )

    if (-not (Test-Path -LiteralPath $CsvPath)) {
        $directory = Split-Path -Parent $CsvPath
        if ($directory) {
            [System.IO.Directory]::CreateDirectory($directory) | Out-Null
        }
        Invoke-WebRequest -Uri $DownloadUrl -Headers $script:DefaultHeaders -OutFile $CsvPath
    }

    return Import-Csv -LiteralPath $CsvPath
}

function Escape-CsvValue {
    param([AllowEmptyString()][string]$Value)

    if ($null -eq $Value) {
        return ''
    }

    if ($Value.Contains('"')) {
        $Value = $Value.Replace('"', '""')
    }

    if ($Value.IndexOfAny([char[]]@(',', '"', "`r", "`n")) -ge 0) {
        return '"' + $Value + '"'
    }

    return $Value
}

function ConvertTo-CsvLine {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [AllowEmptyString()]
        [object[]]$Values
    )

    return (($Values | ForEach-Object {
                Escape-CsvValue -Value ([string]$_)
            }) -join ',')
}

$MasterTsv = Resolve-FullPath -Path $MasterTsv
$OutFile = Resolve-FullPath -Path $OutFile

if (-not $MunicipalitySourceCsvPath) {
    $MunicipalitySourceCsvPath = Join-Path $env:TEMP (Split-Path -Leaf $MunicipalitySourceUrl)
}
if (-not $PrefectureSourceCsvPath) {
    $PrefectureSourceCsvPath = Join-Path $env:TEMP (Split-Path -Leaf $PrefectureSourceUrl)
}

$MunicipalitySourceCsvPath = Resolve-FullPath -Path $MunicipalitySourceCsvPath
$PrefectureSourceCsvPath = Resolve-FullPath -Path $PrefectureSourceCsvPath

$masterRows = Import-Csv -LiteralPath $MasterTsv -Delimiter "`t"
$municipalitySourceRows = Get-SourceRows -CsvPath $MunicipalitySourceCsvPath -DownloadUrl $MunicipalitySourceUrl
$prefectureSourceRows = Get-SourceRows -CsvPath $PrefectureSourceCsvPath -DownloadUrl $PrefectureSourceUrl

$municipalityUrlByCode = @{}
foreach ($row in $municipalitySourceRows) {
    $lgcode = [string]$row.lgcode
    $url = [string]$row.url
    if ($lgcode.Length -lt 5 -or [string]::IsNullOrWhiteSpace($url)) {
        continue
    }

    $jisCode = $lgcode.Substring(0, 5)
    if (-not $municipalityUrlByCode.ContainsKey($jisCode)) {
        $municipalityUrlByCode[$jisCode] = $url.Trim()
    }
}

$prefectureUrlByCode = @{}
foreach ($row in $prefectureSourceRows) {
    $url = [string]$row.url
    if ([string]::IsNullOrWhiteSpace($url)) {
        continue
    }

    $jisCode = '{0:D2}000' -f [int]$row.pid
    if (-not $prefectureUrlByCode.ContainsKey($jisCode)) {
        $prefectureUrlByCode[$jisCode] = $url.Trim()
    }
}

$csvLines = New-Object System.Collections.Generic.List[string]
$csvLines.Add((ConvertTo-CsvLine -Values @('jis_code', 'url')))

$matchedCount = 0
$blankCount = 0

foreach ($row in ($masterRows | Sort-Object jis_code)) {
    $jisCode = [string]$row.jis_code
    $url = if ([string]$row.entity_type -eq 'prefecture') {
        [string]($prefectureUrlByCode[$jisCode] ?? '')
    } else {
        [string]($municipalityUrlByCode[$jisCode] ?? '')
    }

    if ([string]::IsNullOrWhiteSpace($url)) {
        $blankCount++
        $url = ''
    } else {
        $matchedCount++
    }

    $csvLines.Add((ConvertTo-CsvLine -Values @($jisCode, $url)))
}

[System.IO.Directory]::CreateDirectory((Split-Path -Parent $OutFile)) | Out-Null
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($OutFile, $csvLines, $utf8NoBom)

Write-Host ("Wrote {0} matched URLs and {1} blanks to {2}" -f $matchedCount, $blankCount, $OutFile)
