#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
川崎市例規集を差分ダウンロードするプログラム
"""

import os
import re
import time
import requests
from pathlib import Path

# 設定
BASE_URL = "http://www.reiki.city.kawasaki.jp/kawasaki/d1w_reiki/"
# 実行ディレクトリからの相対パス、または絶対パス
WORKSPACE_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = WORKSPACE_ROOT / "data" / "reiki" / "kawasaki"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DELAY = 0.5  # サーバー負荷軽減のための待機時間（秒）

def download_file(url, dest_path, force=False):
    """
    ファイルをダウンロードする。既に存在する場合はスキップする（force=Trueでない限り）。
    """
    if not force and dest_path.exists() and dest_path.stat().st_size > 0:
        return False
    
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT}
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        with open(dest_path, "wb") as f:
            f.write(response.content)
        print(f"Downloaded: {url}")
        time.sleep(DELAY)
        return True
    except Exception as e:
        print(f"Failed to download {url}: {e}")
        return False

def get_hno_list():
    """
    目次ページから例規番号（HNO）のリストを取得する。
    """
    hno_set = set()
    
    # 1. 目次インデックスを取得
    print("Fetching index pages...")
    download_file(BASE_URL + "mokuji_index_index.html", DATA_DIR / "mokuji_index_index.html", force=True)
    download_file(BASE_URL + "mokuji_bunya_index.html", DATA_DIR / "mokuji_bunya_index.html", force=True)
    
    # 2. 関連する目次・分類ページを再帰的に取得（簡易版）
    to_scan = ["mokuji_index_index.html", "mokuji_bunya_index.html"]
    scanned = set()
    
    while to_scan:
        current = to_scan.pop(0)
        if current in scanned:
            continue
        scanned.add(current)
        
        file_path = DATA_DIR / current
        # 存在しない場合はダウンロードを試みる
        if not file_path.exists():
            download_file(BASE_URL + current, file_path)
            
        if not file_path.exists():
            continue
            
        try:
            with open(file_path, "r", encoding="cp932", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
            continue
            
        # 他の目次ページを探す (index_*.html, bunya_*.html)
        links = re.findall(r'(index_\d+\.html|bunya_\d+\.html)', content)
        for link in links:
            if link not in scanned:
                to_scan.append(link)
        
        # 例規番号を抽出 (OpenResDataWin('HNO'))
        hnos = re.findall(r"OpenResDataWin\('([^']+)'\)", content)
        for hno in hnos:
            hno_set.add(hno)
                
    return sorted(list(hno_set))

def main():
    print(f"Target directory: {DATA_DIR}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    hno_list = get_hno_list()
    print(f"Found {len(hno_list)} unique regulation IDs.")
    
    count = 0
    for i, hno in enumerate(hno_list):
        # 本文（_j.html）のみをダウンロードする
        suffix = "_j"
        filename = f"{hno}{suffix}.html"
        url = f"{BASE_URL}{hno}/{filename}"
        # DATA_DIR直下に保存
        dest_path = DATA_DIR / filename
        
        if download_file(url, dest_path):
            count += 1
        
        if (i + 1) % 10 == 0:
            print(f"Progress: {i + 1}/{len(hno_list)} IDs processed...")
                
    print(f"Finished. Downloaded {count} new files.")

if __name__ == "__main__":
    main()
