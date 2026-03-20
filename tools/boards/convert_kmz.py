#!/usr/bin/env python3
"""Convert KMZ files in kmzs/ to tools/boards/data/<municipality>/data.tsv"""
import zipfile, os, sys
import xml.etree.ElementTree as ET

sys.stdout.reconfigure(encoding='utf-8')

KML_NS = 'http://www.opengis.net/kml/2.2'
BASE_KMZ = 'F:/dev/mimidesunya-public/miyabe-tools/kmzs'
BASE_OUT = 'F:/dev/mimidesunya-public/miyabe-tools/tools/boards/data'


def get_data(placemark, name):
    for d in placemark.findall(f'.//{{{KML_NS}}}Data'):
        if d.get('name') == name:
            v = d.find(f'{{{KML_NS}}}value')
            if v is not None and v.text:
                # Collapse newlines/tabs to spaces to avoid TSV row breaks
                return ' '.join(v.text.split())
    return ''


def get_coords(placemark):
    coords = placemark.find(f'.//{{{KML_NS}}}coordinates')
    if coords is not None and coords.text:
        parts = coords.text.strip().split(',')
        if len(parts) >= 2:
            return parts[1].strip(), parts[0].strip()
    return '', ''


def parse_kml(content):
    return ET.fromstring(content).findall(f'.//{{{KML_NS}}}Placemark')


def read_kml(path):
    with zipfile.ZipFile(path) as z:
        with z.open('doc.kml') as f:
            return f.read().decode('utf-8')


def write_tsv(rows, dirname):
    out = os.path.join(BASE_OUT, dirname)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'data.tsv'), 'w', encoding='utf-8', newline='') as f:
        f.write('code\taddress\tplace\tlat\tlon\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    print(f'{dirname}: {len(rows)} rows')


def get_name(p):
    n = p.find(f'{{{KML_NS}}}name')
    return n.text.strip() if n is not None and n.text else ''


def find_kmz(keyword):
    for name in os.listdir(BASE_KMZ):
        if name.endswith('.kmz') and keyword in name:
            return os.path.join(BASE_KMZ, name)
    return None


# (keyword, output-dir, address-field, place-field, code-transform)
# code-transform: optional callable to convert the raw <name> to a code string
def _strip_dot_zero(s):
    """Convert '1.0' -> '1', '12.0' -> '12', etc."""
    try:
        return str(int(float(s)))
    except ValueError:
        return s


def _muko_code(s):
    """Convert '1一1' -> '1-1' (全角区切りを半角ハイフンに)"""
    return s.replace('一', '-')


CONFIGS = [
    ('伏見区',       'kyoto-fushimi-ku',    '設置場所',                    '設置名称',    None),
    ('舞鶴市',       'maizuru-shi',         '住所',                        '設置場所',    None),
    ('京田辺市',     'kyotanabe-shi',        '設置箇所',                    '説明',        None),
    ('上京区',       'kyoto-kamigyo-ku',     '住所',                        '施設名',      None),
    ('下京区',       'kyoto-shimogyo-ku',    '設置場所',                    '施設の名称',  None),
    ('中京区',       'kyoto-nakagyo-ku',     '住所',                        '施設名',      None),
    ('東山区',       'kyoto-higashiyama-ku', '設置場所（住所）',             '設置名称',    None),
    ('八幡市',       'yawata-shi',           '設置場所',                    '備考',        None),
    ('南丹市',       'nantan-shi',           '',                            '',            None),
    ('木津川市',     'kizugawa-shi',         'ポスター掲示場所在地',         '',            None),
    ('宮津市',       'miyazu-shi',           '住所',                        'ポスター掲示場の設置場所', _strip_dot_zero),
    ('井手町',       'ide-cho',              '住所',                        'ポスター掲示場の設置場所', _strip_dot_zero),
    ('南区',         'kyoto-minami-ku',      '設置場所',                    '設置名称',    None),
    ('北区',         'kyoto-kita-ku',        '設置場所',                    '設置名称',    None),
    ('左京区',       'kyoto-sakyo-ku',       '設置場所',                    '設置名称',    None),
    ('与謝野町',     'yosano-cho',           '住所',                        '設置場所名称等', _strip_dot_zero),
    ('向日市',       'muko-shi',             '所在地',                      '備考',        _muko_code),
    ('京丹後市',     'kyotango-shi',         '所在地',                      '設置場所',    None),
    ('西京区',       'kyoto-nishikyo-ku',    '設置場所',                    '設置名称',    None),
    ('精華町',       'seika-cho',            '住所',                        'ポスター掲示場の設置場所', _strip_dot_zero),
    ('長岡京市',     'nagaokakyo-shi',       '設置場所',                    '設置名称',    None),
    ('久御山町',     'kumiyama-cho',         '所在地',                      '',            _strip_dot_zero),
    ('宇治田原町',   'ujitawara-cho',        '住所',                        'ポスター掲示場の設置場所', _strip_dot_zero),
]

if __name__ == '__main__':
    for keyword, dirname, addr_field, place_field, code_tf in CONFIGS:
        path = find_kmz(keyword)
        if not path:
            print(f'NOT FOUND: {keyword}')
            continue
        placemarks = parse_kml(read_kml(path))
        rows = []
        for p in placemarks:
            code = get_name(p)
            if code_tf:
                code = code_tf(code)
            addr = get_data(p, addr_field) if addr_field else ''
            place = get_data(p, place_field) if place_field else ''
            lat, lon = get_coords(p)
            rows.append([code, addr, place, lat, lon])
        write_tsv(rows, dirname)
    print('Done!')
