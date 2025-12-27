#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
掲示板データ（TSV）に緯度経度を付加するプログラム
Google Maps Geocoding APIを使用します。

使用方法:
1. data/config.json に GOOGLE_MAPS_API_KEY を設定してください。
2. python tools/boards/geocode_boards.py <入力TSV> <住所プレフィックス>
   例: python tools/boards/geocode_boards.py tools/boards/data/hino-shi/data.tsv "東京都日野市"
"""

import csv
import json
import os
import sys
import time
from pathlib import Path
import requests

def load_api_key():
    """data/config.json から API キーを読み込む"""
    root = Path(__file__).resolve().parents[2]
    config_path = root / "data" / "config.json"
    
    if not config_path.exists():
        print(f"エラー: 設定ファイルが見つかりません: {config_path}")
        return None
        
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("GOOGLE_MAPS_API_KEY")
    except Exception as e:
        print(f"エラー: 設定ファイルの読み込みに失敗しました: {e}")
        return None

def geocode(address, api_key):
    """住所を緯度経度に変換"""
    if not api_key or api_key == "YOUR_GOOGLE_MAPS_API_KEY_HERE":
        return None, None

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        'address': address, 
        'key': api_key,
        'language': 'ja',
        'region': 'jp'
    }
    
    try:
        r = requests.get(url, params=params)
        data = r.json()
        
        if data['status'] == 'OK' and data['results']:
            location = data['results'][0]['geometry']['location']
            return location['lat'], location['lng']
        else:
            if data['status'] != 'ZERO_RESULTS':
                print(f"  ✗ ジオコーディング失敗: {address} (ステータス: {data['status']})")
            return None, None
    except Exception as e:
        print(f"  ✗ エラー発生 {address}: {e}")
        return None, None

def main():
    if len(sys.argv) < 3:
        print("使用法: python geocode_boards.py <入力TSV> <住所プレフィックス>")
        print("例: python geocode_boards.py data.tsv \"東京都日野市\"")
        return

    input_path = Path(sys.argv[1])
    address_prefix = sys.argv[2]
    output_path = input_path.with_suffix(".geocoded.tsv")

    api_key = load_api_key()
    if not api_key or api_key == "YOUR_GOOGLE_MAPS_API_KEY_HERE":
        print("エラー: data/config.json に有効な GOOGLE_MAPS_API_KEY を設定してください。")
        return

    if not input_path.exists():
        print(f"エラー: 入力ファイルが見つかりません: {input_path}")
        return

    print(f"入力ファイル: {input_path}")
    print(f"住所プレフィックス: {address_prefix}")
    print(f"出力ファイル: {output_path}")
    print("処理を開始します...")

    rows = []
    header_found = False
    header = ['code', 'address', 'place', 'lat', 'lon']

    # ファイルの読み込み（プリアンブルを考慮）
    with open(input_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    data_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        parts = line.split('\t')
        if not header_found:
            if 'code' in parts and 'address' in parts:
                header_found = True
                # ヘッダー行を正規化
                header = parts
            continue
        
        if header_found:
            # カラム数が足りない場合は補完
            while len(parts) < len(header):
                parts.append('')
            data_lines.append(parts)

    if not header_found:
        print("エラー: TSVファイル内にヘッダー (code, address, ...) が見つかりませんでした。")
        return

    total = len(data_lines)
    success = 0

    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(header)

        # インデックスの特定
        try:
            idx_addr = header.index('address')
            idx_lat = header.index('lat')
            idx_lon = header.index('lon')
            idx_code = header.index('code')
        except ValueError as e:
            print(f"エラー: 必要なカラムが見つかりません: {e}")
            return

        for i, parts in enumerate(data_lines, 1):
            code = parts[idx_code]
            addr = parts[idx_addr]
            
            # すでに座標がある場合はスキップ（オプション）
            if parts[idx_lat] and parts[idx_lon]:
                print(f"[{i}/{total}] {code}: すでに座標があるためスキップします")
                writer.writerow(parts)
                success += 1
                continue

            full_address = f"{address_prefix}{addr}"
            print(f"[{i}/{total}] {code}: {full_address} をジオコーディング中...", end='', flush=True)
            
            lat, lon = geocode(full_address, api_key)
            
            if lat is not None and lon is not None:
                parts[idx_lat] = f"{lat:.6f}"
                parts[idx_lon] = f"{lon:.6f}"
                success += 1
                print(f" ✓ 成功 ({lat:.6f}, {lon:.6f})")
            else:
                print(" ✗ 失敗")
            
            writer.writerow(parts)
            time.sleep(0.1) # API制限への配慮

    print(f"\n完了！")
    print(f"総件数: {total}")
    print(f"成功: {success}")
    print(f"失敗: {total - success}")
    print(f"結果を {output_path} に保存しました。")

if __name__ == "__main__":
    main()
