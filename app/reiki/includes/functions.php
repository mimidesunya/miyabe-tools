<?php
declare(strict_types=1);

/**
 * Shared utility functions for the Reiki (ordinance) viewer.
 */

// Shared logic for score display (Color and Label)
function get_score_style_and_label(string $key, float|int $val): array {
    $style = '';
    $suffix = '';
    
    if ($key === 'necessity_score') {
        if ($val == -1) {
            $style = 'color:#94a3b8; font-style:italic;';
            $suffix = '(対象外)';
        } elseif ($val >= 80) {
            $style = 'color:#15803d; font-weight:bold;';
            $suffix = '(高)';
        } elseif ($val >= 50) {
            $style = 'color:#334155;';
        } elseif ($val >= 21) {
            $style = 'color:#b45309; font-weight:bold;';
            $suffix = '(低)';
        } else {
            $style = 'color:#dc2626; font-weight:bold; background:#fef2f2; padding:0 2px; border-radius:3px;';
            $suffix = '(不要?)';
        }
    } elseif ($key === 'fiscal_impact_score') {
        if ($val >= 4.0) {
            $style = 'color:#dc2626; font-weight:bold; background:#fef2f2; padding:0 2px; border-radius:3px;';
            $suffix = '(重)';
        } elseif ($val >= 2.0) {
            $style = 'color:#334155;'; 
        } else {
            $style = 'color:#15803d; font-weight:bold;';
            $suffix = '(軽)';
        }
    } elseif ($key === 'regulatory_burden_score') {
        if ($val >= 4.0) {
            $style = 'color:#dc2626; font-weight:bold; background:#fef2f2; padding:0 2px; border-radius:3px;';
            $suffix = '(重)';
        } elseif ($val >= 2.0) {
            $style = 'color:#334155;'; 
        } else {
            $style = 'color:#15803d; font-weight:bold;';
            $suffix = '(軽)';
        }
    } elseif ($key === 'policy_effectiveness_score') {
        if ($val >= 4.0) {
            $style = 'color:#15803d; font-weight:bold;';
            $suffix = '(高)';
        } elseif ($val >= 2.0) {
            $style = 'color:#334155;';
        } else {
            $style = 'color:#dc2626; font-weight:bold; background:#fef2f2; padding:0 2px; border-radius:3px;';
            $suffix = '(無効?)';
        }
    }
    
    return [$style, $suffix];
}

