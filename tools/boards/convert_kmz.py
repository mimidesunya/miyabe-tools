#!/usr/bin/env python3
"""Convert KMZ files in kmzs/ to tools/boards/data/<municipality>/data.tsv

使用方法:
  python tools/boards/convert_kmz.py              # 座標変換のみ
  python tools/boards/convert_kmz.py --geocode    # 座標なしの行を住所からジオコーディング
"""
import argparse
import json
import time
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


def load_api_key():
    """data/config.json から Google Maps API キーを読み込む"""
    root = os.path.join(os.path.dirname(__file__), '..', '..')
    config_path = os.path.join(root, 'data', 'config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        key = config.get('GOOGLE_MAPS_API_KEY', '')
        if key and key != 'YOUR_GOOGLE_MAPS_API_KEY_HERE':
            return key
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return None


def geocode_address(address, api_key):
    """住所を緯度経度に変換（Google Maps Geocoding API）"""
    import requests
    url = 'https://maps.googleapis.com/maps/api/geocode/json'
    params = {'address': address, 'key': api_key, 'language': 'ja', 'region': 'jp'}
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if data['status'] == 'OK' and data['results']:
            loc = data['results'][0]['geometry']['location']
            return str(loc['lat']), str(loc['lng'])
        if data['status'] != 'ZERO_RESULTS':
            print(f'  ✗ ジオコーディング失敗: {address} ({data["status"]})')
    except Exception as e:
        print(f'  ✗ エラー: {address}: {e}')
    return '', ''


# (keyword, output-dir, address-field, place-field, code-transform, address-prefix)
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
    ('伏見区',       'kyoto-fushimi-ku',    '設置場所',                    '設置名称',    None,             '京都府'),
    ('舞鶴市',       'maizuru-shi',         '住所',                        '設置場所',    None,             '京都府'),
    ('京田辺市',     'kyotanabe-shi',        '設置箇所',                    '説明',        None,             '京都府'),
    ('上京区',       'kyoto-kamigyo-ku',     '住所',                        '施設名',      None,             '京都府'),
    ('下京区',       'kyoto-shimogyo-ku',    '設置場所',                    '施設の名称',  None,             '京都府'),
    ('中京区',       'kyoto-nakagyo-ku',     '住所',                        '施設名',      None,             '京都府'),
    ('東山区',       'kyoto-higashiyama-ku', '設置場所（住所）',             '設置名称',    None,             '京都府'),
    ('八幡市',       'yawata-shi',           '設置場所',                    '備考',        None,             '京都府'),
    ('南丹市',       'nantan-shi',           '',                            '',            None,             ''),
    ('木津川市',     'kizugawa-shi',         'ポスター掲示場所在地',         '',            None,             '京都府'),
    ('宮津市',       'miyazu-shi',           '住所',                        'ポスター掲示場の設置場所', _strip_dot_zero, '京都府'),
    ('井手町',       'ide-cho',              '住所',                        'ポスター掲示場の設置場所', _strip_dot_zero, ''),
    ('南区',         'kyoto-minami-ku',      '設置場所',                    '設置名称',    None,             '京都府'),
    ('北区',         'kyoto-kita-ku',        '設置場所',                    '設置名称',    None,             '京都府'),
    ('左京区',       'kyoto-sakyo-ku',       '設置場所',                    '設置名称',    None,             '京都府'),
    ('与謝野町',     'yosano-cho',           '住所',                        '設置場所名称等', _strip_dot_zero, '京都府'),
    ('向日市',       'muko-shi',             '所在地',                      '備考',        _muko_code,       '京都府向日市'),
    ('京丹後市',     'kyotango-shi',         '所在地',                      '設置場所',    None,             '京都府'),
    ('西京区',       'kyoto-nishikyo-ku',    '設置場所',                    '設置名称',    None,             '京都府'),
    ('精華町',       'seika-cho',            '住所',                        'ポスター掲示場の設置場所', _strip_dot_zero, '京都府'),
    ('長岡京市',     'nagaokakyo-shi',       '設置場所',                    '設置名称',    None,             ''),
    ('久御山町',     'kumiyama-cho',         '所在地',                      '',            _strip_dot_zero,  '京都府久世郡久御山町'),
    ('宇治田原町',   'ujitawara-cho',        '住所',                        'ポスター掲示場の設置場所', _strip_dot_zero, ''),
]

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='KMZファイルからTSVへ変換')
    parser.add_argument('--geocode', action='store_true',
                        help='座標がない行を住所からジオコーディング（Google Maps API使用）')
    parser.add_argument('keyword', nargs='?', default=None,
                        help='処理対象のキーワード（省略時は全件処理）')
    args = parser.parse_args()

    api_key = None
    if args.geocode:
        api_key = load_api_key()
        if not api_key:
            print('エラー: data/config.json に有効な GOOGLE_MAPS_API_KEY を設定してください。')
            sys.exit(1)
        print('ジオコーディング: 有効')

    for keyword, dirname, addr_field, place_field, code_tf, addr_prefix in CONFIGS:
        if args.keyword and args.keyword not in keyword and args.keyword != dirname:
            continue
        path = find_kmz(keyword)
        if not path:
            print(f'NOT FOUND: {keyword}')
            continue
        placemarks = parse_kml(read_kml(path))
        rows = []
        geocoded_count = 0
        for p in placemarks:
            code = get_name(p)
            if code_tf:
                code = code_tf(code)
            addr = get_data(p, addr_field) if addr_field else ''
            place = get_data(p, place_field) if place_field else ''
            lat, lon = get_coords(p)
            # 座標がなく住所がある場合、ジオコーディングを試行
            if api_key and not lat and not lon and addr:
                full_addr = f'{addr_prefix}{addr}' if addr_prefix else addr
                print(f'  {dirname}/{code}: {full_addr} ...', end='', flush=True)
                lat, lon = geocode_address(full_addr, api_key)
                if lat and lon:
                    geocoded_count += 1
                    print(f' ✓ ({lat}, {lon})')
                else:
                    print(' ✗')
                time.sleep(0.1)
            rows.append([code, addr, place, lat, lon])
        write_tsv(rows, dirname)
        if geocoded_count:
            print(f'  → {geocoded_count} 件ジオコーディング')
    print('Done!')
