#!/bin/sh
# nginx のアクセスログとエラーログを、記録から30日以内に消す。
# プライバシーポリシー（/privacy/）で約束している保存期間はこの処理が守る。
#
# アクセスログは nginx が日ごとのファイル（access-YYYY-MM-DD.log）に書く。
# エラーログはファイル名に日付を使えないので、日付が変わったら中身を
# error-YYYY-MM-DD.log へ移して空にする。nginx は追記で開いているので、
# 空にしたあとも同じファイルに書き続けられる。
set -u

dir=/var/log/nginx/site
stamp="$dir/.error-log-rotated-on"

while true; do
    today=$(date -u +%F)
    if [ "$(cat "$stamp" 2>/dev/null)" != "$today" ]; then
        if [ -s "$dir/error.log" ]; then
            cat "$dir/error.log" >> "$dir/error-$today.log" && : > "$dir/error.log"
        fi
        echo "$today" > "$stamp"
    fi

    # 1日分のファイルの先頭は最後の書き込みより最大1日古い。最後の書き込みから
    # 29日を過ぎたら消せば、どの記録も30日以内に消える。
    find "$dir" -maxdepth 1 -type f \( -name 'access-*.log' -o -name 'error-*.log' \) -mtime +28 -delete

    sleep 600
done
