#!/bin/bash

# ==========================================================
# compare_rpms_http_sqlite.sh
# 支援 primary.sqlite.xz 的 RPM 檔案比對腳本
# ==========================================================

# === 互動輸入 ===
read -rp "請輸入本地 RPM 資料夾路徑 (例如 ./epel_rpms): " LOCAL_DIR
read -rp "請輸入遠端 EPEL 網址 (例如 https://dl.fedoraproject.org/pub/epel/9/Everything/x86_64/): " REMOTE_URL

# === 驗證輸入 ===
if ! command -v sqlite3 &> /dev/null; then
    echo "❌ 錯誤: 未安裝 sqlite3。請使用 'sudo dnf install sqlite' 或 'sudo apt-get install sqlite3' 安裝。"
    exit 1
fi

if [ ! -d "$LOCAL_DIR" ]; then
    echo "❌ 錯誤: 本地資料夾不存在: $LOCAL_DIR"
    exit 1
fi

if [[ ! "$REMOTE_URL" =~ ^https?:// ]]; then
    echo "❌ 錯誤: 請輸入正確的網址 (http/https)"
    exit 1
fi

# 確保網址以 / 結尾
REMOTE_URL="${REMOTE_URL%/}/"

WORKDIR="./compare_tmp"
mkdir -p "$WORKDIR"

echo "🌐 從遠端取得 repomd.xml 檔案..."
REPODATA_URL="${REMOTE_URL}repodata/repomd.xml"
REPO_XML="$WORKDIR/repomd.xml"
curl -s "$REPODATA_URL" -o "$REPO_XML"

if [ ! -f "$REPO_XML" ]; then
    echo "❌ 錯誤: 無法下載 repomd.xml 或檔案不存在。"
    exit 1
fi

echo "🔍 解析 repomd.xml 獲取遠端檔案清單..."
REMOTE_LIST="$WORKDIR/remote_full.list"
> "$REMOTE_LIST"

# 修改 grep，尋找 primary.sqlite.xz
SQLITE_PATH=$(grep '<location href="' "$REPO_XML" | grep 'primary.sqlite.xz' | sed -E 's/.*href="([^"]+)".*/\1/')

if [ -z "$SQLITE_PATH" ]; then
    echo "❌ 錯誤: 無法在 repomd.xml 中找到 primary.sqlite.xz 的路徑。"
    exit 1
fi

echo "🌐 下載 primary.sqlite.xz..."
SQLITE_URL="${REMOTE_URL}${SQLITE_PATH}"
SQLITE_DB_XZ="$WORKDIR/primary.sqlite.xz"
curl -s "$SQLITE_URL" -o "$SQLITE_DB_XZ"

echo "🔍 解壓縮 primary.sqlite.xz 並使用 sqlite3 查詢..."
SQLITE_DB="$WORKDIR/primary.sqlite"
unxz -c "$SQLITE_DB_XZ" > "$SQLITE_DB"

if [ ! -f "$SQLITE_DB" ]; then
    echo "❌ 錯誤: 無法解壓縮 primary.sqlite.xz。"
    exit 1
fi

# 使用 sqlite3 查詢資料庫
sqlite3 "$SQLITE_DB" "SELECT location_href, size_package FROM packages;" | \
awk -F'|' '{print $1"\t"$2}' > "$REMOTE_LIST"

sort -u -o "$REMOTE_LIST" "$REMOTE_LIST"

# --- 本地清單和比對邏輯與先前版本相同 ---

echo "💾 建立本地清單 (含大小)..."

LOCAL_LIST="$WORKDIR/local_full.list"
> "$LOCAL_LIST"

# 使用 find 命令遞歸尋找所有 .rpm 檔案
find "$LOCAL_DIR" -type f -name "*.rpm" | while read -r file; do
    size=$(stat -c %s "$file")
    # 取得相對於 $LOCAL_DIR 的路徑
    relative_path="${file#$LOCAL_DIR/}"
    echo -e "${relative_path}\t${size}" >> "$LOCAL_LIST"
done

sort -u -o "$LOCAL_LIST" "$LOCAL_LIST"

echo "🔍 比對差異..."
TO_DOWNLOAD="$WORKDIR/to_download.list"
> "$TO_DOWNLOAD"

while IFS=$'\t' read -r filename remotesize; do
    # 這裡的 grep 改用 -w 確保精準匹配檔案名稱
    localsize=$(grep -w -P "^$(basename "$filename")\t" "$LOCAL_LIST" | cut -f2)
    if [ -z "$localsize" ] || [ "$localsize" != "$remotesize" ]; then
        echo "$filename" >> "$TO_DOWNLOAD"
    fi
done < "$REMOTE_LIST"

TOTAL=$(wc -l < "$TO_DOWNLOAD")
COUNT=0

echo "📥 需要下載 $TOTAL 個檔案"
DOWNLOAD_DIR="$LOCAL_DIR"
mkdir -p "$DOWNLOAD_DIR"

while read -r full_path; do
    COUNT=$((COUNT+1))
    echo "[$COUNT/$TOTAL] 下載: $full_path"
    wget -q --show-progress -c "${REMOTE_URL}${full_path}" -P "$DOWNLOAD_DIR"
done < "$TO_DOWNLOAD"

echo "✅ 全部完成！"
echo "📄 下載清單: $TO_DOWNLOAD"
