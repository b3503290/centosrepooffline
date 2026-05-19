#!/bin/bash
# === 設定 ===
ISO_PATH="/dev/cdrom"
MOUNT_DIR="/mnt/dvd"
WGET_DIR="/home/user/Downloads/9-stream"
WORKDIR="./compare_tmp"

# === 建立資料夾 ===
mkdir -p "$WORKDIR"

echo " 掛載 ISO..."
sudo mkdir -p "$MOUNT_DIR"
sudo mount -o loop "$ISO_PATH" "$MOUNT_DIR"

# === 產生檔案清單（只取檔名）===
echo " 建立 RPM 清單..."
find "$MOUNT_DIR" -type f -name "*.rpm" -exec basename {} \; | sort > "$WORKDIR/iso.list"
find "$WGET_DIR" -type f -name "*.rpm" -exec basename {} \; | sort > "$WORKDIR/wget.list"

# === 比對：重複檔名 ===
comm -12 "$WORKDIR/iso.list" "$WORKDIR/wget.list" > "$WORKDIR/duplicate_by_name.txt"

# === 比對：只在下載資料夾的 ===
comm -23 "$WORKDIR/wget.list" "$WORKDIR/iso.list" > "$WORKDIR/only_in_wget.txt"

# === 刪除重複檔案 ===
echo " 刪除重複檔案..."
while read -r filename; do
    fullpath=$(find "$WGET_DIR" -type f -name "$filename")
    if [ -n "$fullpath" ]; then
        echo "刪除：$fullpath"
        rm -f "$fullpath"
    fi
done < "$WORKDIR/duplicate_by_name.txt"

# === 輸出結果 ===
echo ""
echo " 比對報告："
echo "重複檔名（已刪除）: $WORKDIR/duplicate_by_name.txt"
echo "只在下載資料夾:      $WORKDIR/only_in_wget.txt"

# === 卸載 ISO ===
echo ""
read -p "按 Enter 卸載 ISO..."
sudo umount "$MOUNT_DIR"
