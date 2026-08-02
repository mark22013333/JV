# M3U8 Helper 瀏覽器擴充功能

此資料夾是可直接載入 Chrome／Edge 的 Manifest V3 未封裝擴充功能，不需要 npm 或建置流程。

## 載入方式

1. 啟動專案根目錄的本機服務：`python m3u8_helper.py`。
2. 在瀏覽器擴充功能管理頁開啟開發人員模式。
3. 選擇「載入未封裝項目」，指定本 `extension` 資料夾。
4. 前往 `http://127.0.0.1:8765` 產生配對碼，再於擴充功能彈出視窗輸入。

## 偵測範圍

- URL：M3U8、MPD、MP4、WebM、M4A。
- Content-Type：HLS、DASH、video、audio。
- 自動排除 TS、M4S、AAC、金鑰等串流分段，避免清單洗版。
- 每個分頁最多保留 50 筆候選；分頁重新整理或關閉後自動清除。

本擴充功能不提供 DRM 解密，也不應用於未取得合法下載權限的內容。
