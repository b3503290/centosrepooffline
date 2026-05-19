#!/bin/bash

# === 手動輸入兩個資料夾路徑 ===
read -rp "請輸入第一個資料夾路徑 (DIR_A，例如 ./CentOS_Offline): " DIR_A
read -rp "請輸入第二個資料夾路徑，比對後會刪除 (DIR_B，例如 ./Packages): " DIR_B

# === 驗證資料夾是否存在 ===
if [ ! -d "$DIR_A" ] || [ ! -d "$DIR_B" ]; then
    echo "❌ 錯誤：其中一個資料夾不存在"
    exit 1
fi

# === 提取資料夾名稱 ===
NAME_A=$(basename "$DIR_A")
NAME_B=$(basename "$DIR_B")
WORKDIR="./compare_tmp"
mkdir -p "$WORKDIR"

# === 產生 RPM 檔案清單（僅取檔名）===
echo "📦 建立 RPM 清單..."
find "$DIR_A" -follow -type f -name "*.rpm" -exec basename {} \; | sort > "$WORKDIR/${NAME_A}.list"
find "$DIR_B" -follow -type f -name "*.rpm" -exec basename {} \; | sort > "$WORKDIR/${NAME_B}.list"

# === 比對：重複檔名 ===
comm -12 "$WORKDIR/${NAME_A}.list" "$WORKDIR/${NAME_B}.list" > "$WORKDIR/duplicate_by_name.txt"

# === 比對：只存在於 DIR_B 的檔案 ===
comm -23 "$WORKDIR/${NAME_B}.list" "$WORKDIR/${NAME_A}.list" > "$WORKDIR/only_in_${NAME_B}.txt"

# === 刪除 DIR_B 中重複的檔案 ===
echo "🗑️ 刪除 ${DIR_B} 中重複的檔案..."
while read -r filename; do
    fullpath=$(find "$DIR_B" -type f -name "$filename")
    if [ -n "$fullpath" ]; then
        echo "刪除：$fullpath"
        rm -f "$fullpath"
    fi
done < "$WORKDIR/duplicate_by_name.txt"

# === 輸出結果 ===
echo ""
echo "📄 比對報告："
echo "重複檔名（已刪除）: $WORKDIR/duplicate_by_name.txt"
echo "只在 ${DIR_B} 的檔案: $WORKDIR/only_in_${NAME_B}.txt"