function h(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function decode_html_text(?string $value): string
{
    $decoded = html_entity_decode((string)($value ?? ''), ENT_QUOTES | ENT_HTML5, 'UTF-8');
    return trim((string)$decoded);
}

function get_stance_label(string $stance): string
{
    return match ($stance) {
        '合致', '適合' => '適合',
        '一部合致', '概ね適合' => '概ね適合',
        '中立/不明', '判断保留' => '判断保留',
        '衝突', '要見直し' => '要見直し',
        default => $stance,
    };
}

function normalize_document_type(?string $documentType): string
{
    $value = trim((string)$documentType);
    return match ($value) {
        '条例' => '条例',
        '規則' => '規則',
        '規程' => '規程',
        '要綱' => '要綱',
        default => 'その他',
    };
}

function read_text_auto(string $path): string
{
    $encodings = ['UTF-8', 'SJIS-win', 'CP932', 'EUC-JP', 'ISO-2022-JP'];
    $raw = @file_get_contents($path);
    if ($raw === false) {
        return '';
    }
    if (str_ends_with(strtolower($path), '.gz')) {
        $decoded = @gzdecode($raw);
        if ($decoded === false) {
            return '';
        }
        $raw = $decoded;
    }

    foreach ($encodings as $enc) {
        $converted = @mb_convert_encoding($raw, 'UTF-8', $enc);
        if ($converted !== false && $converted !== '') {
            return $converted;
        }
    }

    return (string)$raw;
}

function extract_title(string $html, string $fallback): string
{
    if (preg_match('/<title[^>]*>(.*?)<\/title>/is', $html, $m)) {
        $title = decode_html_text(strip_tags($m[1]));
        if ($title !== '') {
            return $title;
        }
    }

    if (preg_match('/○([^\r\n<]{2,120})/u', $html, $m)) {
        $title = decode_html_text($m[1]);
        if ($title !== '') {
            return $title;
        }
    }

    return decode_html_text($fallback);
}

function normalize_text(string $html): string
{
    $text = preg_replace('/<script\b[^>]*>.*?<\/script>/is', '', $html);
    $text = preg_replace('/<style\b[^>]*>.*?<\/style>/is', '', (string)$text);
    $text = preg_replace('/<br\s*\/?>/i', "\n", (string)$text);
    $text = strip_tags((string)$text);
    $text = html_entity_decode((string)$text, ENT_QUOTES | ENT_HTML5, 'UTF-8');
    $text = preg_replace("/\r\n|\r/", "\n", (string)$text);
    $text = preg_replace("/\n{3,}/", "\n\n", (string)$text);
    return trim((string)$text);
}

function get_score_html(string $key, float|int $val) {
    list($style, $suffix) = get_score_style_and_label($key, $val);
    return "<span style=\"{$style}\">" . h((string)$val) . " <span style=\"font-size:0.9em; opacity:0.9;\">{$suffix}</span></span>";
}

function inner_html(DOMNode $node): string {
    $html = '';
    foreach ($node->childNodes as $child) {
        $html .= $node->ownerDocument?->saveHTML($child) ?? '';
    }
    return $html;
}

function resolve_record_title(array $record, array &$cache): string {
    $name = (string)($record['name'] ?? '');
    if ($name !== '' && isset($cache[$name])) {
        return $cache[$name];
    }
    if (!empty($record['title'])) {
        $cache[$name] = decode_html_text((string)$record['title']);
        return $cache[$name];
    }
    
    $html = read_text_auto((string)$record['path']);
    $title = extract_title($html, $name !== '' ? $name : '無題');
    if ($name !== '') {
        $cache[$name] = $title;
    }
    return $title;
}

function sanitize_law_html(string $html, string $imageBaseUrl = '/data/reiki/14130-kawasaki-shi/images'): string
{
    $dom = new DOMDocument();
    libxml_use_internal_errors(true);
    $dom->loadHTML('<?xml encoding="utf-8" ?>' . $html, LIBXML_HTML_NOIMPLIED | LIBXML_HTML_NODEFDTD);
    libxml_clear_errors();

    $xpath = new DOMXPath($dom);
    foreach ($xpath->query('//script|//style') as $node) {
        if ($node && $node->parentNode) {
            $node->parentNode->removeChild($node);
        }
    }

    foreach ($xpath->query('//*') as $el) {
        if (!($el instanceof DOMElement)) {
            continue;
        }

        $toRemove = [];
        foreach ($el->attributes as $attr) {
            $name = strtolower($attr->name);
            if (str_starts_with($name, 'on')) {
                $toRemove[] = $attr->name;
            }
            if (($name === 'href' || $name === 'src') && preg_match('/^\s*javascript:/i', $attr->value)) {
                $toRemove[] = $attr->name;
            }
        }
        foreach ($toRemove as $name) {
            $el->removeAttribute($name);
        }
    }

    foreach ($xpath->query('//img[@src]') as $img) {
        if ($img instanceof DOMElement) {
            $src = str_replace('\\', '/', $img->getAttribute('src'));
            if ($src === '' || preg_match('#^(https?://|//)#i', $src)) {
                continue;
            }

            $filename = basename($src);
            if ($filename === '' || strtolower($filename) === 'download_default.gif') {
                continue;
            }

            $shouldRewrite = !str_contains($src, '/')
                || preg_match('#^\.\./(?:[a-z0-9_-]+_images|images)/#i', $src)
                || preg_match('#^(?:[a-z0-9_-]+_images|images)/#i', $src)
                || preg_match('#^/data/reiki/[a-z0-9_-]+(?:/images|_images)/#i', $src);

            if ($shouldRewrite) {
                $img->setAttribute('src', rtrim($imageBaseUrl, '/') . '/' . $filename);
            }
        }
    }

    return $dom->saveHTML() ?: '';
}

function extract_law_content_html(string $html, string $imageBaseUrl = '/data/reiki/14130-kawasaki-shi/images'): string
{
    $dom = new DOMDocument();
    libxml_use_internal_errors(true);
    $dom->loadHTML('<?xml encoding="utf-8" ?>' . $html, LIBXML_HTML_NOIMPLIED | LIBXML_HTML_NODEFDTD);
    libxml_clear_errors();

    $xpath = new DOMXPath($dom);
    $nodes = $xpath->query("//div[contains(concat(' ', normalize-space(@class), ' '), ' USER-SET-STYLE ')]");
    if ($nodes instanceof DOMNodeList && $nodes->length > 0) {
        $raw = inner_html($nodes->item(0));
        return sanitize_law_html($raw, $imageBaseUrl);
    }

    $body = $xpath->query('//body');
    if ($body instanceof DOMNodeList && $body->length > 0) {
        return sanitize_law_html(inner_html($body->item(0)), $imageBaseUrl);
    }

    return '';
}

function load_classification_for_record(array $record, string $htmlDir, string $classificationDir): ?array
{
    $htmlPath = (string)($record['path'] ?? '');
    if ($htmlPath === '') {
        return null;
    }
    $htmlReal = realpath($htmlPath);
    $baseReal = realpath($htmlDir);
    if ($htmlReal === false || $baseReal === false) {
        return null;
    }

    $prefix = rtrim($baseReal, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR;
    if (!str_starts_with($htmlReal, $prefix)) {
        return null;
    }

    $relative = substr($htmlReal, strlen($prefix));
    if ($relative === false || $relative === '') {
        return null;
    }

    $relativeJson = preg_replace('/\.html$/i', '.json', str_replace(['/', '\\'], DIRECTORY_SEPARATOR, $relative));
    if ($relativeJson === null || $relativeJson === '') {
        return null;
    }

    $classificationBasePath = rtrim($classificationDir, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . $relativeJson;
    $classificationPath = is_file($classificationBasePath . '.gz')
        ? $classificationBasePath . '.gz'
        : $classificationBasePath;
    if (!is_file($classificationPath)) {
        return null;
    }

    $json = read_text_auto($classificationPath);
    $row = json_decode($json, true);
    return is_array($row) ? $row : null;
}

function query_with(array $patch): string
{
    $params = $_GET;
    foreach ($patch as $k => $v) {
        if ($v === null) {
            unset($params[$k]);
        } else {
            $params[$k] = (string)$v;
        }
    }
    return '?' . http_build_query($params);
}
