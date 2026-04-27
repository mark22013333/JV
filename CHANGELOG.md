# Changelog

## 0.1.0
- 改善串流下載的瀏覽器偽裝：`ffmpeg` 現在會分開傳送 `-user_agent` / `-referer`，並在有 `Referer` 時自動補上 `Origin`。
- 新增 `yt-dlp` 下載器支援：CLI 可用 `--downloader yt-dlp` 與 `--cookies-from-browser`，Web UI 若偵測到已安裝 `yt-dlp` 也會優先使用。
- 新增 master playlist 畫質選擇：CLI 可用 `--quality best|worst|1080p|720p`，並會自動挑選對應變體。
- CLI 互動模式現在能辨識一般頁面網址，會走 `--page-url` 掃描流程；若找不到公開串流，也會明確提示改貼 `curl/HAR`。
- 初版 CLI，可掃描 m3u8、產生 ffmpeg 指令並可選擇直接下載。
- 新增 curl 指令解析（可自動帶入 URL 與 headers）。
- 新增 HAR 解析，可從瀏覽器 Network 匯出檔抓出媒體連結。
- 新增以頁面標題命名檔案（可選用）。
