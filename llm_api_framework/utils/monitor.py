from typing import Dict
from ..core.client import APIClient
import time
import statistics

class Monitor:
    def __init__(self):
        self.metrics = {
            "success_count": 0,
            "failure_count": 0,
            "latencies": [],
            "last_success": None,
            "last_failure": None
        }

    def record_latency(self, client: APIClient, latency: float):
        """Record API call latency"""
        self.metrics["latencies"].append(latency)
        if len(self.metrics["latencies"]) > 100:  # Keep last 100 samples
            self.metrics["latencies"].pop(0)

    def record_success(self, client: APIClient):
        """Record successful API call"""
        self.metrics["success_count"] += 1
        self.metrics["last_success"] = time.time()

    def record_failure(self, client: APIClient):
        """Record failed API call"""
        self.metrics["failure_count"] += 1
        self.metrics["last_failure"] = time.time()

    def get_stats(self) -> Dict:
        """Get performance statistics"""
        stats = {
            "success_rate": self.metrics["success_count"] / 
                          max(1, self.metrics["success_count"] + self.metrics["failure_count"]),
            "total_calls": self.metrics["success_count"] + self.metrics["failure_count"],
            "avg_latency": statistics.mean(self.metrics["latencies"]) if self.metrics["latencies"] else 0,
            "p95_latency": statistics.quantiles(self.metrics["latencies"], n=20)[-1] if self.metrics["latencies"] else 0
        }
        return stats