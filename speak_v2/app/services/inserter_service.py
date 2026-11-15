from __future__ import annotations

import ctypes
import ctypes.wintypes
import time
from typing import Optional, Iterable, List

import pyperclip


ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("hwndActive", ctypes.c_void_p),
        ("hwndFocus", ctypes.c_void_p),
        ("hwndCapture", ctypes.c_void_p),
        ("hwndMenuOwner", ctypes.c_void_p),
        ("hwndMoveSize", ctypes.c_void_p),
        ("hwndCaret", ctypes.c_void_p),
        ("rcCaret", ctypes.c_long * 4),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.wintypes.LONG),
        ("dy", ctypes.wintypes.LONG),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.wintypes.DWORD),
        ("wParamL", ctypes.wintypes.WORD),
        ("wParamH", ctypes.wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.wintypes.DWORD), ("u", _INPUTUNION)]


class InserterService:
    WM_PASTE = 0x0302
    SW_RESTORE = 9
    GA_ROOT = 2

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL = 0x11
    VK_V = 0x56

    def __init__(self, logger):
        self.log = logger.log
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self.user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
        self.user32.SendInput.restype = ctypes.c_uint
        self.overlay_hwnds: list[int] = []
        self.last_external_hwnd: Optional[int] = None
        self.targets_by_session: dict[str, dict] = {}

    def set_overlay_hwnds(self, hwnds: Iterable[int]):
        self.overlay_hwnds = [int(h) for h in hwnds if h]

    def update_last_external_hwnd(self, hwnd: Optional[int]):
        if hwnd:
            self.last_external_hwnd = int(hwnd)

    def _class_name(self, hwnd: Optional[int]) -> str:
        if not hwnd:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(256)
            self.user32.GetClassNameW(ctypes.c_void_p(int(hwnd)), buf, 255)
            return buf.value or ""
        except Exception:
            return ""

    def _is_overlay(self, hwnd: Optional[int]) -> bool:
        if not hwnd:
            return False
        try:
            hwnd_i = int(hwnd)
            overlays = set(self.overlay_hwnds)
            if hwnd_i in overlays:
                return True
            ancestor = self.user32.GetAncestor(ctypes.c_void_p(hwnd_i), self.GA_ROOT)
            return bool(ancestor and int(ancestor) in overlays)
        except Exception:
            return False

    def _focus_target(self, target: dict) -> None:
        top_hwnd = target.get("topHwndInt")
        focus_hwnd = target.get("focusHwndInt")
        if not top_hwnd:
            return
        try:
            # Restoring a maximized window would shrink it on every dictation.
            # Only restore when the target is actually minimized.
            if self.user32.IsIconic(ctypes.c_void_p(int(top_hwnd))):
                self.user32.ShowWindow(ctypes.c_void_p(int(top_hwnd)), self.SW_RESTORE)
            self.user32.SetForegroundWindow(ctypes.c_void_p(int(top_hwnd)))
            self.user32.SetActiveWindow(ctypes.c_void_p(int(top_hwnd)))
            if focus_hwnd:
                self.user32.SetFocus(ctypes.c_void_p(int(focus_hwnd)))
        except Exception:
            pass

    def _get_foreground_hwnd(self) -> Optional[int]:
        try:
            fg = self.user32.GetForegroundWindow()
            return int(fg) if fg else None
        except Exception:
            return None

    def _ensure_target_foreground(self, target: dict, timeout_s: float = 0.35) -> bool:
        top_hwnd = target.get("topHwndInt")
        if not top_hwnd:
            return False
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            self._focus_target(target)
            fg = self._get_foreground_hwnd()
            if fg and int(fg) == int(top_hwnd):
                return True
            time.sleep(0.03)
        return False

    def capture_target(self, session_id: str) -> dict:
        fg = self.user32.GetForegroundWindow()
        fg_i = int(fg) if fg else None
        fg_cls = self._class_name(fg_i)

        # Prefer a known non-overlay external target if foreground is our overlay
        # or another Tk overlay window.
        seed = fg_i
        if self._is_overlay(seed) or fg_cls == "TkTopLevel":
            seed = self.last_external_hwnd

        seed_cls = self._class_name(seed)
        if self._is_overlay(seed) or seed_cls == "TkTopLevel":
            seed = None

        if not seed:
            self.log(
                f"capture_target no_external_target session={session_id} fg={hex(fg_i) if fg_i else None}<{fg_cls}> last_external={hex(self.last_external_hwnd) if self.last_external_hwnd else None}<{self._class_name(self.last_external_hwnd)}>")
            raise RuntimeError("no_external_target")

        thread_id = self.user32.GetWindowThreadProcessId(ctypes.c_void_p(seed), None)
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        ok = self.user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)) if thread_id else 0

        if ok:
            top = int(info.hwndActive) if info.hwndActive else seed
            focus = int(info.hwndFocus) if info.hwndFocus else top
        else:
            top, focus = seed, seed

        top_cls = self._class_name(top)
        focus_cls = self._class_name(focus)

        if self._is_overlay(top) or top_cls == "TkTopLevel":
            fallback = self.last_external_hwnd
            if fallback and (not self._is_overlay(fallback)) and self._class_name(fallback) != "TkTopLevel":
                top = fallback
            else:
                top = seed
            top_cls = self._class_name(top)

        if self._is_overlay(focus) or focus_cls == "TkTopLevel":
            focus = top
            focus_cls = self._class_name(focus)

        if (not top) or top_cls == "TkTopLevel":
            raise RuntimeError("no_external_target")

        target = {
            "sessionId": session_id,
            "topHwnd": hex(top) if top else None,
            "focusHwnd": hex(focus) if focus else None,
            "topClass": top_cls,
            "focusClass": focus_cls,
            "topHwndInt": top,
            "focusHwndInt": focus,
        }
        self.targets_by_session[session_id] = target
        self.log(f"capture_target session={session_id} top={target['topHwnd']}<{top_cls}> focus={target['focusHwnd']}<{focus_cls}>")
        return target

    def _send_ctrl_v(self) -> dict:
        try:
            seq: List[INPUT] = []
            for vk, flags in [
                (self.VK_CONTROL, 0),
                (self.VK_V, 0),
                (self.VK_V, self.KEYEVENTF_KEYUP),
                (self.VK_CONTROL, self.KEYEVENTF_KEYUP),
            ]:
                i = INPUT()
                i.type = self.INPUT_KEYBOARD
                i.u.ki.wVk = vk
                i.u.ki.wScan = 0
                i.u.ki.dwFlags = flags
                i.u.ki.time = 0
                i.u.ki.dwExtraInfo = 0
                seq.append(i)

            arr = (INPUT * len(seq))(*seq)
            self.kernel32.SetLastError(0)
            sent = self.user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))
            gle = self.kernel32.GetLastError()
            ok = sent == len(seq)
            return {"name": "sendinput_ctrl_v", "success": ok, "code": 0 if ok else "partial_send", "detail": f"sent={sent}/{len(seq)} gle={gle}"}
        except Exception as e:
            return {"name": "sendinput_ctrl_v", "success": False, "code": "sendinput_exception", "detail": str(e)}

    def _wm_paste(self, hwnd: Optional[int], name: str) -> dict:
        if not hwnd:
            return {"name": name, "success": False, "code": "no_hwnd", "detail": "no hwnd"}
        try:
            self.kernel32.SetLastError(0)
            self.user32.SendMessageW(ctypes.c_void_p(int(hwnd)), self.WM_PASTE, 0, 0)
            gle = self.kernel32.GetLastError()
            return {"name": name, "success": True, "code": 0, "detail": f"gle={gle}"}
        except Exception as e:
            return {"name": name, "success": False, "code": "wm_paste_exception", "detail": str(e)}

    def apply(self, session_id: str, text: str, strategy_order: List[str]) -> dict:
        target = self.targets_by_session.get(session_id)
        if not target:
            raise RuntimeError("no_target_for_session")

        t0 = time.perf_counter()
        attempts: List[dict] = []

        pyperclip.copy((text or "") + " ")
        self._focus_target(target)
        time.sleep(0.05)

        success = False
        used = ""
        for strategy in strategy_order:
            if strategy == "uia_insert":
                a = {"name": "uia_insert", "success": False, "code": "unsupported_control", "detail": "not_implemented"}
            elif strategy == "wm_paste_focus":
                focused = self._ensure_target_foreground(target)
                if not focused:
                    fg = self._get_foreground_hwnd()
                    a = {
                        "name": "wm_paste_focus",
                        "success": False,
                        "code": "focus_not_acquired",
                        "detail": f"fg={hex(fg) if fg else None} target={target.get('topHwnd')}",
                    }
                else:
                    a = self._wm_paste(target.get("focusHwndInt"), strategy)
            elif strategy == "wm_paste_top":
                focused = self._ensure_target_foreground(target)
                if not focused:
                    fg = self._get_foreground_hwnd()
                    a = {
                        "name": "wm_paste_top",
                        "success": False,
                        "code": "focus_not_acquired",
                        "detail": f"fg={hex(fg) if fg else None} target={target.get('topHwnd')}",
                    }
                else:
                    a = self._wm_paste(target.get("topHwndInt"), strategy)
            elif strategy == "sendinput_ctrl_v":
                focused = self._ensure_target_foreground(target)
                if not focused:
                    fg = self._get_foreground_hwnd()
                    a = {
                        "name": "sendinput_ctrl_v",
                        "success": False,
                        "code": "focus_not_acquired",
                        "detail": f"fg={hex(fg) if fg else None} target={target.get('topHwnd')}",
                    }
                else:
                    a = self._send_ctrl_v()
            elif strategy == "clipboard_only":
                a = {"name": "clipboard_only", "success": True, "code": 0, "detail": "copied"}
            else:
                a = {"name": strategy, "success": False, "code": "unknown_strategy", "detail": strategy}

            attempts.append(a)
            self.log(f"insert attempt session={session_id} {a['name']} success={a['success']} code={a['code']}")
            if a["success"]:
                success = True
                used = a["name"]
                if used != "clipboard_only":
                    break

        return {
            "sessionId": session_id,
            "success": success,
            "strategyUsed": used,
            "attempts": attempts,
            "insertMs": int((time.perf_counter() - t0) * 1000),
        }

    def handle(self, envelope: dict) -> dict:
        name = envelope["name"]
        payload = envelope.get("payload", {})
        req_id = envelope["id"]

        if name == "insert.capture_target":
            target = self.capture_target(payload["sessionId"])
            resp_payload = {k: target[k] for k in ["sessionId", "topHwnd", "focusHwnd", "topClass", "focusClass"]}
            return {
                "id": req_id,
                "type": "response",
                "ts": envelope["ts"],
                "source": "insert",
                "target": envelope["source"],
                "name": "insert.target_captured",
                "payload": resp_payload,
            }

        if name == "insert.apply":
            result = self.apply(
                session_id=payload["sessionId"],
                text=payload.get("text", ""),
                strategy_order=payload.get("strategyOrder")
                or ["sendinput_ctrl_v", "uia_insert", "wm_paste_focus", "wm_paste_top", "clipboard_only"],
            )
            return {
                "id": req_id,
                "type": "response",
                "ts": envelope["ts"],
                "source": "insert",
                "target": envelope["source"],
                "name": "insert.result",
                "payload": result,
            }

        raise RuntimeError(f"unknown_command:{name}")
