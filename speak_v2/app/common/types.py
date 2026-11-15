from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Dict
import time


@dataclass
class TargetInfo:
    top_hwnd: Optional[int] = None
    focus_hwnd: Optional[int] = None
    top_class: str = ""
    focus_class: str = ""


@dataclass
class TranscriptionResult:
    text: str
    audio_ms: int = 0
    transcribe_ms: int = 0
    model: str = ""
    error: Optional[str] = None


@dataclass
class InsertAttempt:
    name: str
    success: bool
    detail: str = ""


@dataclass
class InsertResult:
    success: bool
    strategy_used: str = ""
    attempts: List[InsertAttempt] = field(default_factory=list)
    insert_ms: int = 0


@dataclass
class SessionContext:
    session_id: str
    started_at: float = field(default_factory=time.time)
    target: TargetInfo = field(default_factory=TargetInfo)
    metadata: Dict[str, str] = field(default_factory=dict)
