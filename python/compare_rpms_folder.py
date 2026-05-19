#!/usr/bin/env python3
"""Compare RPM filenames in two local folders.

This is the Python replacement for compare_rpms_folder.sh. By default it only
writes reports. Add --delete-duplicates to delete duplicate RPM filenames from
DIR_B, matching the original shell script behavior.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def rpm_files_by_name(folder: Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for path in sorted(folder.rglob("*.rpm")):
        result.setdefault(path.name, []).append(path)
    return result


def write_list(path: Path, values: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for value in values:
            file.write(value + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare RPM filenames between two local folders.")
    parser.add_argument("dir_a", type=Path, nargs="?", help="First folder, usually the existing offline repo.")
    parser.add_argument("dir_b", type=Path, nargs="?", help="Second folder, usually the folder to clean.")
    parser.add_argument("--workdir", type=Path, default=Path("compare_tmp"), help="Report output folder.")
    parser.add_argument("--delete-duplicates", action="store_true", help="Delete duplicate RPM names from dir_b.")
    return parser.parse_args()


def ask_path(label: str) -> Path:
    return Path(input(label).strip().strip('"'))


def main() -> int:
    args = parse_args()
    dir_a = args.dir_a or ask_path("請輸入第一個資料夾路徑 (DIR_A): ")
    dir_b = args.dir_b or ask_path("請輸入第二個資料夾路徑 (DIR_B): ")

    if not dir_a.is_dir():
        print(f"錯誤：資料夾不存在: {dir_a}")
        return 1
    if not dir_b.is_dir():
        print(f"錯誤：資料夾不存在: {dir_b}")
        return 1

    rpms_a = rpm_files_by_name(dir_a)
    rpms_b = rpm_files_by_name(dir_b)
    names_a = set(rpms_a)
    names_b = set(rpms_b)

    duplicates = sorted(names_a & names_b)
    only_in_b = sorted(names_b - names_a)

    args.workdir.mkdir(parents=True, exist_ok=True)
    write_list(args.workdir / f"{dir_a.name}.list", sorted(names_a))
    write_list(args.workdir / f"{dir_b.name}.list", sorted(names_b))
    write_list(args.workdir / "duplicate_by_name.txt", duplicates)
    write_list(args.workdir / f"only_in_{dir_b.name}.txt", only_in_b)

    deleted: list[str] = []
    if args.delete_duplicates:
        for filename in duplicates:
            for path in rpms_b[filename]:
                path.unlink()
                deleted.append(str(path))
        write_list(args.workdir / "deleted.txt", deleted)

    print(f"DIR_A RPM: {len(names_a)}")
    print(f"DIR_B RPM: {len(names_b)}")
    print(f"重複檔名: {len(duplicates)}")
    print(f"只在 DIR_B: {len(only_in_b)}")
    print(f"報告位置: {args.workdir}")
    if args.delete_duplicates:
        print(f"已刪除 DIR_B 重複檔案: {len(deleted)}")
    else:
        print("未刪除檔案；若要刪除 DIR_B 重複 RPM，請加 --delete-duplicates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
