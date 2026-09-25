# Spark LLM Token Monitor

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg)](https://fastapi.tiangolo.com)
[![SGLang](https://img.shields.io/badge/Engine-SGLang%20%7C%20vLLM-orange.svg)](https://github.com/sgl-project/sglang)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

專為本機 LLM 推論伺服器（SGLang / vLLM）打造的**即時與歷史 Token 用量監控服務**。

本系統採用「**計算在 Spark，視覺化在邊緣 Jetson**」的解耦設計：在推論主機（Spark）高頻採樣並聚合 SGLang Prometheus 指標，儲存於輕量 SQLite，並對外提供高效能 RESTful API；讓遠端的 Jetson 監控網頁可隨時拉取即時 Token/s (TPS)、每小時累積用量與每日平均值進行圖表繪製。

---

## 🌟 核心特色

- **⚡ 即時吞吐量追蹤**：即時回傳當前每秒輸出 Token 數（Generation TPS）與輸入 Prefill 處理速度。
- **📊 歷史聚合統計**：提供最近 24/48/168 小時每小時累積用量（支援長條圖），以及近 14/30 日每日總量與日均線。
- **🧠 記憶體與並發預警**：提供 KV Cache 顯存佔用率（%）與當前活躍請求數（Active Requests）。
- **🛡️ SGLang 重啟容錯機制 (Reset-Safe)**：自動辨識並處理推論容器重啟導致的 Prometheus Counter 歸零，不丟失歷史累積。
- **🪶 極致輕量**：採用 SQLite (WAL 模式) 與 Python FastAPI，整體記憶體開銷低於 40MB。
- **🌐 跨域支援 (CORS)**：原生啟用跨來源資源共享，Jetson 端的瀏覽器可直接以 JavaScript `fetch()` 存取。
- **🖥️ 附贈開箱即用儀表板**：內附 `examples/jetson_dashboard.html`，以 Chart.js 實作的暗色系即時監控儀表板。

---

## 🏗️ 系統架構

```
┌────────────────────────────────────────────────────────┐
│ Spark LLM 推論伺服器 (192.168.31.128 / 100.88.14.123) │
│                                                        │
│  ┌───────────────────────┐                             │
│  │ SGLang Engine (:8000) │                             │
│  │ └─► /metrics          │                             │
│  └───────────┬───────────┘                             │
│              │ (每 3 秒取樣一次)                        │
│  ┌───────────▼──────────────────────────────────────┐  │
│  │ LLM Token Monitor (:8095)                        │  │
│  │ ├─ MetricsCollector (Regex Parser + Reset-Safe)  │  │
│  │ ├─ SQLite DB (token_metrics.db, WAL Mode)        │  │
│  │ └─ FastAPI REST API                              │  │
│  └──────────────────────────────────────────────────┘  │
└───────────────────────────┬────────────────────────────┘
                            │ HTTP GET (JSON, CORS)
                            ▼
┌────────────────────────────────────────────────────────┐
│ Jetson 邊緣端面板 (Remote Client Dashboard)           │
│                                                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Web Dashboard (examples/jetson_dashboard.html)   │  │
│  │ ├─ 即時卡片：當前 TPS、KV Cache %、今日總用量    │  │
│  │ ├─ 每小時累積長條圖 (Chart.js Bar Chart)         │  │
│  │ └─ 每日趨勢與日均線 (Chart.js Line Chart)        │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
```

---

## 🚀 快速開始

### 1. 安裝依賴環境
```bash
git clone https://github.com/tzuchen/llm-token-monitor.git
cd llm-token-monitor
pip install -r requirements.txt
```

### 2. 本地直接啟動
```bash
python3 main.py
```
啟動後服務將預設監聽於 `http://0.0.0.0:8095`。

### 3. 以 systemd 常駐於系統背景（開機自啟）
專案已提供 systemd unit 設定檔：
```bash
cp llm-token-monitor.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now llm-token-monitor.service

# 查看運行狀態
systemctl --user status llm-token-monitor.service
```

---

## 📡 API 介面規格

Spark 主機連線位置：
- **區域網路 (LAN IP)**：`http://192.168.31.128:8095`
- **Tailscale 網內 IP**：`http://100.88.14.123:8095`
- **本機測試**：`http://127.0.0.1:8095`

### 1. 即時狀態 (`GET /api/llm/realtime`)
更新頻率建議 1~3 秒一次，提供給儀表板上的即時狀態卡片。

```bash
curl http://192.168.31.128:8095/api/llm/realtime
```

**回傳 JSON 範例**：
```json
{
  "status": "online",
  "timestamp": 1790310182,
  "model": "spark-vllm-docker",
  "tps": {
    "generation_tps": 16.5,
    "prefill_tps": 21.12
  },
  "kv_cache": {
    "usage_percent": 18.4,
    "used_tokens": 72067,
    "max_tokens": 391672
  },
  "concurrency": {
    "active_requests": 1,
    "total_requests": 21692
  },
  "today_summary": {
    "date": "2026-09-25",
    "prompt_tokens": 64,
    "generation_tokens": 50,
    "total_tokens": 114,
    "request_count": 1
  }
}
```

---

### 2. 每小時累積用量 (`GET /api/llm/hourly?hours=24`)
獲取最近 N 小時（預設 24，最大 168 小時）的累積數據，適合繪製長條圖（Bar Chart）。

```bash
curl "http://192.168.31.128:8095/api/llm/hourly?hours=24"
```

**回傳 JSON 範例**：
```json
{
  "unit": "tokens",
  "hours": 24,
  "history": [
    {
      "time": "2026-09-25 11:00:00",
      "prompt": 89200,
      "generation": 24100,
      "total": 113300,
      "requests": 34
    },
    {
      "time": "2026-09-25 12:00:00",
      "prompt": 64,
      "generation": 50,
      "total": 114,
      "requests": 1
    }
  ]
}
```

---

### 3. 每日用量與日平均 (`GET /api/llm/daily?days=14`)
獲取最近 N 天（預設 14，最大 90 天）的用量歷史與每日平均 Token 消耗量。

```bash
curl "http://192.168.31.128:8095/api/llm/daily?days=14"
```

**回傳 JSON 範例**：
```json
{
  "days": 1,
  "daily_average": 114.0,
  "history": [
    {
      "date": "2026-09-25",
      "prompt": 64,
      "generation": 50,
      "total": 114,
      "requests": 1
    }
  ]
}
```

---

### 4. 綜合統計端點 (`GET /api/llm/stats`)
一次取得即時、最近 24 小時與最近 14 天歷史數據，大幅減少 Jetson 前端 HTTP 請求數。

```bash
curl "http://192.168.31.128:8095/api/llm/stats"
```

---

## 🎨 Jetson 儀表板範例網頁

專案目錄下的 `examples/jetson_dashboard.html` 是一個開箱即用的單頁儀表板：
1. 直接在 Jetson 上用瀏覽器打開 `examples/jetson_dashboard.html`。
2. 或在 Jetson 上啟動簡易 HTTP 伺服器：
   ```bash
   python3 -m http.server 8090
   ```
3. 在頁面上方填入 Spark 的 IP（如 `http://192.168.31.128:8095`），即可即時觀看圖表更新。

---

## ⚙️ 常用管理指令

```bash
# 查看常駐服務即時狀態
systemctl --user status llm-token-monitor.service

# 查看即時日誌輸出
journalctl --user -u llm-token-monitor.service -f

# 重新啟動服務
systemctl --user restart llm-token-monitor.service

# 停止服務
systemctl --user stop llm-token-monitor.service
```

---

## 🔧 環境變數配置表

可在啟動前或於 `llm-token-monitor.service` 檔案中自訂以下環境變數：

| 變數名稱 | 預設值 | 說明 |
| :--- | :--- | :--- |
| `PORT` | `8095` | HTTP API 監聽連接埠 |
| `HOST` | `0.0.0.0` | 監聽網路介面 |
| `SGLANG_METRICS_URL` | `http://localhost:8000/metrics` | SGLang Prometheus 指標來源 |
| `POLL_INTERVAL_SEC` | `3.0` | 指標取樣頻率（秒） |
| `METRICS_DB_PATH` | `data/token_metrics.db` | SQLite 資料庫儲存路徑 |
| `MODEL_NAME` | `spark-vllm-docker` | 模型識別名稱 |

---

## 📄 License

MIT License.
