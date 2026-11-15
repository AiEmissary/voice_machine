from __future__ import annotations

import ctypes
import time
import tkinter as tk
import uuid


class OverlayUI:
    def __init__(self, logger, bus, inserter_service):
        self.log = logger.log
        self.bus = bus
        self.inserter = inserter_service

        self.user32 = ctypes.windll.user32
        self.GWL_EXSTYLE = -20
        self.WS_EX_NOACTIVATE = 0x08000000
        self.WS_EX_TOOLWINDOW = 0x00000080
        self.GA_ROOT = 2

        self.transparent_key = "#00ff00"
        self.size = 88
        self.stt_ready = False
        self.state = "warming"

        self.root = tk.Tk()
        self.root.title("Speak V2")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.geometry(f"{self.size}x{self.size}+80+80")
        self.root.configure(bg=self.transparent_key)

        try:
            self.root.wm_attributes("-transparentcolor", self.transparent_key)
        except Exception:
            pass

        self.canvas = tk.Canvas(
            self.root,
            width=self.size,
            height=self.size,
            bg=self.transparent_key,
            bd=0,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self._draw_button(self.state)

        self.root.update_idletasks()
        self._make_no_activate(self.root)

        # Easier repositioning: support middle-drag, right-drag, and Shift+left-drag.
        self.drag_x = 0
        self.drag_y = 0
        self.canvas.bind("<ButtonPress-2>", self._start_drag)
        self.canvas.bind("<B2-Motion>", self._do_drag)
        self.canvas.bind("<ButtonPress-3>", self._start_drag)
        self.canvas.bind("<B3-Motion>", self._do_drag)
        self.canvas.bind("<Shift-ButtonPress-1>", self._start_drag)
        self.canvas.bind("<Shift-B1-Motion>", self._do_drag)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.root.bind_all("<ButtonRelease-1>", self._on_release, add="+")
        self.canvas.bind("<Double-Button-3>", self._close)

        self.recording = False
        self.releasing = False
        self.gesture_active = False
        self.last_press_ts = 0.0
        self.last_release_ts = 0.0
        self.recording_started_ts = 0.0
        self.press_cooldown_s = 0.18
        self.post_release_guard_s = 0.45
        self.max_hold_s = 15.0

        # Respect swapped mouse buttons on Windows.
        self.primary_vk = 0x01
        try:
            SM_SWAPBUTTON = 23
            swapped = self.user32.GetSystemMetrics(SM_SWAPBUTTON) != 0
            if swapped:
                self.primary_vk = 0x02
        except Exception:
            pass

        self.current_session_id: str | None = None

        self.inserter.set_overlay_hwnds([self.root.winfo_id(), self.canvas.winfo_id()])
        self.bus.register("ui", self._on_ui_event)
        self._track_external_foreground()
        self._poll_mouse_release()
        self._poll_stt_ready()

    @staticmethod
    def _rgb(r: int, g: int, b: int) -> str:
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def _lerp(a: int, b: int, t: float) -> int:
        return int(a + (b - a) * t)

    def _draw_button(self, state: str):
        self.canvas.delete("all")

        # Color profile from provided design.
        idle_edge = (36, 36, 36)
        idle_center = (112, 112, 112)
        hold_edge = (23, 63, 130)
        hold_center = (86, 162, 255)
        border = (18, 18, 18)

        if state == "listening":
            edge = hold_edge
            center = hold_center
        elif state == "warming":
            edge = (120, 82, 18)
            center = (238, 178, 66)
        elif state == "error":
            edge = (85, 20, 20)
            center = (170, 48, 48)
        else:
            edge = idle_edge
            center = idle_center

        cx = cy = self.size // 2
        r = (self.size // 2) - 2

        # Soft shadow
        self.canvas.create_oval(cx - r, cy - r + 3, cx + r, cy + r + 3, fill="#000000", outline="", stipple="gray50")

        # Concentric radial fill approximation
        layers = 30
        for i in range(layers, 0, -1):
            t = i / layers
            rr = self._lerp(center[0], edge[0], t)
            gg = self._lerp(center[1], edge[1], t)
            bb = self._lerp(center[2], edge[2], t)
            ri = int(r * t)
            self.canvas.create_oval(cx - ri, cy - ri, cx + ri, cy + ri, fill=self._rgb(rr, gg, bb), outline="")

        # Border
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=self._rgb(*border), width=2)

        # Center mark
        mark = "S" if state != "warming" else "…"
        self.canvas.create_text(
            cx,
            cy,
            text=mark,
            fill="#f5f5f5",
            font=("Segoe UI Semibold", 30, "bold"),
        )

    def _set_state(self, state: str):
        self.state = state
        self._draw_button(state)

    def _on_ui_event(self, envelope: dict) -> dict:
        name = envelope.get("name", "")
        payload = envelope.get("payload", {})

        # UX policy: never flash red. If STT is not ready, show warming; else idle.
        if envelope.get("type") == "error":
            self._set_state("warming" if not self.stt_ready else "idle")
            self.log(f"ui error: {payload}")
        elif name == "ui.state.listening":
            self._set_state("listening")
        elif name in ("ui.state.transcribing", "ui.state.inserting", "ui.state.success", "ui.state.error", "ui.state.idle"):
            self._set_state("warming" if not self.stt_ready else "idle")

        return {
            "id": envelope["id"],
            "type": "response",
            "ts": envelope["ts"],
            "source": "ui",
            "target": envelope["source"],
            "name": "ui.ack",
            "payload": {"ok": True},
        }

    def _make_no_activate(self, window):
        try:
            hwnd = window.winfo_id()
            ex = self.user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE)
            self.user32.SetWindowLongW(hwnd, self.GWL_EXSTYLE, ex | self.WS_EX_NOACTIVATE | self.WS_EX_TOOLWINDOW)
        except Exception:
            pass

    def _is_overlay_hwnd(self, hwnd: int | None) -> bool:
        if not hwnd:
            return False
        try:
            hwnd_i = int(hwnd)
            root = int(self.root.winfo_id())
            if hwnd_i == root:
                return True
            ancestor = self.user32.GetAncestor(ctypes.c_void_p(hwnd_i), self.GA_ROOT)
            return bool(ancestor and int(ancestor) == root)
        except Exception:
            return False

    def _track_external_foreground(self):
        try:
            fg = self.user32.GetForegroundWindow()
            fg_i = int(fg) if fg else None
            if fg_i and (not self._is_overlay_hwnd(fg_i)):
                self.inserter.update_last_external_hwnd(fg_i)
        except Exception:
            pass
        self.root.after(120, self._track_external_foreground)

    def _poll_stt_ready(self):
        try:
            resp = self.bus.send(
                source="ui",
                target="stt",
                msg_type="command",
                name="stt.status",
                payload={},
            )
            ready = bool(resp.get("payload", {}).get("ready")) if resp.get("type") == "response" else False
            if ready and (not self.stt_ready):
                self.stt_ready = True
                self.log("ui readiness: STT ready")
                if not self.recording:
                    self._set_state("idle")
            elif (not ready) and (not self.stt_ready):
                self._set_state("warming")
        except Exception:
            if not self.stt_ready:
                self._set_state("warming")
        self.root.after(500, self._poll_stt_ready)

    def _poll_mouse_release(self):
        # Safety net: with NOACTIVATE windows, Tk can occasionally miss release events.
        try:
            left_down = (self.user32.GetAsyncKeyState(self.primary_vk) & 0x8000) != 0
            if self.recording and (not left_down) and (not self.releasing):
                self._on_release()

            # Hard safety net: auto-release if hold appears stuck.
            if self.recording and (not self.releasing) and self.recording_started_ts:
                if (time.monotonic() - self.recording_started_ts) > self.max_hold_s:
                    self.log("ui safety auto-release fired")
                    self._on_release()

            # If a gesture got stuck before recording started, clear it once button is up.
            if self.gesture_active and (not self.recording) and (not left_down) and (not self.releasing):
                self.gesture_active = False
        except Exception:
            pass
        self.root.after(30, self._poll_mouse_release)

    def _start_drag(self, event):
        self.drag_x, self.drag_y = event.x, event.y

    def _do_drag(self, event):
        x = self.root.winfo_x() + (event.x - self.drag_x)
        y = self.root.winfo_y() + (event.y - self.drag_y)
        self.root.geometry(f"{self.size}x{self.size}+{x}+{y}")

    def _on_press(self, _event=None):
        now = time.monotonic()
        if self.recording or self.releasing or self.gesture_active:
            return
        if (now - self.last_press_ts) < self.press_cooldown_s:
            return
        if (now - self.last_release_ts) < self.post_release_guard_s:
            return

        # Ignore queued/stale press events if primary button is not currently down.
        try:
            primary_down = (self.user32.GetAsyncKeyState(self.primary_vk) & 0x8000) != 0
            if not primary_down:
                return
        except Exception:
            pass

        if not self.stt_ready:
            self._set_state("warming")
            return

        self.last_press_ts = now
        self.gesture_active = True

        try:
            self.current_session_id = str(uuid.uuid4())
            resp = self.bus.send(
                source="ui",
                target="orchestrator",
                msg_type="command",
                name="session.press_start",
                payload={"sessionId": self.current_session_id, "cursorHint": True},
            )
            if resp.get("type") == "error":
                self._set_state("idle")
                self.log(f"press_start failed: {resp.get('payload')}")
                self.current_session_id = None
                self.gesture_active = False
                return

            self.recording = True
            self.recording_started_ts = time.monotonic()
            self._set_state("listening")
        except Exception as e:
            self.log(f"ui press error: {e}")
            self._set_state("idle")
            self.current_session_id = None
            self.gesture_active = False

    def _on_release(self, _event=None):
        if not self.recording:
            return

        self.releasing = True
        self.recording = False
        self.recording_started_ts = 0.0
        self.last_release_ts = time.monotonic()
        self._set_state("idle")
        # Force immediate visual update before synchronous transcription pipeline runs.
        try:
            self.root.update_idletasks()
        except Exception:
            pass

        sid = self.current_session_id
        self.current_session_id = None
        if not sid:
            self.releasing = False
            return

        try:
            resp = self.bus.send(
                source="ui",
                target="orchestrator",
                msg_type="command",
                name="session.press_end",
                payload={"sessionId": sid},
            )
            if resp.get("type") == "error":
                self.log(f"press_end failed: {resp.get('payload')}")
        except Exception as e:
            self.log(f"ui release error: {e}")
            self._set_state("error")
            self.root.after(700, lambda: self._set_state("idle"))
        finally:
            self.releasing = False
            self.gesture_active = False

    def _close(self, _event=None):
        try:
            if self.recording and self.current_session_id:
                self.bus.send(
                    source="ui",
                    target="orchestrator",
                    msg_type="command",
                    name="session.cancel",
                    payload={"sessionId": self.current_session_id},
                )
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()
