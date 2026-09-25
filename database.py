import sqlite3
import os
import time
from typing import Optional, List, Dict, Any

class MetricsDB:
    def __init__(self, db_path: str = "data/token_metrics.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS hourly_stats (
                hour_key TEXT PRIMARY KEY,
                prompt_tokens INTEGER DEFAULT 0,
                generation_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                request_count INTEGER DEFAULT 0,
                last_updated REAL
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """)
            conn.commit()

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM meta WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else default

    def set_meta(self, key: str, value: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """, (key, str(value)))
            conn.commit()

    def record_delta(
        self,
        hour_key: str,
        prompt_delta: int,
        gen_delta: int,
        request_delta: int,
        timestamp: float
    ):
        total_delta = prompt_delta + gen_delta
        if prompt_delta == 0 and gen_delta == 0 and request_delta == 0:
            return

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO hourly_stats (
                hour_key, prompt_tokens, generation_tokens, total_tokens, request_count, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(hour_key) DO UPDATE SET
                prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                generation_tokens = generation_tokens + excluded.generation_tokens,
                total_tokens = total_tokens + excluded.total_tokens,
                request_count = request_count + excluded.request_count,
                last_updated = excluded.last_updated;
            """, (hour_key, prompt_delta, gen_delta, total_delta, request_delta, timestamp))
            conn.commit()

    def get_today_summary(self, today_date_prefix: str) -> Dict[str, Any]:
        """today_date_prefix is formatted as 'YYYY-MM-DD'"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT 
                COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                COALESCE(SUM(generation_tokens), 0) as generation_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(request_count), 0) as request_count
            FROM hourly_stats
            WHERE hour_key LIKE ?;
            """, (f"{today_date_prefix}%",))
            row = cursor.fetchone()
            return {
                "date": today_date_prefix,
                "prompt_tokens": int(row["prompt_tokens"]),
                "generation_tokens": int(row["generation_tokens"]),
                "total_tokens": int(row["total_tokens"]),
                "request_count": int(row["request_count"]),
            }

    def get_hourly_history(self, hours: int = 24) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT hour_key, prompt_tokens, generation_tokens, total_tokens, request_count
            FROM hourly_stats
            ORDER BY hour_key DESC
            LIMIT ?;
            """, (hours,))
            rows = cursor.fetchall()
            history = [
                {
                    "time": row["hour_key"],
                    "prompt": int(row["prompt_tokens"]),
                    "generation": int(row["generation_tokens"]),
                    "total": int(row["total_tokens"]),
                    "requests": int(row["request_count"])
                }
                for row in reversed(rows)
            ]
            return history

    def get_daily_history(self, days: int = 14) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT 
                substr(hour_key, 1, 10) as date_key,
                SUM(prompt_tokens) as prompt_tokens,
                SUM(generation_tokens) as generation_tokens,
                SUM(total_tokens) as total_tokens,
                SUM(request_count) as request_count
            FROM hourly_stats
            GROUP BY substr(hour_key, 1, 10)
            ORDER BY date_key DESC
            LIMIT ?;
            """, (days,))
            rows = cursor.fetchall()
            history = [
                {
                    "date": row["date_key"],
                    "prompt": int(row["prompt_tokens"]),
                    "generation": int(row["generation_tokens"]),
                    "total": int(row["total_tokens"]),
                    "requests": int(row["request_count"])
                }
                for row in reversed(rows)
            ]
            total_sum = sum(item["total"] for item in history)
            avg = round(total_sum / len(history), 1) if history else 0.0
            return {
                "days": len(history),
                "daily_average": avg,
                "history": history
            }
