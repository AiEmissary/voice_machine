import json
import queue
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, Optional

from app.runtime.voice_runtime import VoiceRuntime


STATE_COLORS = {
    "idle": "#5f6368",
    "off": "#5f6368",
    "starting": "#1e88e5",
    "listening": "#2e7d32",
    "waiting": "#2e7d32",
    "detected": "#f9a825",
    "processing": "#ef6c00",
    "armed": "#00897b",
    "capturing": "#00897b",
    "running": "#1e88e5",
    "ready": "#2e7d32",
    "done": "#00897b",
    "degraded": "#ef6c00",
    "error": "#c62828",
}


class VoiceMachineUI:
    def __init__(self, runtime: Optional[VoiceRuntime], app_title: str = "voice_machine"):
        self.runtime = runtime
        self.root = tk.Tk()
        self.root.title(app_title)
        self.root.geometry("900x620")
        self.root.minsize(820, 560)

        self.event_queue: "queue.Queue[tuple[str, Dict[str, Any]]]" = queue.Queue()

        self.status_vars = {
            "mic": tk.StringVar(value="off"),
            "wake": tk.StringVar(value="idle"),
            "capture": tk.StringVar(value="idle"),
            "transcribe": tk.StringVar(value="idle"),
            "error": tk.StringVar(value="none"),
            "inputDeviceName": tk.StringVar(value="(not connected)"),
        }

        self.last_result_var = tk.StringVar(value="(no transcript yet)")
        self.last_output_json = tk.StringVar(value="{}")

        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_layout(self) -> None:
        wrapper = ttk.Frame(self.root, padding=10)
        wrapper.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(wrapper, text="voice_machine", font=("Segoe UI", 16, "bold"))
        title.grid(row=0, column=0, sticky="w")

        btn_row = ttk.Frame(wrapper)
        btn_row.grid(row=0, column=1, sticky="e")
        self.start_btn = ttk.Button(btn_row, text="Start", command=self._start_runtime)
        self.start_btn.pack(side=tk.LEFT, padx=4)
        self.stop_btn = ttk.Button(btn_row, text="Stop", command=self._stop_runtime)
        self.stop_btn.pack(side=tk.LEFT, padx=4)

        indicators = ttk.LabelFrame(wrapper, text="Indicators", padding=10)
        indicators.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 8))

        self.indicator_labels: Dict[str, tk.Label] = {}
        cols = ["mic", "wake", "capture", "transcribe", "error"]
        for idx, key in enumerate(cols):
            frame = ttk.Frame(indicators)
            frame.grid(row=0, column=idx, padx=8, sticky="nsew")
            ttk.Label(frame, text=key.capitalize(), font=("Segoe UI", 10, "bold")).pack()
            chip = tk.Label(
                frame,
                textvariable=self.status_vars[key],
                fg="white",
                bg=STATE_COLORS.get("idle", "#5f6368"),
                width=14,
                padx=6,
                pady=6,
            )
            chip.pack(pady=(4, 0))
            self.indicator_labels[key] = chip

        device_frame = ttk.Frame(wrapper)
        device_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Label(device_frame, text="Input Device:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        ttk.Label(device_frame, textvariable=self.status_vars["inputDeviceName"]).pack(side=tk.LEFT, padx=(6, 0))

        ack_frame = ttk.LabelFrame(wrapper, text="Last Transcript", padding=10)
        ack_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(8, 8))
        ttk.Label(ack_frame, textvariable=self.last_result_var, wraplength=850).pack(anchor="w")

        output_frame = ttk.LabelFrame(wrapper, text="Latest Output JSON", padding=10)
        output_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
        self.output_text = tk.Text(output_frame, height=8, wrap=tk.WORD)
        self.output_text.pack(fill=tk.BOTH, expand=True)
        self.output_text.insert("1.0", "{}")
        self.output_text.configure(state=tk.DISABLED)

        logs_frame = ttk.LabelFrame(wrapper, text="Runtime Log", padding=10)
        logs_frame.grid(row=5, column=0, columnspan=2, sticky="nsew")
        self.log_text = tk.Text(logs_frame, height=12, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(state=tk.DISABLED)

        wrapper.columnconfigure(0, weight=1)
        wrapper.columnconfigure(1, weight=1)
        for r in [1, 3, 4, 5]:
            wrapper.rowconfigure(r, weight=1)

    def emit_from_runtime(self, event_type: str, payload: Dict[str, Any]) -> None:
        self.event_queue.put((event_type, payload or {}))

    def run(self, auto_start: bool = True) -> None:
        if auto_start:
            self._start_runtime()
        self.root.after(100, self._poll_events)
        self.root.mainloop()

    def _start_runtime(self) -> None:
        if self.runtime is None:
            self._append_log("Runtime unavailable.")
            return
        self._append_log("Starting runtime...")
        self.runtime.start()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)

    def _stop_runtime(self) -> None:
        if self.runtime is None:
            return
        self._append_log("Stopping runtime...")
        self.runtime.stop()
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)

    def _on_close(self) -> None:
        try:
            self.runtime.stop()
        except Exception:
            pass
        self.root.destroy()

    def _poll_events(self) -> None:
        try:
            while True:
                event_type, payload = self.event_queue.get_nowait()
                self._handle_event(event_type, payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        if event_type == "status":
            self._set_status(payload)
            return

        if event_type == "ack":
            msg = str(payload.get("message") or "")
            if msg:
                self._append_log(f"ACK: {msg}")
            return

        if event_type == "transcript":
            msg = str(payload.get("message") or "").strip()
            result = payload.get("result") or {}
            self.last_result_var.set(msg or "(empty)")
            self._set_output_json(result)
            self._append_log(f"TRANSCRIPT: {msg}")
            return

        if event_type == "error":
            msg = str(payload.get("message") or "unknown error")
            self.status_vars["error"].set(msg[:48])
            self._refresh_chip("error", "error")
            self._append_log(f"ERROR: {msg}")
            return

        if event_type == "log":
            self._append_log(str(payload.get("message") or ""))

    def _set_status(self, payload: Dict[str, Any]) -> None:
        for key in ("mic", "wake", "capture", "transcribe"):
            value = str(payload.get(key) or "idle")
            self.status_vars[key].set(value)
            self._refresh_chip(key, value)

        input_name = str(payload.get("inputDeviceName") or "(unknown)")
        self.status_vars["inputDeviceName"].set(input_name)

        # clear transient error if all looks healthy
        if self.status_vars["mic"].get() in ("listening", "waiting", "starting"):
            if self.status_vars["error"].get() != "none":
                self.status_vars["error"].set("none")
                self._refresh_chip("error", "idle")

    def _refresh_chip(self, key: str, value: str) -> None:
        chip = self.indicator_labels.get(key)
        if not chip:
            return
        chip.configure(bg=STATE_COLORS.get(value, "#5f6368"))

    def _set_output_json(self, data: Dict[str, Any]) -> None:
        text = json.dumps(data or {}, indent=2, ensure_ascii=False)
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert("1.0", text)
        self.output_text.configure(state=tk.DISABLED)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
