# CentOS RPM 離線更新工具

這個專案用來維護 CentOS Stream 的離線 RPM 資料夾。程式會讀取遠端
repository metadata，比對光碟內既有 RPM 與本機已下載 RPM，只下載缺少或
大小不同的 RPM。

## 資料夾結構

- `python/`：Python 更新程式與舊版 `.sh` 腳本。
- `python/temp/`：本機 RPM 清單快取，避免每次重掃光碟與下載資料夾。
- `centos9/`：CentOS 9 RPM 下載資料夾。
- `centos9/log/YYYY-MM-DD/`：每日更新紀錄。

下載後的 RPM 會依 repo 名稱放在 `Packages` 資料夾，例如：

```text
centos9/AppStream/Packages/*.rpm
centos9/BaseOS/Packages/*.rpm
centos9/CRB/Packages/*.rpm
```

之後若下載 CentOS 10，預設會使用：

```text
centos10/AppStream/Packages/*.rpm
centos10/BaseOS/Packages/*.rpm
```

## 主要流程

`D:\` 會被當成初始光碟或 ISO 掛載來源。程式會先記錄光碟上的 RPM，例如：

```text
D:\AppStream\Packages
D:\BaseOS\Packages
```

接著輸入 CentOS base URL。程式會自動找出該網址下的 repo 資料夾，例如：

```text
https://mirror.stream.centos.org/9-stream/AppStream/x86_64/os/
https://mirror.stream.centos.org/9-stream/BaseOS/x86_64/os/
https://mirror.stream.centos.org/9-stream/CRB/x86_64/os/
```

最後只下載「光碟與下載資料夾都沒有」或「本機大小與遠端不同」的 RPM。

## 執行方式

互動模式：

```powershell
python python/compare_rpms_http.py
```

程式會提示預設網址：

```text
預設 CentOS base URL: https://mirror.stream.centos.org/9-stream/
使用這個網址嗎？輸入 Y 使用，或直接輸入其他網址:
```

若要下載 CentOS 10，可直接輸入：

```text
https://mirror.stream.centos.org/10-stream/
```

接著程式會依網址推測下載資料夾，並顯示完整路徑。輸入 `Y` 使用，或直接輸入其他資料夾。

若輸入相對路徑，例如：

```text
centos9
```

實際會解析成目前執行目錄底下的完整路徑，例如：

```text
C:\Users\user\Documents\virtual_share\codex-projects\centosrepooffline\centos9
```

## 非互動模式

下載 CentOS 9：

```powershell
python python/compare_rpms_http.py --download-root .\centos9 --base-url https://mirror.stream.centos.org/9-stream/
```

下載 CentOS 10：

```powershell
python python/compare_rpms_http.py --download-root .\centos10 --base-url https://mirror.stream.centos.org/10-stream/
```

如果光碟或 ISO 不是掛在 `D:\`，可指定 `--disc-root`：

```powershell
python python/compare_rpms_http.py --disc-root E:\ --download-root .\centos9 --base-url https://mirror.stream.centos.org/9-stream/
```

只預覽、不下載：

```powershell
python python/compare_rpms_http.py --download-root .\centos9 --base-url https://mirror.stream.centos.org/9-stream/ --dry-run
```

## Repo 選擇

預設 `--repos auto`，會下載 base URL 下找到的所有 repo。

若只想處理特定 repo：

```powershell
python python/compare_rpms_http.py --base-url https://mirror.stream.centos.org/9-stream/ --repos AppStream,BaseOS,CRB
```

## 快取機制

程式會把光碟與下載資料夾的 RPM 清單存在 `python/temp/`，例如：

```text
python/temp/disc_AppStream_local_rpms.tsv
python/temp/disc_BaseOS_local_rpms.tsv
python/temp/download_AppStream_local_rpms.tsv
python/temp/download_BaseOS_local_rpms.tsv
python/temp/latest_to_download.tsv
python/temp/latest_downloaded.tsv
```

第二次以後執行時，會重用這些清單，只上線讀取最新遠端 metadata 並比對需要下載的 RPM。

若你手動新增、刪除或搬動 RPM，請重建快取：

```powershell
python python/compare_rpms_http.py --download-root .\centos9 --base-url https://mirror.stream.centos.org/9-stream/ --refresh-local-cache
```

## Log 與報表

每日紀錄會放在下載版本資料夾底下，例如：

```text
centos9/log/YYYY-MM-DD/
centos10/log/YYYY-MM-DD/
```

主要檔案：

- `{repo}_disc_rpms.tsv`：光碟 RPM 清單。
- `{repo}_download_rpms.tsv`：下載資料夾 RPM 清單。
- `{repo}_remote_rpms.tsv`：遠端 repo RPM 清單。
- `to_download.tsv`：需要下載的 RPM。
- `downloaded.tsv`：成功下載的 RPM。
- `failed.tsv`：下載失敗的 RPM。
- `summary.tsv`：本次執行摘要。

執行時也會顯示目前正在處理哪個 repo 與下載到哪個資料夾，例如：

```text
CRB 下載資料夾: C:\...\centos9\CRB\Packages
[CRB 1/25] 下載 package-name.rpm -> C:\...\centos9\CRB\Packages
```

## 本地資料夾比對工具

`compare_rpms_folder.py` 是舊版 `compare_rpms_folder.sh` 的 Python 版本。

只產生報告：

```powershell
python python/compare_rpms_folder.py D:\AppStream D:\BaseOS
```

若要刪除第二個資料夾中重複檔名的 RPM，需明確加上：

```powershell
python python/compare_rpms_folder.py D:\existing D:\new_downloads --delete-duplicates
```
