#!/bin/bash

# ==========================================================
# compare_rpms_http_robust.sh
# 支援多個檔案路徑的比對腳本
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

# 使用 sqlite3 查詢資料庫，並將結果中的路徑和大小寫入遠端清單
sqlite3 "$SQLITE_DB" "SELECT location_href, size_package FROM packages;" > "$REMOTE_LIST"

echo "💾 建立本地清單 (含大小)..."
LOCAL_LIST="$WORKDIR/local_full.list"
> "$LOCAL_LIST"

# 使用 find 命令遞歸尋找所有 .rpm 檔案
find "$LOCAL_DIR" -follow -type f -name "*.rpm" | while read -r file; do
    size=$(stat -c %s "$file")
    echo -e "$(basename "$file")\t$size" >> "$LOCAL_LIST"
done
sort -u -o "$LOCAL_LIST" "$LOCAL_LIST"

echo "🔍 比對差異..."
TO_DOWNLOAD="$WORKDIR/to_download.list"
> "$TO_DOWNLOAD"

while IFS=$'|' read -r full_path remotesize; do
    # 擷取遠端檔案的純粹檔名
    filename=$(basename "$full_path")
    # 在本地清單中尋找比對
    localsize=$(grep -P "^$filename\t" "$LOCAL_LIST" | cut -f2)

    if [ -z "$localsize" ] || [ "$localsize" != "$remotesize" ]; then
        echo "$full_path" >> "$TO_DOWNLOAD"
    fi
done < "$REMOTE_LIST"

TOTAL=$(wc -l < "$TO_DOWNLOAD")
COUNT=0

echo "📥 需要下載 $TOTAL 個檔案"
DOWNLOAD_DIR="$LOCAL_DIR/everything"
mkdir -p "$DOWNLOAD_DIR"

while read -r full_path; do
    COUNT=$((COUNT+1))
    echo "[$COUNT/$TOTAL] 下載: $full_path"
    # 使用 wget -c 來進行斷點續傳
    wget -q --show-progress -c "${REMOTE_URL}${full_path}" -P "$DOWNLOAD_DIR"
done < "$TO_DOWNLOAD"

echo "✅ 全部完成！"
echo "📄 下載清單: $TO_DOWNLOAD"
