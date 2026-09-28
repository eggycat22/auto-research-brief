from __future__ import annotations

from dataclasses import dataclass, field

from .util import is_off_peak

# deepseek-flash, USD per 1M tokens. Peak is 2x.
OFF_PEAK = {"hit": 0.003, "miss": 0.15, "out": 0.60}


@dataclass
class Usage:
    hit: int = 0
    miss: int = 0
    out: int = 0
    calls: int = 0

    def add_from_api(self, data: dict) -> None:
        usage = data.get("usage") or {}
        self.hit += int(usage.get("prompt_cache_hit_tokens") or 0)
        miss = usage.get("prompt_cache_miss_tokens")
        prompt = int(usage.get("prompt_tokens") or 0)
        if miss is None:
            self.miss += max(prompt - int(usage.get("prompt_cache_hit_tokens") or 0), 0)
        else:
            self.miss += int(miss)
        self.out += int(usage.get("completion_tokens") or 0)
        self.calls += 1

    def cost_usd(self) -> float:
        rates = OFF_PEAK
        if not is_off_peak():
            rates = {k: v * 2 for k, v in OFF_PEAK.items()}
        return (self.hit * rates["hit"] + self.miss * rates["miss"] + self.out * rates["out"]) / 1_000_000


@dataclass
class RunBudget:
    usage: Usage = field(default_factory=Usage)
