# Spark LLM Token Monitor

專為本地 LLM 推論伺服器（SGLang / vLLM）設計的即時與歷史 Token 用量監控服務。
提供 RESTful API，方便遠端儀表板（如 Jetson 邊緣端監控面板）即時拉取數據繪製統計圖表。

## 🌟 特色

- **即時吞吐量追蹤**：提供當前每秒生成 Token（Tokens/sec, TPS）與 Prefill 處理速度。
- **累積歷史聚合**：支援最近 24 小時每小時用量長條圖資料，以及每日用量統計與平均值。
- **KV Cache 與並發監控**：即時掌握顯存 KV Cache 佔用率與當前活躍請求數。
- **SGLang 重啟容錯**：具備計數器重置（Counter Reset-Safe）自動偵測，伺服器重啟不丟失統計基線。
- **輕量低開銷**：以 SQLite (WAL 模式) 與 Python FastAPI 運行，記憶體佔用極低 (< 40MB)。
- **支援跨域存取 (CORS)**：開放所有來源，Jetson 前端網頁可直接以 JavaScript `fetch()` 存取。

---

## 🏗️ 系統架構

```
[Spark Server (192.168.31.128 / 100.88.14.123)]
┌──────────────────────────────────────────────┐
│  SGLang (Port 8000)                          │
│  └─► /metrics (Prometheus)                   │
│            ▲                                 │
│            │ (每 3 秒取樣一次)               │
│  LLM Token Monitor Service (Port 8095)        │
│  ├─ MetricsCollector                         │
│  ├─ SQLite DB (hourly_stats & meta)          │
│  └─ FastAPI REST Endpoints                   │
└──────────────────────┬───────────────────────┘
                       │ HTTP GET (JSON)
                       ▼
[Jetson Dashboard / Client]
┌──────────────────────────────────────────────┐
│  網頁儀表板 (Chart.js / ECharts)             │
│  ├─ 實時卡片: 當前 TPS, KV Cache %           │
│  ├─ 24 小時長條圖: 每小時 Token 用量        │
│  └─ 每日趨勢圖: 每日累積用量與日均線         │
└──────────────────────────────────────────────┘
```

---

## 🚀 API 規格與使用範例

預設服務埠號為 `8095`。Spark 主機 IP：
- **區域網路 (LAN)**: `http://192.168.31.128:8095`
- **Tailscale 網內**: `http://100.88.14.123:8095`
- **本機**: `http://127.0.0.1:8095`

### 1. 即時狀態 API (`GET /api/llm/realtime`)
獲取當前生成速率、KV Cache 佔用率、活躍請求數與今日累積。

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
    "prompt_tokens": 125000,
    "generation_tokens": 32000,
    "total_tokens": 157000,
    "request_count": 42
  }
}
```

### 2. 每小時累積用量 (`GET /api/llm/hourly?hours=24`)
獲取最近 N 小時的每小時累積用量（可用於畫長條圖）。

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
      "time": "2026-09-25 10:00:00",
      "prompt": 45100,
      "generation": 12400,
      "total": 57500,
      "requests": 18
    },
    {
      "time": "2026-09-25 11:00:00",
      "prompt": 89200,
      "generation": 24100,
      "total": 113300,
      "requests": 34
    }
  ]
}
```

### 3. 每日用量與日平均 (`GET /api/llm/daily?days=14`)
獲取最近 N 天的總用量與日平均值。

```bash
curl "http://192.168.31.128:8095/api/llm/daily?days=14"
```

**回傳 JSON 範例**：
```json
{
  "days": 1,
  "daily_average": 157000.0,
  "history": [
    {
      "date": "2026-09-25",
      "prompt": 125000,
      "generation": 32000,
      "total": 157000,
      "requests": 42
    }
  ]
}
```

### 4. 綜合統計 API (`GET /api/llm/stats`)
一次取得即時、最近 24 小時與最近 14 天歷史數據，大幅減少 Jetson 前端 HTTP 請求數。

```bash
curl "http://192.168.31.128:8095/api/llm/stats"
```

---

## 📊 Jetson 前端整合範例 (JavaScript)

在 Jetson 的網頁中，可直接透過 JavaScript Fetch 繪製圖表：

```javascript
// 取得綜合數據並繪製圖表
async function updateLLMMetrics() {
  const SPARK_HOST = "http://192.168.31.128:8095"; // 或使用 Tailscale IP
  try {
    const res = await fetch(`${SPARK_HOST}/api/llm/stats`);
    const data = await res.json();
    
    // 1. 更新即時儀表
    document.getElementById("tps").innerText = `${data.realtime.tps.generation_tps} tok/s`;
    document.getElementById("kv-cache").innerText = `${data.realtime.kv_cache.usage_percent}%`;
    document.getElementById("today-tokens").innerText = data.realtime.today_summary.total_tokens.toLocaleString();

    // 2. 用於繪製 Hourly 柱狀圖 (例如 Chart.js)
    const hourlyLabels = data.hourly.history.map(item => item.time.split(" ")[1].slice(0, 5));
    const hourlyTotals = data.hourly.history.map(item => item.total);
    // myChart.data.labels = hourlyLabels;
    // myChart.data.datasets[0].data = hourlyTotals;
    // myChart.update();
  } catch (err) {
    console.error("無法連線到 Spark LLM Monitor:", err);
  }
}

// 每 3 秒更新一次
setInterval(updateLLMMetrics, 3000);
```

---

## ⚙️ 常駐服務管理 (systemd)

本服務已註冊為 systemd user service，開機自啟並具備異常崩潰自動重啟功能：

```bash
# 查看運行狀態
systemctl --user status llm-token-monitor.service

# 查看即時日誌
journalctl --user -u llm-token-monitor.service -f

# 重新啟動服務
systemctl --user restart llm-token-monitor.service

# 停止服務
systemctl --user stop llm-token-monitor.service
```

---

## 🔧 環境變數配置

可在啟動前或於 service 檔案中自訂以下環境變數：

| 變數名稱 | 預設值 | 說明 |
| :--- | :--- | :--- |
| `PORT` | `8095` | HTTP API 監聽埠號 |
| `HOST` | `0.0.0.0` | 監聽介面 |
| `SGLANG_METRICS_URL` | `http://localhost:8000/metrics` | SGLang Prometheus 指標來源 |
| `POLL_INTERVAL_SEC` | `3.0` | 指標取樣頻率（秒） |
| `METRICS_DB_PATH` | `data/token_metrics.db` | SQLite 資料庫儲存路徑 |
| `MODEL_NAME` | `spark-vllm-docker` | 模型識別名稱 |
