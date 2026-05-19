#!/bin/bash

# === 互動輸入 ===
read -rp "請輸入本地 RPM 資料夾路徑 (例如 ./epel_rpms): " LOCAL_DIR
read -rp "請輸入遠端 EPEL 網址 (例如 https://dl.fedoraproject.org/pub/epel/9/Everything/x86_64/Packages/): " REMOTE_URL

# === 驗證輸入 ===
if [ ! -d "$LOCAL_DIR" ]; then
    echo "❌ 錯誤: 本地資料夾不存在: $LOCAL_DIR"
    exit 1
fi

if [[ ! "$REMOTE_URL" =~ ^https?:// ]]; then
    echo "❌ 錯誤: 請輸入正確的網址 (http/https)"
    exit 1
fi

WORKDIR="./compare_tmp"
mkdir -p "$WORKDIR"

REMOTE_LIST="$WORKDIR/remote_full.list"
> "$REMOTE_LIST"

echo "🌐 從遠端取得檔案清單 (含子資料夾 + 檔案大小)..."

# 找出所有子資料夾 (a/, b/, c/... 等)
SUBDIRS=$(curl -s "$REMOTE_URL" | grep -oE 'href="[^"]+/"' | sed 's/href="//;s/"//')
for sub in $SUBDIRS; do
    curl -s "${REMOTE_URL}${sub}" | grep -oE '<a href="[^"]+\.rpm"' | \
        sed -E "s#.*href=\"([^\"]+)\".* \([0-9]+ bytes\).*#\1#" > "$WORKDIR/tmp_rpms.list"

    # 抓取檔案大小（用 curl -sI）
    while read -r rpmfile; do
        size=$(curl -sI "${REMOTE_URL}${sub}${rpmfile}" | grep -i Content-Length | awk '{print $2}' | tr -d '\r')
        echo -e "${rpmfile}\t${size}" >> "$REMOTE_LIST"
    done < "$WORKDIR/tmp_rpms.list"
done

sort -u -o "$REMOTE_LIST" "$REMOTE_LIST"

echo "💾 建立本地清單 (含大小)..."
LOCAL_LIST="$WORKDIR/local_full.list"
> "$LOCAL_LIST"
for file in "$LOCAL_DIR"/*.rpm; do
    [ -e "$file" ] || continue
    size=$(stat -c %s "$file")
    echo -e "$(basename "$file")\t$size" >> "$LOCAL_LIST"
done
sort -u -o "$LOCAL_LIST" "$LOCAL_LIST"

echo "🔍 比對差異..."
# 找出需要下載或大小不一致的檔案
TO_DOWNLOAD="$WORKDIR/to_download.list"
> "$TO_DOWNLOAD"

while IFS=$'\t' read -r filename remotesize; do
    localsize=$(grep -P "^$filename\t" "$LOCAL_LIST" | cut -f2)
    if [ -z "$localsize" ] || [ "$localsize" != "$remotesize" ]; then
        echo "$filename" >> "$TO_DOWNLOAD"
    fi
done < "$REMOTE_LIST"

TOTAL=$(wc -l < "$TO_DOWNLOAD")
COUNT=0

echo "📥 需要下載 $TOTAL 個檔案"
DOWNLOAD_DIR="$LOCAL_DIR/downloaded_rpms"
mkdir -p "$DOWNLOAD_DIR"

while read -r filename; do
    COUNT=$((COUNT+1))
    echo "[$COUNT/$TOTAL] 下載: $filename"
    wget -q --show-progress -c "${REMOTE_URL}${filename}" -P "$DOWNLOAD_DIR"
done < "$TO_DOWNLOAD"

echo "✅ 全部完成！"
echo "📄 下載清單: $TO_DOWNLOAD"
