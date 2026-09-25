import asyncio
import logging
import re
import time
from datetime import datetime
from typing import Dict, Any, Optional
import httpx
from database import MetricsDB

logger = logging.getLogger("llm_collector")

class MetricsCollector:
    def __init__(
        self,
        db: MetricsDB,
        metrics_url: str = "http://localhost:8000/metrics",
        poll_interval: float = 3.0,
        model_name: str = "spark-vllm-docker"
    ):
        self.db = db
        self.metrics_url = metrics_url
        self.poll_interval = poll_interval
        self.model_name = model_name
        self.running = False

        self.last_poll_time: Optional[float] = None
        self.last_prompt_total: Optional[float] = None
        self.last_gen_total: Optional[float] = None
        self.last_requests_total: Optional[float] = None

        self.latest_state: Dict[str, Any] = {
            "status": "initializing",
            "timestamp": int(time.time()),
            "model": self.model_name,
            "tps": {"generation_tps": 0.0, "prefill_tps": 0.0},
            "kv_cache": {"usage_percent": 0.0, "used_tokens": 0, "max_tokens": 0},
            "concurrency": {"active_requests": 0, "total_requests": 0},
            "today_summary": {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "prompt_tokens": 0,
                "generation_tokens": 0,
                "total_tokens": 0,
                "request_count": 0
            }
        }
        self._load_meta_state()

    def _load_meta_state(self):
        try:
            p = self.db.get_meta("last_prompt_total")
            g = self.db.get_meta("last_gen_total")
            r = self.db.get_meta("last_requests_total")
            t = self.db.get_meta("last_poll_time")
            if p is not None:
                self.last_prompt_total = float(p)
            if g is not None:
                self.last_gen_total = float(g)
            if r is not None:
                self.last_requests_total = float(r)
            if t is not None:
                self.last_poll_time = float(t)
        except Exception as e:
            logger.warning(f"Failed to load meta state: {e}")

    def _parse_sum(self, text: str, metric_prefix: str) -> float:
        total = 0.0
        pattern = re.compile(rf"^{re.escape(metric_prefix)}(\{{[^}}]*\}})?\s+([0-9\.eE\+\-]+)", re.MULTILINE)
        for match in pattern.finditer(text):
            try:
                total += float(match.group(2))
            except ValueError:
                pass
        return total

    def _parse_active_completions(self, text: str) -> int:
        pattern = re.compile(r"^sglang:http_requests_active\{endpoint=\"/v1/chat/completions\",method=\"POST\"\}\s+([0-9\.]+)", re.MULTILINE)
        match = pattern.search(text)
        if match:
            try:
                return int(float(match.group(1)))
            except ValueError:
                pass
        return 0

    async def scrape_once(self, client: httpx.AsyncClient):
        now = time.time()
        try:
            resp = await client.get(self.metrics_url, timeout=2.5)
            if resp.status_code != 200:
                self.latest_state["status"] = f"http_error_{resp.status_code}"
                return

            text = resp.text
            prompt_total = self._parse_sum(text, "sglang:prompt_tokens_total")
            gen_total = self._parse_sum(text, "sglang:generation_tokens_total")
            req_total = self._parse_sum(text, "sglang:num_requests_total")
            sglang_gen_tps = self._parse_sum(text, "sglang:gen_throughput")
            kv_max = self._parse_sum(text, "sglang:max_total_num_tokens")
            kv_used = self._parse_sum(text, "sglang:num_used_tokens")
            active_reqs = self._parse_active_completions(text)

            prompt_delta = 0
            gen_delta = 0
            req_delta = 0
            calc_gen_tps = 0.0
            calc_prefill_tps = 0.0

            dt = (now - self.last_poll_time) if self.last_poll_time else 0.0

            if self.last_prompt_total is not None and self.last_gen_total is not None:
                # Handle potential SGLang restart
                if prompt_total < self.last_prompt_total or gen_total < self.last_gen_total:
                    logger.info("Detected SGLang restart! Resetting counter baseline.")
                    prompt_delta = int(prompt_total)
                    gen_delta = int(gen_total)
                    req_delta = int(req_total)
                else:
                    prompt_delta = int(prompt_total - self.last_prompt_total)
                    gen_delta = int(gen_total - self.last_gen_total)
                    req_delta = int(req_total - (self.last_requests_total or req_total))

                if dt > 0.1:
                    calc_gen_tps = max(0.0, gen_delta / dt)
                    calc_prefill_tps = max(0.0, prompt_delta / dt)
            else:
                # First ever run: initialize baseline without huge delta
                prompt_delta = 0
                gen_delta = 0
                req_delta = 0

            # Determine best generation TPS: SGLang throughput or delta
            effective_gen_tps = max(sglang_gen_tps, calc_gen_tps)

            # Record delta into SQLite if there is activity
            now_dt = datetime.now()
            hour_key = now_dt.strftime("%Y-%m-%d %H:00:00")
            today_prefix = now_dt.strftime("%Y-%m-%d")

            if prompt_delta > 0 or gen_delta > 0 or req_delta > 0:
                self.db.record_delta(
                    hour_key=hour_key,
                    prompt_delta=prompt_delta,
                    gen_delta=gen_delta,
                    request_delta=req_delta,
                    timestamp=now
                )

            # Update meta records
            self.last_prompt_total = prompt_total
            self.last_gen_total = gen_total
            self.last_requests_total = req_total
            self.last_poll_time = now

            self.db.set_meta("last_prompt_total", str(prompt_total))
            self.db.set_meta("last_gen_total", str(gen_total))
            self.db.set_meta("last_requests_total", str(req_total))
            self.db.set_meta("last_poll_time", str(now))

            # KV cache percentage
            kv_pct = round((kv_used / kv_max * 100.0), 2) if kv_max > 0 else 0.0

            # Today summary
            today_summary = self.db.get_today_summary(today_prefix)

            self.latest_state = {
                "status": "online",
                "timestamp": int(now),
                "model": self.model_name,
                "tps": {
                    "generation_tps": round(effective_gen_tps, 2),
                    "prefill_tps": round(calc_prefill_tps, 2)
                },
                "kv_cache": {
                    "usage_percent": kv_pct,
                    "used_tokens": int(kv_used),
                    "max_tokens": int(kv_max)
                },
                "concurrency": {
                    "active_requests": active_reqs,
                    "total_requests": int(req_total)
                },
                "today_summary": today_summary
            }
        except httpx.ConnectError:
            self.latest_state["status"] = "offline"
            self.latest_state["timestamp"] = int(now)
            self.latest_state["tps"] = {"generation_tps": 0.0, "prefill_tps": 0.0}
        except Exception as e:
            logger.error(f"Collector error: {e}")
            self.latest_state["status"] = "error"
            self.latest_state["timestamp"] = int(now)

    async def run_loop(self):
        self.running = True
        logger.info(f"Starting collector loop targeting {self.metrics_url}")
        async with httpx.AsyncClient() as client:
            while self.running:
                await self.scrape_once(client)
                await asyncio.sleep(self.poll_interval)

    def stop(self):
        self.running = False
