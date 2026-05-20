#!/usr/bin/env python3
"""Compare local RPM folders with remote CentOS repos and download new RPMs.

Default workflow:
1. Ask for an ISO file path or mounted DVD/ISO folder path.
2. Ask for the local download folder and create repo/Packages folders as needed.
3. Ask for the remote CentOS base URL, such as https://mirror.stream.centos.org/9-stream/.
4. Find repo folders under the base URL and read their x86_64/os repository metadata.
5. Download RPMs missing from both the DVD/ISO and the download folder.
6. Cache local RPM lists under python/temp and write daily logs under centos9/log/YYYY-MM-DD.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
from html.parser import HTMLParser
import lzma
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DOWNLOAD_ROOT = PROJECT_ROOT / "centos9"
DEFAULT_BASE_URL = "https://mirror.stream.centos.org/9-stream/"
DEFAULT_CACHE_DIR = SCRIPT_DIR / "temp"
REPO_NS = {"repo": "http://linux.duke.edu/metadata/repo"}
COMMON_NS = {"common": "http://linux.duke.edu/metadata/common"}
PACKAGES_DIR = "Packages"


@dataclass(frozen=True)
class LocalRpm:
    repo: str
    path: Path
    relative_path: str
    filename: str
    size: int


@dataclass(frozen=True)
class RemoteRpm:
    repo: str
    href: str
    filename: str
    size: int
    checksum_type: str | None
    checksum: str | None


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value)


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("URL is empty")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"URL must start with http:// or https://: {url}")
    return url.rstrip("/") + "/"


def prompt_base_url(default: str = DEFAULT_BASE_URL) -> str:
    while True:
        print(f"預設 CentOS base URL: {default}")
        answer = input("使用這個網址嗎？輸入 Y 使用，或直接輸入其他網址: ").strip()
        if answer.lower() in {"y", "yes"}:
            return normalize_url(default)
        if answer:
            return normalize_url(answer)
        print("請輸入 Y 或其他網址。")


def infer_download_root(base_url: str | None) -> Path:
    if not base_url:
        return DEFAULT_DOWNLOAD_ROOT

    parsed = urlparse(base_url)
    folder_name = Path(parsed.path.strip("/")).name
    if folder_name.endswith("-stream"):
        major_version = folder_name.split("-", 1)[0]
        if major_version.isdigit():
            return PROJECT_ROOT / f"centos{major_version}"
    if folder_name:
        safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in folder_name)
        return PROJECT_ROOT / safe_name
    return DEFAULT_DOWNLOAD_ROOT


def absolute_path(path: Path) -> Path:
    return path.expanduser().resolve()


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_powershell(command: str) -> str:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(message)
    return result.stdout.strip()


def mount_iso(iso_path: Path) -> Path:
    if os.name != "nt":
        raise RuntimeError("ISO 自動掛載目前只支援 Windows；Linux 請先自行掛載 ISO，再用 --disc-root 指向掛載路徑。")

    resolved_iso = absolute_path(iso_path)
    if not resolved_iso.is_file():
        raise FileNotFoundError(f"找不到 ISO 檔案: {resolved_iso}")

    quoted_iso = powershell_quote(str(resolved_iso))
    command = (
        f"$ImagePath = {quoted_iso}; "
        "$null = Mount-DiskImage -ImagePath $ImagePath; "
        "Start-Sleep -Milliseconds 500; "
        "$Volume = Get-DiskImage -ImagePath $ImagePath | Get-Volume | Select-Object -First 1; "
        "if (-not $Volume -or -not $Volume.DriveLetter) { throw 'ISO 已掛載，但找不到磁碟代號。' }; "
        "Write-Output ($Volume.DriveLetter + ':\\')"
    )
    drive_root = run_powershell(command).splitlines()[-1].strip()
    return Path(drive_root)


def dismount_iso(iso_path: Path) -> None:
    if os.name != "nt":
        return
    resolved_iso = absolute_path(iso_path)
    quoted_iso = powershell_quote(str(resolved_iso))
    run_powershell(f"Dismount-DiskImage -ImagePath {quoted_iso}")


def request_bytes(url: str, timeout: int) -> bytes:
    request = Request(url, headers={"User-Agent": "centosrepooffline-python/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def request_text(url: str, timeout: int) -> str:
    return request_bytes(url, timeout).decode("utf-8", errors="replace")


def discover_repo_names(base_url: str, timeout: int) -> list[str]:
    parser = LinkParser()
    parser.feed(request_text(base_url, timeout))

    names: list[str] = []
    seen: set[str] = set()
    for href in parser.hrefs:
        if href.startswith(("/", "?", "#")) or "://" in href:
            continue
        if not href.endswith("/"):
            continue
        name = href.strip("/").split("/")[0]
        if not name or name == ".." or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def parse_repo_arg(value: str) -> list[str]:
    if value.strip().lower() == "auto":
        return []
    repos = [item.strip() for item in value.split(",") if item.strip()]
    if not repos:
        raise ValueError("--repos 必須是 auto，或至少包含一個 repo 名稱")
    return repos


def build_repo_urls(args: argparse.Namespace) -> dict[str, str]:
    explicit_urls = {
        "AppStream": args.appstream_url,
        "BaseOS": args.baseos_url,
    }
    if any(explicit_urls.values()):
        missing = [repo for repo, url in explicit_urls.items() if not url]
        if missing:
            raise ValueError(f"Missing explicit URL for: {', '.join(missing)}")
        return {repo: normalize_url(url) for repo, url in explicit_urls.items() if url}

    base_url = args.base_url
    if not base_url:
        base_url = prompt_base_url()
    base_url = normalize_url(base_url)

    print(f"讀取 base URL 目錄: {base_url}")
    remote_repo_names = discover_repo_names(base_url, args.timeout)
    requested_repos = parse_repo_arg(args.repos)
    if not requested_repos:
        print(f"自動找到 repo: {', '.join(remote_repo_names)}")
        return {
            repo: urljoin(base_url, f"{repo}/{args.arch}/os/")
            for repo in remote_repo_names
        }

    found = [repo for repo in requested_repos if repo in remote_repo_names]
    if not found:
        raise RuntimeError(
            "Cannot find requested repo folders under base URL. "
            f"Requested={requested_repos}, found={remote_repo_names}"
        )

    missing = [repo for repo in requested_repos if repo not in remote_repo_names]
    if missing:
        print(f"略過 base URL 下不存在的 repo: {', '.join(missing)}")

    return {
        repo: urljoin(base_url, f"{repo}/{args.arch}/os/")
        for repo in found
    }


def scan_local_repo(local_root: Path, repo: str, create_missing: bool) -> list[LocalRpm]:
    repo_root = local_root / repo
    packages_root = repo_root / PACKAGES_DIR
    if create_missing:
        packages_root.mkdir(parents=True, exist_ok=True)
    elif not packages_root.is_dir():
        return []

    rpms: list[LocalRpm] = []
    for path in sorted(packages_root.rglob("*.rpm")):
        relative_path = path.relative_to(repo_root).as_posix()
        rpms.append(
            LocalRpm(
                repo=repo,
                path=path,
                relative_path=relative_path,
                filename=path.name,
                size=path.stat().st_size,
            )
        )
    return rpms


def path_belongs_to_repo(path: Path | str, root: Path, repo: str) -> bool:
    repo_root_text = os.path.normcase(os.path.normpath(str(root / repo))).rstrip("\\/")
    path_text = os.path.normcase(os.path.normpath(str(path)))
    return path_text == repo_root_text or path_text.startswith(repo_root_text + "\\")


def local_cache_path(cache_dir: Path, source: str, repo: str) -> Path:
    return cache_dir / f"{source}_{repo}_local_rpms.tsv"


def load_local_cache(cache_dir: Path, source: str, local_root: Path, repo: str) -> list[LocalRpm] | None:
    cache_path = local_cache_path(cache_dir, source, repo)
    if not cache_path.is_file():
        return None

    rpms: list[LocalRpm] = []
    with cache_path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 4:
                print(f"快取格式不正確，重新掃描: {cache_path}:{line_number}")
                return None
            relative_path, filename, size_text, path_text = parts
            path = Path(path_text)
            if not path_belongs_to_repo(path_text, local_root, repo):
                print(f"快取路徑不屬於目前 local-root，重新掃描: {cache_path}")
                return None
            try:
                size = int(size_text)
            except ValueError:
                print(f"快取大小欄位不正確，重新掃描: {cache_path}:{line_number}")
                return None
            rpms.append(
                LocalRpm(
                    repo=repo,
                    path=path,
                    relative_path=relative_path,
                    filename=filename,
                    size=size,
                )
            )
    return rpms


def save_local_cache(cache_dir: Path, source: str, repo: str, rpms: list[LocalRpm]) -> None:
    write_tsv(
        local_cache_path(cache_dir, source, repo),
        [(rpm.relative_path, rpm.filename, rpm.size, rpm.path) for rpm in sorted(rpms, key=lambda item: item.relative_path)],
    )


def get_local_rpms(
    local_root: Path,
    repo: str,
    cache_dir: Path,
    cache_source: str,
    refresh_cache: bool,
    create_missing: bool,
) -> tuple[list[LocalRpm], str]:
    if not refresh_cache:
        cached = load_local_cache(cache_dir, cache_source, local_root, repo)
        if cached is not None:
            return cached, "cache"

    rpms = scan_local_repo(local_root, repo, create_missing)
    save_local_cache(cache_dir, cache_source, repo, rpms)
    return rpms, "scan"


def write_tsv(path: Path, rows: list[tuple[object, ...]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write("\t".join(str(value) for value in row) + "\n")


def find_primary_href(repo_url: str, timeout: int) -> str:
    repomd_url = urljoin(repo_url, "repodata/repomd.xml")
    root = ET.fromstring(request_bytes(repomd_url, timeout))

    preferred: str | None = None
    fallback: str | None = None
    for data_node in root.findall("repo:data", REPO_NS):
        if data_node.get("type") != "primary":
            continue
        location = data_node.find("repo:location", REPO_NS)
        href = location.get("href") if location is not None else None
        if not href:
            continue
        fallback = href
        if href.endswith((".xml.gz", ".xml.xz", ".xml")):
            preferred = href
            break

    if preferred:
        return preferred
    if fallback:
        return fallback
    raise RuntimeError(f"Cannot find primary metadata in {repomd_url}")


def decompress_metadata(href: str, data: bytes) -> bytes:
    if href.endswith(".gz"):
        return gzip.decompress(data)
    if href.endswith(".xz"):
        return lzma.decompress(data)
    if href.endswith(".zst") or href.endswith(".zck"):
        raise RuntimeError(f"Unsupported metadata compression: {href}")
    return data


def load_remote_repo(repo: str, repo_url: str, timeout: int) -> list[RemoteRpm]:
    primary_href = find_primary_href(repo_url, timeout)
    primary_url = urljoin(repo_url, primary_href)
    primary_xml = decompress_metadata(primary_href, request_bytes(primary_url, timeout))
    root = ET.fromstring(primary_xml)

    rpms: list[RemoteRpm] = []
    for package_node in root.findall("common:package", COMMON_NS):
        location = package_node.find("common:location", COMMON_NS)
        size_node = package_node.find("common:size", COMMON_NS)
        checksum_node = package_node.find("common:checksum", COMMON_NS)
        href = location.get("href") if location is not None else None
        size_text = size_node.get("package") if size_node is not None else None
        if not href or not href.endswith(".rpm") or not size_text:
            continue

        rpms.append(
            RemoteRpm(
                repo=repo,
                href=href,
                filename=Path(href).name,
                size=int(size_text),
                checksum_type=checksum_node.get("type") if checksum_node is not None else None,
                checksum=checksum_node.text.strip() if checksum_node is not None and checksum_node.text else None,
            )
        )
    return rpms


def file_checksum(path: Path, checksum_type: str) -> str:
    digest = hashlib.new(checksum_type)
    with path.open("rb") as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def needs_download(remote: RemoteRpm, local_by_name: dict[str, LocalRpm], verify_checksum: bool) -> tuple[bool, str]:
    local = local_by_name.get(remote.filename)
    if local is None:
        return True, "光碟與下載資料夾都缺少此遠端檔案"
    if local.size != remote.size:
        return True, f"本機檔案大小不同 檔案={local.path} 本機={local.size} 遠端={remote.size}"
    if verify_checksum and remote.checksum and remote.checksum_type:
        checksum_type = remote.checksum_type.lower()
        if checksum_type in hashlib.algorithms_available:
            if file_checksum(local.path, checksum_type).lower() != remote.checksum.lower():
                return True, f"本機檔案檢查碼不同 檔案={local.path}"
    return False, "已存在"


def merge_local_rpms(disc_rpms: list[LocalRpm], download_rpms: list[LocalRpm]) -> dict[str, LocalRpm]:
    merged = {rpm.filename: rpm for rpm in download_rpms}
    for rpm in disc_rpms:
        merged.setdefault(rpm.filename, rpm)
    return merged


def download_rpm(repo_url: str, remote: RemoteRpm, destination: Path, timeout: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_suffix(destination.suffix + ".part")
    request = Request(urljoin(repo_url, remote.href), headers={"User-Agent": "centosrepooffline-python/1.0"})
    with urlopen(request, timeout=timeout) as response, temp_path.open("wb") as file:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            file.write(chunk)
    os.replace(temp_path, destination)


def destination_for(download_root: Path, remote: RemoteRpm) -> Path:
    return download_root / remote.repo / PACKAGES_DIR / remote.filename


def local_rpm_from_download(download_root: Path, remote: RemoteRpm) -> LocalRpm:
    repo_root = download_root / remote.repo
    path = destination_for(download_root, remote)
    return LocalRpm(
        repo=remote.repo,
        path=path,
        relative_path=path.relative_to(repo_root).as_posix(),
        filename=remote.filename,
        size=path.stat().st_size,
    )


def prompt_download_root(default: Path) -> Path:
    while True:
        print(f"依網址推測的下載資料夾完整路徑: {absolute_path(default)}")
        answer = input("使用這個資料夾嗎？輸入 Y 使用，或直接輸入其他資料夾路徑: ").strip().strip('"')
        if answer.lower() in {"y", "yes"}:
            return absolute_path(default)
        if answer:
            return absolute_path(Path(answer))
        print("請輸入 Y 或其他資料夾路徑。")


def prompt_disc_source() -> tuple[Path | None, Path | None]:
    while True:
        answer = input("請輸入 ISO 檔完整路徑，或已掛載光碟資料夾路徑，例如 E:\\: ").strip().strip('"')
        if not answer:
            print("路徑不可空白，請重新輸入。")
            continue

        path = absolute_path(Path(answer))
        if path.is_file() and path.suffix.lower() == ".iso":
            return path, None
        if path.is_dir():
            return None, path
        print("找不到此 ISO 檔或資料夾，請重新輸入。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="比對本機 RPM 與 CentOS 遠端 repo，只下載光碟與下載資料夾都缺少的 RPM。",
        add_help=False,
    )
    parser._optionals.title = "選項"
    parser.add_argument("-h", "--help", action="help", help="顯示此說明訊息並結束。")
    parser.add_argument("--disc-root", type=Path, default=None, help="已掛載的光碟或 ISO 根目錄，例如 E:\\。")
    parser.add_argument("--iso-path", type=Path, default=None, help="ISO 檔案路徑；提供後程式會自動掛載並當作光碟來源。")
    parser.add_argument("--local-root", type=Path, default=None, help="舊參數，等同於 --download-root。")
    parser.add_argument("--download-root", type=Path, default=None, help="RPM 下載根目錄；未指定時會依 --base-url 推測 centos9、centos10。")
    parser.add_argument("--base-url", default=None, help="CentOS base URL，例如 https://mirror.stream.centos.org/9-stream/。")
    parser.add_argument("--arch", default="x86_64", help="repo 架構資料夾名稱，預設 x86_64。")
    parser.add_argument("--repos", default="auto", help="要處理的 repo，以逗號分隔；auto 表示自動處理 base URL 下所有 repo。")
    parser.add_argument("--appstream-url", default=None, help="直接指定 AppStream repo URL；通常不需要，建議使用 --base-url。")
    parser.add_argument("--baseos-url", default=None, help="直接指定 BaseOS repo URL；通常不需要，建議使用 --base-url。")
    parser.add_argument("--log-root", type=Path, default=None, help="log 根目錄；未指定時使用 download-root/log。")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="本機 RPM 清單快取資料夾。")
    parser.add_argument("--refresh-local-cache", action="store_true", help="重新掃描光碟與下載資料夾，並重建快取。")
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="log 日期資料夾名稱，格式 YYYY-MM-DD。")
    parser.add_argument("--timeout", type=int, default=60, help="HTTP 連線逾時秒數。")
    parser.add_argument("--dry-run", action="store_true", help="只產生報表與顯示待下載清單，不實際下載。")
    parser.add_argument("--verify-checksum", action="store_true", help="比對既有檔案時也檢查 checksum，較慢但更嚴格。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.base_url and not (args.appstream_url or args.baseos_url):
        args.base_url = prompt_base_url()

    download_root = args.download_root or args.local_root
    if download_root is None:
        default_download_root = infer_download_root(args.base_url)
        download_root = prompt_download_root(default_download_root)
    else:
        download_root = absolute_path(download_root)
    mounted_iso_path = args.iso_path
    disc_root_arg = args.disc_root
    if not mounted_iso_path and not disc_root_arg:
        mounted_iso_path, disc_root_arg = prompt_disc_source()

    if mounted_iso_path:
        disc_root = mount_iso(mounted_iso_path)
        print(f"ISO 已掛載，光碟來源: {disc_root}")
    else:
        disc_root = absolute_path(disc_root_arg)
        print(f"使用已掛載光碟來源: {disc_root}")
    download_root.mkdir(parents=True, exist_ok=True)
    log_root = args.log_root or download_root / "log"
    log_dir = log_root / args.date
    log_dir.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"實際下載根目錄: {download_root}")
    print(f"實際 log 根目錄: {absolute_path(log_root)}")

    try:
        repo_urls = build_repo_urls(args)

        all_failures: list[tuple[object, ...]] = []
        all_to_download: list[tuple[object, ...]] = []
        all_downloaded: list[tuple[object, ...]] = []

        for repo, repo_url in repo_urls.items():
            print(f"建立光碟 {repo} RPM 清單...")
            disc_rpms, disc_source = get_local_rpms(
                local_root=disc_root,
                repo=repo,
                cache_dir=args.cache_dir,
                cache_source="disc",
                refresh_cache=args.refresh_local_cache,
                create_missing=False,
            )
            print(f"{repo} 光碟清單來源: {'快取' if disc_source == 'cache' else '重新掃描'} ({len(disc_rpms)} 個 RPM)")

            print(f"建立下載資料夾 {repo} RPM 清單...")
            repo_download_dir = download_root / repo / PACKAGES_DIR
            download_rpms, download_source = get_local_rpms(
                local_root=download_root,
                repo=repo,
                cache_dir=args.cache_dir,
                cache_source="download",
                refresh_cache=args.refresh_local_cache,
                create_missing=True,
            )
            print(f"{repo} 下載資料夾: {repo_download_dir}")
            print(f"{repo} 下載清單來源: {'快取' if download_source == 'cache' else '重新掃描'} ({len(download_rpms)} 個 RPM)")

            local_by_name = merge_local_rpms(disc_rpms, download_rpms)
            write_tsv(
                log_dir / f"{repo}_disc_rpms.tsv",
                [(rpm.relative_path, rpm.filename, rpm.size, rpm.path) for rpm in disc_rpms],
            )
            write_tsv(
                log_dir / f"{repo}_download_rpms.tsv",
                [(rpm.relative_path, rpm.filename, rpm.size, rpm.path) for rpm in download_rpms],
            )

            print(f"讀取遠端 {repo} metadata...")
            remote_rpms = load_remote_repo(repo, repo_url, args.timeout)
            write_tsv(
                log_dir / f"{repo}_remote_rpms.tsv",
                [(rpm.href, rpm.filename, rpm.size, rpm.checksum_type or "", rpm.checksum or "") for rpm in remote_rpms],
            )

            repo_to_download: list[tuple[RemoteRpm, str]] = []
            for remote in remote_rpms:
                should_download, reason = needs_download(remote, local_by_name, args.verify_checksum)
                if should_download:
                    repo_to_download.append((remote, reason))
                    all_to_download.append((repo, remote.href, remote.size, reason))

            print(f"{repo} 需要下載 {len(repo_to_download)} 個 RPM")
            repo_downloaded = False
            for index, (remote, reason) in enumerate(repo_to_download, start=1):
                destination = destination_for(download_root, remote)
                if args.dry_run:
                    print(f"[dry-run {repo} {index}/{len(repo_to_download)}] {remote.filename} -> {repo_download_dir} ({reason})")
                    continue
                try:
                    print(f"[{repo} {index}/{len(repo_to_download)}] 下載 {remote.filename} -> {repo_download_dir} ({reason})")
                    download_rpm(repo_url, remote, destination, args.timeout)
                    if destination.stat().st_size != remote.size:
                        raise RuntimeError(f"downloaded size mismatch: {destination.stat().st_size} != {remote.size}")
                    downloaded_rpm = local_rpm_from_download(download_root, remote)
                    local_by_name[downloaded_rpm.filename] = downloaded_rpm
                    all_downloaded.append((repo, remote.href, remote.size, destination))
                    repo_downloaded = True
                except (HTTPError, URLError, OSError, RuntimeError) as exc:
                    all_failures.append((repo, remote.href, exc))
                    print(f"下載失敗: {remote.href}: {exc}")

            if not args.dry_run and repo_downloaded:
                refreshed_download_rpms = [
                    rpm for rpm in local_by_name.values()
                    if path_belongs_to_repo(rpm.path, download_root, repo)
                ]
                save_local_cache(args.cache_dir, "download", repo, refreshed_download_rpms)

        write_tsv(log_dir / "to_download.tsv", all_to_download)
        write_tsv(log_dir / "downloaded.tsv", all_downloaded)
        write_tsv(log_dir / "failed.tsv", all_failures)
        write_tsv(args.cache_dir / "latest_to_download.tsv", all_to_download)
        write_tsv(args.cache_dir / "latest_downloaded.tsv", all_downloaded)

        summary_rows = [
            ("disc_root", disc_root),
            ("iso_path", mounted_iso_path or ""),
            ("download_root", download_root),
            ("cache_dir", args.cache_dir),
            ("refresh_local_cache", args.refresh_local_cache),
            ("dry_run", args.dry_run),
            ("to_download", len(all_to_download)),
            ("downloaded", len(all_downloaded)),
            ("failed", len(all_failures)),
        ]
        write_tsv(log_dir / "summary.tsv", summary_rows)

        print(f"完成。log 位置: {log_dir}")
        print(f"需下載: {len(all_to_download)}, 已下載: {len(all_downloaded)}, 失敗: {len(all_failures)}")
        return 1 if all_failures else 0
    finally:
        if mounted_iso_path:
            dismount_iso(mounted_iso_path)
            print(f"ISO 已卸載: {absolute_path(mounted_iso_path)}")


if __name__ == "__main__":
    raise SystemExit(main())
