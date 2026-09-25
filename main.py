import asyncio
import os
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from database import MetricsDB
from collector import MetricsCollector

# Configuration
DB_PATH = os.getenv("METRICS_DB_PATH", "data/token_metrics.db")
METRICS_URL = os.getenv("SGLANG_METRICS_URL", "http://localhost:8000/metrics")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL_SEC", "3.0"))
MODEL_NAME = os.getenv("MODEL_NAME", "spark-vllm-docker")

db = MetricsDB(DB_PATH)
collector = MetricsCollector(
    db=db,
    metrics_url=METRICS_URL,
    poll_interval=POLL_INTERVAL,
    model_name=MODEL_NAME
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(collector.run_loop())
    yield
    collector.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(
    title="Spark LLM Token Monitor API",
    description="Real-time and historical token usage monitor for local LLM (SGLang/vLLM) on Spark",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for Jetson and remote dashboards
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {
        "service": "Spark LLM Token Monitor API",
        "status": collector.latest_state.get("status", "unknown"),
        "model": MODEL_NAME,
        "endpoints": {
            "realtime": "/api/llm/realtime",
            "hourly": "/api/llm/hourly?hours=24",
            "daily": "/api/llm/daily?days=14",
            "all_stats": "/api/llm/stats"
        }
    }

@app.get("/api/llm/realtime")
async def get_realtime():
    """Get current TPS, KV cache usage, active requests, and today cumulative tokens."""
    return collector.latest_state

@app.get("/api/llm/hourly")
async def get_hourly(hours: int = Query(default=24, ge=1, le=168)):
    """Get hourly cumulative token usage for charting."""
    return {
        "unit": "tokens",
        "hours": hours,
        "history": db.get_hourly_history(hours=hours)
    }

@app.get("/api/llm/daily")
async def get_daily(days: int = Query(default=14, ge=1, le=90)):
    """Get daily cumulative token usage and daily average for charting."""
    return db.get_daily_history(days=days)

@app.get("/api/llm/stats")
async def get_all_stats(
    hours: int = Query(default=24, ge=1, le=168),
    days: int = Query(default=14, ge=1, le=90)
):
    """Convenience endpoint: returns realtime state, hourly history, and daily history in one payload."""
    return {
        "realtime": collector.latest_state,
        "hourly": {
            "hours": hours,
            "history": db.get_hourly_history(hours=hours)
        },
        "daily": db.get_daily_history(days=days)
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8095"))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run("main:app", host=host, port=port, log_level="info")
