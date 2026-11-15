from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


DEFAULT_ORDER = ["sendinput_ctrl_v", "wm_paste_focus", "uia_insert", "wm_paste_top", "clipboard_only"]
BROWSER_HINTS = ("chrome_widgetwin", "mozilla", "msedge", "applicationframewindow")


class StrategyBot:
    def __init__(self, memory_path: Path):
        self.memory_path = memory_path
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        self.memory: Dict[str, dict] = {}
        self._load()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _load(self) -> None:
        if self.memory_path.exists():
            try:
                self.memory = json.loads(self.memory_path.read_text(encoding="utf-8"))
            except Exception:
                self.memory = {}

    def _save(self) -> None:
        self.memory_path.write_text(json.dumps(self.memory, indent=2), encoding="utf-8")

    @staticmethod
    def _key(top_class: str, focus_class: str) -> str:
        return f"{top_class}|{focus_class}"

    @staticmethod
    def _class_default_order(top_class: str, focus_class: str) -> List[str]:
        cls = f"{top_class}|{focus_class}".lower()
        if any(h in cls for h in BROWSER_HINTS):
            return ["sendinput_ctrl_v", "wm_paste_focus", "uia_insert", "wm_paste_top", "clipboard_only"]
        return DEFAULT_ORDER.copy()

    @staticmethod
    def _normalize_order(order: List[str], default_order: List[str]) -> List[str]:
        # Keep strategy set strict and class-prioritized to avoid typing into wrong place.
        valid_order = [s for s in order if s in DEFAULT_ORDER]
        return [s for s in default_order if s in valid_order] + [s for s in default_order if s not in valid_order]

    def get_strategy_order(self, top_class: str, focus_class: str) -> List[str]:
        key = self._key(top_class, focus_class)
        class_default = self._class_default_order(top_class, focus_class)
        rec = self.memory.get(key)
        if not rec:
            return class_default
        order = rec.get("preferredStrategyOrder", class_default)
        return self._normalize_order(order, class_default)

    def report_result(self, top_class: str, focus_class: str, strategy_used: str, success: bool) -> None:
        key = self._key(top_class, focus_class)
        rec = self.memory.setdefault(
            key,
            {
                "preferredStrategyOrder": DEFAULT_ORDER.copy(),
                "lastSuccess": None,
                "successRate": 0.0,
                "success": {},
                "fail": {},
                "total": 0,
                "ok": 0,
            },
        )

        rec["total"] = int(rec.get("total", 0)) + 1
        if success:
            rec["ok"] = int(rec.get("ok", 0)) + 1
            rec["lastSuccess"] = self._now_iso()

        bucket = "success" if success else "fail"
        rec[bucket][strategy_used] = int(rec[bucket].get(strategy_used, 0)) + 1

        if success and strategy_used in rec["preferredStrategyOrder"]:
            order = rec["preferredStrategyOrder"]
            order.remove(strategy_used)
            order.insert(0, strategy_used)

        class_default = self._class_default_order(top_class, focus_class)
        rec["preferredStrategyOrder"] = self._normalize_order(rec.get("preferredStrategyOrder", class_default), class_default)

        total = max(1, int(rec.get("total", 1)))
        ok = int(rec.get("ok", 0))
        rec["successRate"] = round(ok / total, 4)

        self._save()
