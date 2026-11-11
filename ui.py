from __future__ import annotations

import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

# Qt's video backend prints the ffmpeg stream banner — codec, bitrate, the
# whole signed googlevideo URL — to the console for every stream it opens. That
# is two screenfuls per video, it looks like an error to anyone reading the log,
# and it puts a URL carrying the viewer's IP address into a terminal people
# paste into bug reports. Silenced here, before Qt initialises its logging.
# An explicit setting from the user is left alone.
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")

from PyQt6.QtCore import (
    QEvent, QEasingCurve, QLineF, QMimeData, QObject, QParallelAnimationGroup, QPointF,
    QPoint, QPropertyAnimation, QRect, QRectF, QSize, QSizeF, Qt, QTimer,
    QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QCursor, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QPolygonF, QRadialGradient, QShortcut,
)
# Video playback for the HUD. Part of PyQt6, so it costs no new dependency —
# but the multimedia plugins are a separate piece of the Qt install and can be
# absent on a stripped-down system, so a failure here disables one feature
# rather than stopping JARVIS from starting.
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
    HAVE_VIDEO = True
except Exception as _e:            # noqa: BLE001 - reported, never fatal
    QAudioOutput = QMediaPlayer = QGraphicsVideoItem = None
    HAVE_VIDEO = False
    print(f"[Video] playback unavailable ({_e}) — the HUD will not show video.")

from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QGraphicsScene, QGraphicsView,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar, QSlider,
)

try:
    from core.avatar import HoloAvatar
except Exception:      # pragma: no cover — HUD must never die over cosmetics
    HoloAvatar = None


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Single source of truth for the release name — the window title, the header
# badge and the readme must never disagree again.
APP_VERSION  = "MARK LVI"
APP_PROTOCOL = APP_VERSION.split()[-1]

_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 148
_RIGHT_W = 340

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    BG        = "#0a030a"
    PANEL     = "#120510"
    PANEL2    = "#180716"
    BORDER    = "#3d1130"
    BORDER_B  = "#7a1a5c"
    BORDER_A  = "#5c1345"
    PRI       = "#ff4da6"
    PRI_DIM   = "#991f63"
    PRI_GHO   = "#2e001a"
    ACC       = "#ba55ff"
    ACC2      = "#ff77c2"
    GREEN     = "#00ffaa"
    GREEN_D   = "#00aa66"
    RED       = "#ff3366"
    MUTED_C   = "#ff3366"
    TEXT      = "#ffecf7"
    TEXT_DIM  = "#d6a0be"
    TEXT_MED  = "#f0b8d8"
    WHITE     = "#ffffff"
    DARK      = "#0a030a"
    BAR_BG    = "#180414"


# Keys tied to the accent colour — status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # near-grey accent → the whole theme is desaturated

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    LIVE full theme change. Replaces the old palette colours with the new ones
    in EVERY widget's stylesheet across the app and repaints them. This way the
    colour change applies INSTANTLY across the whole interface — panels, buttons,
    borders included — not just the painted elements. No restart needed.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        # Probe caches — GPU (NVML) and temperature (WMI) are the expensive
        # queries; initialise their handles once and reuse them instead of
        # rebuilding a connection on every poll.
        self._slow_tick = 0            # gpu/temp refreshed every 3rd cycle
        self._pynvml    = None         # cached pynvml module + device handle
        self._pynvml_h  = None
        self._pynvml_ok = None         # None=untested, False=unavailable here
        self._nv_unix   = None         # cached (lib, dev) for Linux/macOS NVML
        self._wmi_conn  = None         # cached WMI connection (creating one is slow)
        self._wmi_ok    = None         # None=untested, False=unavailable here
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(2.0)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        # GPU and temperature change slowly and are the most expensive probes
        # (NVML / WMI) — refresh them every 3rd cycle (~6 s) instead of every
        # cycle, reusing the previous reading in between.
        self._slow_tick = (self._slow_tick + 1) % 3
        if self._slow_tick == 1:
            gpu = self._get_gpu()
            tmp = self._get_temp()
        else:
            gpu = self.gpu
            tmp = self.tmp

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    self._pynvml    = pynvml
                    self._pynvml_h  = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                return float(self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu)
            except Exception:
                self._pynvml_ok = False

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes — init once, reuse
        try:
            import ctypes

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            if self._nv_unix is None:
                _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"
                nv = ctypes.CDLL(_lib)
                nv.nvmlInit_v2()
                dev = ctypes.c_void_p()
                nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                self._nv_unix = (nv, dev)

            nv, dev = self._nv_unix
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess). Reuse a single
        # connection — building a fresh wmi.WMI() on every poll spins up a COM
        # connection each time and is very slow. Give up after one failure.
        if _OS == "Windows" and self._wmi_ok is not False:
            try:
                if self._wmi_conn is None:
                    import wmi  # type: ignore
                    self._wmi_conn = wmi.WMI(namespace="root/wmi")
                tz = self._wmi_conn.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                self._wmi_ok   = False
                self._wmi_conn = None

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

def rgba(c: QColor, a: float | int) -> QColor:
    return QColor(c.red(), c.green(), c.blue(), max(0, min(255, int(a))))


def keena_font(size: int, bold: bool = False, spacing: float = 0.0) -> QFont:
    f = QFont("Bahnschrift SemiCondensed", size)
    f.setBold(bold)
    if spacing > 0.0:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


class HudFadeOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            p.end()
            return

        fade_h = min(30, max(12, int(h * 0.18)))

        # Top blur / gradient fade
        top_grad = QLinearGradient(0, 0, 0, fade_h)
        top_grad.setColorAt(0.0, QColor(14, 4, 12, 245))
        top_grad.setColorAt(0.5, QColor(14, 4, 12, 175))
        top_grad.setColorAt(0.85, QColor(14, 4, 12, 70))
        top_grad.setColorAt(1.0, QColor(14, 4, 12, 0))
        p.fillRect(QRect(0, 0, w, fade_h), top_grad)

        # Bottom blur / gradient fade
        bot_grad = QLinearGradient(0, h - fade_h, 0, h)
        bot_grad.setColorAt(0.0, QColor(14, 4, 12, 0))
        bot_grad.setColorAt(0.15, QColor(14, 4, 12, 70))
        bot_grad.setColorAt(0.5, QColor(14, 4, 12, 175))
        bot_grad.setColorAt(1.0, QColor(14, 4, 12, 245))
        p.fillRect(QRect(0, h - fade_h, w, fade_h), bot_grad)

        p.end()


class HudConversationWidget(QTextEdit):
    _stream_user_sig = pyqtSignal(str)
    _stream_reply_sig = pyqtSignal(str)
    _add_msg_sig = pyqtSignal(str, str)

    def __init__(self, assistant_name: str = "JARVIS", parent=None):
        super().__init__(parent)
        self._asst_name = (assistant_name or "JARVIS").replace(".", "").strip().upper()
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.document().setMaximumBlockCount(400)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                color: {C.TEXT};
                border: none;
                padding: 10px 4px;
                selection-background-color: rgba(255, 77, 166, 0.35);
            }}
            QScrollBar:vertical {{
                width: 0px;
                height: 0px;
                background: transparent;
                border: none;
            }}
        """)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setAutoFillBackground(False)

        self._messages: list[tuple[str, str]] = []
        self._streaming_user: str = ""
        self._streaming_reply: str = ""

        # Top & bottom blur/gradient fade overlay
        self._fade_overlay = HudFadeOverlay(self)

        self._stream_user_sig.connect(self._on_stream_user)
        self._stream_reply_sig.connect(self._on_stream_reply)
        self._add_msg_sig.connect(self._on_add_msg)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "_fade_overlay") and self._fade_overlay:
            self._fade_overlay.setGeometry(0, 0, self.width(), self.height())
            self._fade_overlay.raise_()

    def append_message(self, speaker: str, text: str):
        self._add_msg_sig.emit(speaker, text)

    def stream_user(self, text: str):
        self._stream_user_sig.emit(text)

    def stream_reply(self, text: str):
        self._stream_reply_sig.emit(text)

    def _on_stream_user(self, text: str):
        self._streaming_user = str(text or "").strip()
        self._render_conversation()

    def _on_stream_reply(self, text: str):
        self._streaming_reply = str(text or "").strip()
        self._render_conversation()

    def _on_add_msg(self, speaker: str, text: str):
        clean = str(text or "").strip()
        if not clean:
            return
        spk = speaker.lower().strip()
        if spk == "you":
            self._streaming_user = ""
            if not (self._messages and self._messages[-1] == ("you", clean)):
                self._messages.append(("you", clean))
        elif spk in ("ai", "jarvis", self._asst_name.lower()):
            self._streaming_reply = ""
            if not (self._messages and self._messages[-1] == ("jarvis", clean)):
                self._messages.append(("jarvis", clean))
        else:
            self._messages.append(("sys", clean))
        self._render_conversation()

    def _render_conversation(self):
        html_parts = []
        html_parts.append(f'<div style="font-family: \'Courier New\', monospace; color: {C.TEXT}; font-size: 11px;">')

        for spk, txt in self._messages[-50:]:
            escaped = (txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>"))
            if spk == "you":
                html_parts.append(
                    f'<div style="margin-top: 6px; margin-bottom: 2px;">'
                    f'<span style="color: {C.TEXT_DIM}; font-size: 9px; font-weight: bold; letter-spacing: 1.5px;">YOU</span>'
                    f'</div>'
                    f'<div style="color: #ffffff; margin-bottom: 8px; line-height: 1.35;">{escaped}</div>'
                )
            elif spk == "jarvis":
                html_parts.append(
                    f'<div style="margin-top: 6px; margin-bottom: 2px;">'
                    f'<span style="color: {C.PRI}; font-size: 9px; font-weight: bold; letter-spacing: 1.5px;">{self._asst_name}</span>'
                    f'</div>'
                    f'<div style="color: {C.TEXT}; border-left: 2px solid {C.PRI}; padding-left: 6px; margin-bottom: 8px; line-height: 1.35;">{escaped}</div>'
                )
            else:
                html_parts.append(
                    f'<div style="color: {C.TEXT_DIM}; font-size: 10px; font-style: italic; margin-bottom: 4px;">{escaped}</div>'
                )

        if self._streaming_user:
            esc = self._streaming_user.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
            html_parts.append(
                f'<div style="margin-top: 6px; margin-bottom: 2px;">'
                f'<span style="color: {C.TEXT_DIM}; font-size: 9px; font-weight: bold; letter-spacing: 1.5px;">YOU</span>'
                f'</div>'
                f'<div style="color: #ffffff; margin-bottom: 8px; line-height: 1.35;">{esc} <span style="color: {C.PRI};">...</span></div>'
            )

        if self._streaming_reply:
            esc = self._streaming_reply.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
            html_parts.append(
                f'<div style="margin-top: 6px; margin-bottom: 2px;">'
                f'<span style="color: {C.PRI}; font-size: 9px; font-weight: bold; letter-spacing: 1.5px;">{self._asst_name}</span>'
                f'</div>'
                f'<div style="color: {C.TEXT}; border-left: 2px solid {C.PRI}; padding-left: 6px; margin-bottom: 8px; line-height: 1.35;">{esc} <span style="color: {C.PRI}; font-weight: bold;">█</span></div>'
            )

        html_parts.append('</div>')

        bar = self.verticalScrollBar()
        old_val = bar.value()
        was_at_bottom = (bar.maximum() == 0) or (old_val >= bar.maximum() - 35)

        self.setHtml("".join(html_parts))

        if was_at_bottom or self._streaming_reply or self._streaming_user:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(old_val)

    def clear_history(self):
        self._messages.clear()
        self._streaming_user = ""
        self._streaming_reply = ""
        self.setHtml("")


class HudCanvas(QWidget):
    reactor_clicked = pyqtSignal()

    def __init__(self, face_path: str = "", assistant_name: str = "JARVIS", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(40, 40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._assistant_name = assistant_name.replace(".", "").strip() or "JARVIS"
        self.state = "STANDBY"
        self.muted = False
        self.speaking = False
        self.hud_style = "core"

        # Embedded Conversation History widget
        self.conversation = HudConversationWidget(assistant_name=self._assistant_name, parent=self)

        # Audio & animation dynamics
        self._live_amp = 0.0
        self._amp_disp = 0.0
        self._level_target = 0.0
        self._visemes = None
        self._vis_i = None

        self.spin = 0.0
        self.boot = 1.0
        self.wave = [0.0] * 72
        self.cards = []               # list of (action_text, timestamp)
        self.user_text = ""
        self.reply_text = ""
        self.reply_shown = 0.0
        self.stat = "LIVE"
        self.cpu = 0.0
        self.mem = 0.0
        self.gemini_online = True
        self._big_mode = False
        self._mini_mode = False

        self._last_t = time.time()
        self._step_t = time.time()
        self._drag_start = None
        self._win_start = None
        self._is_drag = False

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)           # 60 FPS animation

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_conversation_geometry()

    def showEvent(self, e):
        super().showEvent(e)
        self._update_conversation_geometry()

    def _update_conversation_geometry(self):
        if not hasattr(self, "conversation") or not self.conversation:
            return
        if getattr(self, "_mini_mode", False) or getattr(self, "_big_mode", False):
            self.conversation.hide()
            return

        w, h = self.width(), self.height()
        if w <= 120 or h <= 120:
            self.conversation.hide()
            return

        BASE_W = 420.0
        BASE_H = 540.0
        scale = min(w / BASE_W, h / BASE_H)
        off_x = (w - BASE_W * scale) / 2.0
        off_y = (h - BASE_H * scale) / 2.0

        top_y = 348.0
        bot_y = BASE_H - 14.0
        conv_h = max(40.0, bot_y - top_y)

        c_x = int(off_x + 18.0 * scale)
        c_y = int(off_y + top_y * scale)
        c_w = int((BASE_W - 36.0) * scale)
        c_h = int(conv_h * scale)

        self.conversation.setGeometry(c_x, c_y, c_w, c_h)
        self.conversation.show()

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        pass

    def push_visemes(self, frames, hop: float, at: float) -> None:
        try:
            if not frames:
                return
            hop = max(1e-3, float(hop))
            at = float(at)
            new = list(frames)
            cur = self._visemes
            if cur is not None:
                old, t0, ohop = cur
                if abs(ohop - hop) < 1e-6:
                    i = int(round((at - t0) / hop))
                    if 0 <= i <= len(old) + 1:
                        merged = old[:i] + new
                        played = int((time.time() - t0) / hop) - 2
                        if played > 60:
                            merged = merged[played:]
                            t0 += played * hop
                            if self._vis_i is not None:
                                self._vis_i = max(0, self._vis_i - played)
                        self._visemes = (merged, t0, hop)
                        return
            self._visemes = (new, at, hop)
            self._vis_i = None
        except Exception:
            pass

    def set_audio_level(self, level: float) -> None:
        try:
            lv = max(0.0, min(1.0, float(level)))
            if lv > self._live_amp:
                self._live_amp = lv
        except (TypeError, ValueError):
            pass

    def set_metrics(self, cpu: float, mem: float, stat: str = "") -> None:
        self.cpu = float(cpu)
        self.mem = float(mem)
        if stat:
            self.stat = str(stat)

    def stream_user_text(self, text: str) -> None:
        self.user_text = str(text or "").strip()
        if hasattr(self, "conversation") and self.conversation:
            self.conversation.stream_user(text)

    def stream_reply_text(self, text: str) -> None:
        self.reply_text = str(text or "").strip()
        if hasattr(self, "conversation") and self.conversation:
            self.conversation.stream_reply(text)

    def set_user_text(self, text: str) -> None:
        self.user_text = str(text or "").strip()
        if hasattr(self, "conversation") and self.conversation:
            self.conversation.append_message("you", self.user_text)

    def set_reply_text(self, text: str) -> None:
        new_text = str(text or "").strip()
        keep = bool(self.reply_text and new_text.startswith(self.reply_text))
        self.reply_text = new_text
        if not keep:
            self.reply_shown = 0.0
        if hasattr(self, "conversation") and self.conversation:
            self.conversation.append_message("jarvis", self.reply_text)

    def add_card(self, card_text: str) -> None:
        clean = str(card_text or "").strip()
        if clean:
            self.cards = (self.cards + [(clean, time.time())])[-3:]

    def set_mini_mode(self, mini: bool) -> None:
        self._mini_mode = bool(mini)
        self._update_conversation_geometry()
        self.update()

    def set_big_mode(self, big: bool) -> None:
        self._big_mode = bool(big)
        self._update_conversation_geometry()
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.globalPosition().toPoint()
            self._win_start = self.window().pos()
            self._press_pos = e.position()
            self._is_drag = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if hasattr(self, "_drag_start") and self._drag_start and e.buttons() == Qt.MouseButton.LeftButton:
            delta = e.globalPosition().toPoint() - self._drag_start
            if delta.manhattanLength() > 4:
                self._is_drag = True
                self.window().move(self._win_start + delta)
                main_w = self.window()
                if hasattr(main_w, "_is_minimized_top") and not main_w._is_minimized_top:
                    main_w._normal_geom = main_w.geometry()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if not getattr(self, "_is_drag", False):
                if getattr(self, "_mini_mode", False):
                    self.reactor_clicked.emit()
                elif self._big_mode and hasattr(self, "_press_pos"):
                    cx = self.width() / 2
                    cy = self.height() * 0.43
                    max_r = self.height() * 0.30
                    if math.hypot(self._press_pos.x() - cx, self._press_pos.y() - cy) <= max_r:
                        self.reactor_clicked.emit()
                elif hasattr(self, "_press_pos"):
                    BASE_W, BASE_H = 420.0, 540.0
                    scale = min(self.width() / BASE_W, self.height() / BASE_H)
                    off_x = (self.width() - BASE_W * scale) / 2.0
                    off_y = (self.height() - BASE_H * scale) / 2.0
                    local_x = (self._press_pos.x() - off_x) / scale
                    local_y = (self._press_pos.y() - off_y) / scale
                    cx, cy = BASE_W / 2, 185.0
                    if math.hypot(local_x - cx, local_y - cy) <= 135:
                        self.reactor_clicked.emit()
            self._drag_start = None
            self._is_drag = False
        super().mouseReleaseEvent(e)

    def _step(self):
        now = time.time()
        dt = min(now - self._step_t, 0.1)
        self._step_t = now

        # Visemes / Peak audio
        sched = self._visemes
        if sched is not None:
            frames, t0, hop = sched
            i = int((now - t0) / hop)
            if 0 <= i < len(frames):
                v_seq = frames[max(0, self._vis_i or i):i + 1]
                self._vis_i = i + 1
                if v_seq:
                    peak = max(f[0] for f in v_seq)
                    if peak > self._live_amp:
                        self._live_amp = peak
            elif i >= len(frames):
                self._visemes = None
                self._vis_i = None

        self._live_amp *= 0.88
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        amp = self._amp_disp

        st = (self.state or "STANDBY").upper()
        if self.muted:
            st = "MUTED"

        speed = {
            "THINKING": 220, "PROCESSING": 180,
            "LISTENING": 28, "SPEAKING": 45,
            "EXECUTING": 160, "INTERRUPTED": 90,
            "RECONNECTING": 12, "MUTED": 10,
        }.get(st, 14)
        self.spin = (self.spin + dt * speed) % 360.0

        if st == "SPEAKING":
            self._level_target = max(amp, 0.35 + 0.65 * abs(math.sin(now * 5.3) * math.sin(now * 2.1 + 1)))
        elif st == "LISTENING":
            # Live sound frequency indication when user talks to JARVIS
            self._level_target = max(amp * 1.5, self._live_amp * 1.5)
        elif st in ("THINKING", "PROCESSING"):
            self._level_target = 0.2 + 0.2 * abs(math.sin(now * 3.5))
        else:
            self._level_target = max(0.0, amp * 0.5)

        cur_lvl = getattr(self, "level", 0.0)
        cur_lvl += (self._level_target - cur_lvl) * min(1.0, dt * 14)
        self.level = cur_lvl

        lvl_sample = self.level * random.uniform(0.55, 1.0) if st == "LISTENING" else 0.0
        self.wave = self.wave[1:] + [lvl_sample]

        if self.reply_text:
            self.reply_shown = min(len(self.reply_text), self.reply_shown + dt * 45)

        if self.isVisible():
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        t = time.time()
        w, h = self.width(), self.height()
        st = (self.state or "STANDBY").upper()
        if self.muted:
            st = "MUTED"

        CYAN_ACC = qcol(C.PRI)
        DEEP_ACC = qcol(C.ACC)
        TEXT_COL = qcol(C.TEXT)
        DIM_COL  = qcol(C.TEXT_DIM)
        ALERT_COL = qcol(C.MUTED_C)

        if self.muted or st == "MUTED":
            accent = QColor(138, 138, 138)         # GRAY when mic is muted
            core_mid = QColor(190, 190, 190, 240)
            glow_col = QColor(138, 138, 138)
        elif self.speaking or st == "SPEAKING":
            accent = QColor(255, 215, 0)           # YELLOW when JARVIS is speaking
            core_mid = QColor(255, 245, 180, 240)
            glow_col = QColor(255, 215, 0)
        elif st in ("ERROR", "INTERRUPTED"):
            accent = ALERT_COL
            core_mid = QColor(255, 214, 236, 240)
            glow_col = ALERT_COL
        elif st in ("THINKING", "PROCESSING"):
            accent = DEEP_ACC
            core_mid = QColor(255, 214, 236, 240)
            glow_col = DEEP_ACC
        else:
            accent = CYAN_ACC
            core_mid = QColor(255, 214, 236, 240)
            glow_col = CYAN_ACC

        boot = 1.0

        if self._big_mode:
            self._paint_big(p, accent, boot, t)
            p.end()
            return

        if getattr(self, "_mini_mode", False):
            self._paint_mini_circle(p, accent, core_mid, glow_col, t)
            p.end()
            return

        BASE_W = 420.0
        BASE_H = 540.0
        scale = min(w / BASE_W, h / BASE_H)
        off_x = (w - BASE_W * scale) / 2.0
        off_y = (h - BASE_H * scale) / 2.0

        p.save()
        p.translate(off_x, off_y)
        p.scale(scale, scale)

        w_d, h_d = BASE_W, BASE_H
        cx, cy = w_d / 2, 185

        # ── Frame: cut-corner translucent dark glass
        c = 18
        r = QRectF(6, 6, w_d - 12, h_d - 12)
        panel = QPolygonF([
            QPointF(r.left() + c, r.top()), QPointF(r.right() - c, r.top()),
            QPointF(r.right(), r.top() + c), QPointF(r.right(), r.bottom() - c),
            QPointF(r.right() - c, r.bottom()), QPointF(r.left() + c, r.bottom()),
            QPointF(r.left(), r.bottom() - c), QPointF(r.left(), r.top() + c)
        ])
        p.setPen(QPen(rgba(accent, 55), 1))
        p.setBrush(QColor(14, 4, 12, 220))
        p.drawPolygon(panel)

        # Bright angular corner brackets
        p.setPen(QPen(rgba(accent, 210), 2))
        L = 24
        for (x, y, dx, dy) in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                               (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
            p.drawLine(QPointF(x + dx * c, y), QPointF(x + dx * (c + L), y))
            p.drawLine(QPointF(x, y + dy * c), QPointF(x, y + dy * (c + L)))
            p.drawLine(QPointF(x + dx * c, y), QPointF(x, y + dy * c))

        # Title
        title = ".".join(self._assistant_name.upper())
        p.setPen(rgba(TEXT_COL, 235))
        p.setFont(keena_font(11, True, 6))
        p.drawText(QRectF(0, 18, w_d, 22), Qt.AlignmentFlag.AlignCenter, title)
        p.setPen(rgba(accent, 140))
        p.drawLine(QPointF(cx - 65, 42), QPointF(cx + 65, 42))

        # Readouts
        p.setFont(keena_font(8, False, 1.5))
        left = [time.strftime("%I:%M:%S %p"), time.strftime("%a %d %b").upper()]
        right = []
        if self.cpu is not None:
            right += [f"CPU {self.cpu:3.0f}%", f"MEM {self.mem:3.0f}%"]
        gem_txt = "GEMINI LIVE" if self.gemini_online else "OFFLINE"
        right.append(gem_txt)

        for i, s in enumerate(left):
            p.setPen(rgba(TEXT_COL if i == 0 else DIM_COL, 220))
            p.drawText(QRectF(24, 54 + i * 14, 140, 14), Qt.AlignmentFlag.AlignLeft, s)

        for i, s in enumerate(right):
            hot = s.startswith("CPU") and self.cpu >= 90
            p.setPen(rgba(ALERT_COL if hot else DIM_COL, 220))
            p.drawText(QRectF(w_d - 144, 54 + i * 14, 120, 14), Qt.AlignmentFlag.AlignRight, s)

        # Leader lines
        p.setPen(QPen(rgba(accent, 75), 1))
        for side in (-1, 1):
            x0 = cx + side * 175
            p.drawLine(QPointF(x0, 88), QPointF(x0, 102))
            p.drawLine(QPointF(x0, 102), QPointF(cx + side * 108, 118))

        # ── Arc Reactor
        center = QPointF(cx, cy)
        lvl = getattr(self, "level", 0.0)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # Soft radial glow
        glow_r = (145 + 30 * lvl)
        g = QRadialGradient(center, max(glow_r, 1))
        g.setColorAt(0.0, rgba(glow_col, 95))
        g.setColorAt(0.45, rgba(glow_col, 30))
        g.setColorAt(1.0, rgba(glow_col, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(center, glow_r, glow_r)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # Outer ring with 4 notch markers
        R = 125
        p.setPen(QPen(rgba(accent, 115), 1.2))
        p.drawEllipse(center, R, R)
        p.setBrush(rgba(accent, 210))
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(4):
            a = math.radians(k * 90 + 45)
            tip = QPointF(cx + math.cos(a) * (R - 7), cy + math.sin(a) * (R - 7))
            side = math.radians(k * 90 + 45 + 90)
            base = QPointF(cx + math.cos(a) * (R + 2), cy + math.sin(a) * (R + 2))
            p.drawPolygon(QPolygonF([
                tip,
                base + QPointF(math.cos(side) * 5, math.sin(side) * 5),
                base - QPointF(math.cos(side) * 5, math.sin(side) * 5)
            ]))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # Radar sweep for thinking
        if st in ("THINKING", "PROCESSING"):
            sweep = QConicalGradient(center, -self.spin * 1.5)
            sweep.setColorAt(0.0, rgba(accent, 115))
            sweep.setColorAt(0.12, rgba(accent, 0))
            sweep.setColorAt(1.0, rgba(accent, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(sweep)
            p.drawEllipse(center, R - 2, R - 2)
            p.setBrush(Qt.BrushStyle.NoBrush)

        # Dial ticks (72 ticks, every 6th long)
        p.save()
        p.translate(center)
        p.rotate(-self.spin * 0.35)
        for k in range(72):
            long_tick = (k % 6 == 0)
            p.setPen(QPen(rgba(accent, 215 if long_tick else 90), 2 if long_tick else 1))
            r0 = 102 if long_tick else 106
            a = math.radians(k * 5)
            p.drawLine(QPointF(math.cos(a) * r0, math.sin(a) * r0),
                       QPointF(math.cos(a) * 112, math.sin(a) * 112))
        p.restore()

        # Counter-rotating segmented arcs
        for i, (rad, width, segs, span, direction) in enumerate(((92, 2.8, 3, 70, 1), (80, 1.5, 5, 38, -1))):
            rect = QRectF(cx - rad, cy - rad, 2 * rad, 2 * rad)
            p.setPen(QPen(rgba(accent, 230 - i * 70), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            start = self.spin * (1 + i * 0.6) * direction
            for s in range(segs):
                a0 = start + s * 360 / segs
                p.drawArc(rect, int(-a0 * 16), int(-span * 16))

        # Dynamic Voice ring
        base_r = 62
        if st == "LISTENING":
            # Dynamic sound frequency equalizer bars reacting directly to user speech
            bars = 48
            for k in range(bars):
                a = math.radians(k * 360 / bars + self.spin * 0.3)
                w_idx = int(k * len(self.wave) / bars) % len(self.wave)
                w_val = self.wave[w_idx]
                freq = 0.5 + 0.5 * math.sin(t * 11 + k * 1.2) * math.sin(t * 5.1 + k * 0.7)
                length = 3 + (26 * lvl + 20 * w_val) * freq
                alpha = int(140 + 115 * min(1.0, lvl * 2.0))
                p.setPen(QPen(rgba(accent, alpha), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
                p.drawLine(QPointF(cx + math.cos(a) * base_r, cy + math.sin(a) * base_r),
                           QPointF(cx + math.cos(a) * (base_r + length), cy + math.sin(a) * (base_r + length)))
            p.setPen(QPen(rgba(accent, 220), 1.5))
            p.drawEllipse(center, base_r - 2, base_r - 2)
        elif st == "SPEAKING":
            bars = 48
            for k in range(bars):
                a = math.radians(k * 360 / bars + self.spin * 0.4)
                wob = 0.5 + 0.5 * math.sin(t * 9 + k * 0.9) * math.sin(t * 4.3 + k * 0.37)
                length = 4 + 22 * lvl * wob
                p.setPen(QPen(rgba(accent, 220), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
                p.drawLine(QPointF(cx + math.cos(a) * base_r, cy + math.sin(a) * base_r),
                           QPointF(cx + math.cos(a) * (base_r + length), cy + math.sin(a) * (base_r + length)))
        elif st == "EXECUTING":
            rect = QRectF(cx - base_r, cy - base_r, 2 * base_r, 2 * base_r)
            p.setPen(QPen(rgba(accent, 240), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, int(-self.spin * 3 * 16), int(-90 * 16))
            p.setPen(QPen(rgba(accent, 120), 1.2))
            p.drawEllipse(center, base_r, base_r)
        else:
            p.setPen(QPen(rgba(accent, 130), 1.2))
            p.drawEllipse(center, base_r, base_r)

        # Arc-reactor coil (10 segments)
        p.save()
        p.translate(center)
        p.rotate(self.spin * 0.6)
        coil = QRectF(-46, -46, 92, 92)
        p.setPen(QPen(rgba(accent, 150 + 100 * lvl), 6.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        for k in range(10):
            p.drawArc(coil, int(k * 36 * 16), int(25 * 16))
        p.restore()
        p.setPen(QPen(rgba(accent, 160), 1))
        p.drawEllipse(center, 38, 38)

        # Glowing core: pure white center fading to state color
        core_r = (28 + 6 * lvl + 1.5 * math.sin(t * 2.2))
        cg = QRadialGradient(center, max(core_r, 1))
        cg.setColorAt(0.0, QColor(255, 255, 255, 255))
        cg.setColorAt(0.35, core_mid)
        cg.setColorAt(0.75, rgba(accent, 225))
        cg.setColorAt(1.0, rgba(accent, 40))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(cg)
        p.drawEllipse(center, core_r, core_r)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # Status label under reactor
        label = st
        p.setFont(keena_font(9, True, 4))
        p.setPen(rgba(accent, 235))
        p.drawText(QRectF(0, cy + 138, w_d, 18), Qt.AlignmentFlag.AlignCenter, label)
        dot_x = cx - p.fontMetrics().horizontalAdvance(label) / 2 - 14
        on = not (st in ("THINKING", "PROCESSING", "EXECUTING") and int(t * 3) % 2)
        p.setPen(QPen(rgba(accent, 230), 1.2))
        p.setBrush(rgba(accent, 230) if on else Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(dot_x, cy + 147), 3.5, 3.5)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # Action Cards
        y = cy + 160
        now = time.time()
        live = [(text, born) for text, born in self.cards if now - born <= 7]
        p.setFont(keena_font(8, True, 1.5))
        for text, born in live:
            age = now - born
            fade = max(0.0, min(1.0, 7 - age)) * min(1.0, age * 5)
            p.setOpacity(fade)
            label = f"[ ✓ {text.upper()[:28]} ]"
            p.setPen(rgba(accent, 230))
            p.drawText(QRectF(0, y, w_d, 16), Qt.AlignmentFlag.AlignCenter, label)
            y += 18
        p.setOpacity(1.0)

        p.restore()
        p.end()

    def _paint_big(self, p: QPainter, accent: QColor, boot: float, t: float):
        w, h = self.width(), self.height()
        TEXT_COL = qcol(C.TEXT)
        DIM_COL  = qcol(C.TEXT_DIM)

        p.fillRect(QRectF(0, 0, w, h), QColor(10, 3, 10, 245))
        p.setPen(QPen(rgba(accent, 12), 1))
        for x in range(0, w, 64):
            p.drawLine(QPointF(x, 0), QPointF(x, h))
        for y in range(0, h, 64):
            p.drawLine(QPointF(0, y), QPointF(w, y))

        rx, ry = w / 2, h * 0.43
        s = h * 0.30 / 125
        vg = QRadialGradient(QPointF(rx, ry), h * 0.6)
        vg.setColorAt(0.0, rgba(accent, 30))
        vg.setColorAt(1.0, rgba(accent, 0))
        p.fillRect(QRectF(0, 0, w, h), vg)

        m, L = 28, 70
        p.setPen(QPen(rgba(accent, 200 * boot), 2.5))
        for (x, y, dx, dy) in ((m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + dx * L, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy * L))

        title = ".".join(self._assistant_name.upper())
        p.setPen(rgba(TEXT_COL, 235))
        p.setFont(keena_font(26, True, 18))
        p.drawText(QRectF(0, h * 0.04, w, 44), Qt.AlignmentFlag.AlignCenter, title)
        p.setPen(rgba(DIM_COL, 200 * boot))
        p.setFont(keena_font(10, False, 5))
        p.drawText(QRectF(0, h * 0.04 + 46, w, 20), Qt.AlignmentFlag.AlignCenter, "YOUR PERSONAL AI COMPANION")

        p.setPen(rgba(TEXT_COL, 230 * boot))
        p.setFont(keena_font(32, True, 2))
        p.drawText(QRectF(m + 26, m + 18, 400, 48), Qt.AlignmentFlag.AlignLeft, time.strftime("%I:%M:%S %p"))
        p.setPen(rgba(DIM_COL, 220 * boot))
        p.setFont(keena_font(12, False, 4))
        p.drawText(QRectF(m + 28, m + 68, 400, 22), Qt.AlignmentFlag.AlignLeft, time.strftime("%A %d %B %Y").upper())

        rows = []
        if self.cpu is not None:
            rows += [("CPU", f"{self.cpu:.0f}%", self.cpu), ("MEMORY", f"{self.mem:.0f}%", self.mem)]
        rows.append(("STATUS", self.stat, 0))
        p.setFont(keena_font(12, True, 3))
        for i, (name, val, pct) in enumerate(rows):
            y_pos = m + 18 + i * 40
            p.setPen(rgba(qcol(C.MUTED_C) if pct >= 90 else DIM_COL, 220 * boot))
            p.drawText(QRectF(w - m - 330, y_pos, 300, 22), Qt.AlignmentFlag.AlignRight, f"{name}  {val}")

        p.setPen(rgba(DIM_COL, 150 * boot))
        p.setFont(keena_font(9, False, 3))
        p.drawText(QRectF(0, h - m - 22, w - m - 26, 18), Qt.AlignmentFlag.AlignRight, "ESC OR CLICK — RETURN TO DESKTOP HUD")

        p.save()
        p.translate(rx, ry)
        p.scale(s, s)
        p.translate(-w / 2, -185)

        # Draw scaled reactor
        center = QPointF(w / 2, 185)
        lvl = getattr(self, "level", 0.0)
        R = 125
        p.setPen(QPen(rgba(accent, 115), 1.2))
        p.drawEllipse(center, R, R)
        p.setBrush(rgba(accent, 210))
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(4):
            a = math.radians(k * 90 + 45)
            tip = QPointF(center.x() + math.cos(a) * (R - 7), center.y() + math.sin(a) * (R - 7))
            side = math.radians(k * 90 + 45 + 90)
            base = QPointF(center.x() + math.cos(a) * (R + 2), center.y() + math.sin(a) * (R + 2))
            p.drawPolygon(QPolygonF([
                tip,
                base + QPointF(math.cos(side) * 5, math.sin(side) * 5),
                base - QPointF(math.cos(side) * 5, math.sin(side) * 5)
            ]))
        p.setBrush(Qt.BrushStyle.NoBrush)

        p.save()
        p.translate(center)
        p.rotate(-self.spin * 0.35)
        for k in range(72):
            long_tick = (k % 6 == 0)
            p.setPen(QPen(rgba(accent, 215 if long_tick else 90), 2 if long_tick else 1))
            r0 = 102 if long_tick else 106
            a = math.radians(k * 5)
            p.drawLine(QPointF(math.cos(a) * r0, math.sin(a) * r0),
                       QPointF(math.cos(a) * 112, math.sin(a) * 112))
        p.restore()

        core_r = (28 + 6 * lvl + 1.5 * math.sin(t * 2.2))
        cg = QRadialGradient(center, max(core_r, 1))
        cg.setColorAt(0.0, QColor(255, 255, 255, 255))
        cg.setColorAt(0.35, QColor(255, 214, 236, 240))
        cg.setColorAt(0.75, rgba(accent, 225))
        cg.setColorAt(1.0, rgba(accent, 40))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(cg)
        p.drawEllipse(center, core_r, core_r)
        p.setBrush(Qt.BrushStyle.NoBrush)

        p.restore()

        # Big screen conversation preview
        tw = min(1100, int(w * 0.65))
        tx = (w - tw) // 2
        ty = int(ry + 140 * s + 10)
        if self.user_text:
            p.setPen(rgba(DIM_COL, 220))
            p.setFont(keena_font(13))
            p.drawText(QRectF(tx, ty, tw, 48), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.user_text[-160:])
            ty += 56
        if self.reply_text:
            p.setPen(TEXT_COL)
            p.setFont(keena_font(18, True))
            shown = self.reply_text[:int(self.reply_shown)]
            cursor = "█" if int(self.reply_shown) < len(self.reply_text) else ""
            p.drawText(QRectF(tx, ty, tw, max(60, int(h - ty - 60))), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, shown[-220:] + cursor)

    def _paint_mini_circle(self, p: QPainter, accent: QColor, core_mid: QColor, glow_col: QColor, t: float):
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0

        # Solid non-transparent deep black background around and inside the circle
        p.save()
        disc_r = min(w, h) / 2.0 - 1.0
        p.setPen(QPen(rgba(accent, 255), 2.0))
        p.setBrush(QColor(0, 0, 0, 255))
        p.drawEllipse(QPointF(cx, cy), disc_r, disc_r)
        p.restore()

        R_target = min(w, h) / 2.0 - 2.0
        scale = max(0.01, R_target / 125.0)

        p.save()
        p.translate(cx, cy)
        p.scale(scale, scale)

        center = QPointF(0, 0)
        lvl = getattr(self, "level", 0.0)

        # Inner solid black disc to protect animation contrast
        p.save()
        p.setPen(QPen(rgba(accent, 220), 2.5))
        p.setBrush(QColor(0, 0, 0, 255))
        p.drawEllipse(center, 126, 126)
        p.restore()

        # Soft radial glow (surges with sound)
        glow_r = 135 + 75 * lvl
        g = QRadialGradient(center, max(glow_r, 1))
        g.setColorAt(0.0, rgba(glow_col, 130 + int(35 * lvl)))
        g.setColorAt(0.5, rgba(glow_col, 35 + int(25 * lvl)))
        g.setColorAt(1.0, rgba(glow_col, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(center, glow_r, glow_r)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # Outer ring
        R = 125
        p.setPen(QPen(rgba(accent, 220), 2.5))
        p.drawEllipse(center, R, R)

        # 4 Notch markers
        p.setBrush(rgba(accent, 240))
        for k in range(4):
            a = math.radians(k * 90 + 45)
            p.drawPolygon(QPolygonF([
                QPointF(math.cos(a) * (R - 5), math.sin(a) * (R - 5)),
                QPointF(math.cos(a + 0.08) * (R + 6), math.sin(a + 0.08) * (R + 6)),
                QPointF(math.cos(a - 0.08) * (R + 6), math.sin(a - 0.08) * (R + 6)),
            ]))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # Dial ticks (pulses inward with sound)
        p.save()
        p.rotate(-self.spin * 0.35)
        for k in range(36):
            long_tick = (k % 6 == 0)
            p.setPen(QPen(rgba(accent, 220 if long_tick else 100), 2.5 if long_tick else 1.5))
            r0 = (96 - 12 * lvl) if long_tick else (104 - 6 * lvl)
            a = math.radians(k * 10)
            p.drawLine(QPointF(math.cos(a) * r0, math.sin(a) * r0),
                       QPointF(math.cos(a) * 114, math.sin(a) * 114))
        p.restore()

        # Counter-rotating segmented arcs
        for i, (rad, width, segs, span, direction) in enumerate(((92, 3.5, 3, 70, 1), (80, 2.5, 5, 38, -1))):
            rect = QRectF(-rad, -rad, 2 * rad, 2 * rad)
            p.setPen(QPen(rgba(accent, 240 - i * 60), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            start = self.spin * (1 + i * 0.6) * direction
            for s in range(segs):
                a0 = start + s * 360 / segs
                p.drawArc(rect, int(-a0 * 16), int(-span * 16))

        # Dynamic Voice ring / speech bars (surges with listening and speaking sound)
        st = (self.state or "STANDBY").upper()
        if self.muted:
            st = "MUTED"
        base_r = 62
        if st == "SPEAKING" or self.speaking:
            bars = 24
            for k in range(bars):
                a = math.radians(k * 360 / bars + self.spin * 0.4)
                wob = 0.5 + 0.5 * math.sin(t * 9 + k * 0.9)
                length = 5 + 46 * lvl * wob
                p.setPen(QPen(rgba(accent, 220 + int(35 * lvl)), 2.8 + 1.2 * lvl, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
                p.drawLine(QPointF(math.cos(a) * base_r, math.sin(a) * base_r),
                           QPointF(math.cos(a) * (base_r + length), math.sin(a) * (base_r + length)))
        elif st == "LISTENING":
            path = QPainterPath()
            n = len(self.wave)
            for k in range(n + 1):
                v = sum(self.wave[(k + d) % n] for d in (-2, -1, 0, 1, 2)) / 5
                a = math.radians(k * 360 / n + self.spin)
                r_wave = base_r + 2 + (40 * lvl + 22) * v
                pt = QPointF(math.cos(a) * r_wave, math.sin(a) * r_wave)
                path.moveTo(pt) if k == 0 else path.lineTo(pt)
            p.setPen(QPen(rgba(accent, 210 + int(45 * lvl)), 2.2 + 1.2 * lvl))
            p.drawPath(path)
        else:
            p.setPen(QPen(rgba(accent, 140), 1.5))
            p.drawEllipse(center, base_r, base_r)

        # Arc-reactor coil (10 segments)
        p.save()
        p.rotate(self.spin * 0.6)
        coil = QRectF(-46, -46, 92, 92)
        p.setPen(QPen(rgba(accent, 150 + int(105 * lvl)), 8.0 + 3.5 * lvl, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        for k in range(10):
            p.drawArc(coil, int(k * 36 * 16), int(25 * 16))
        p.restore()
        p.setPen(QPen(rgba(accent, 180), 1.8))
        p.drawEllipse(center, 38, 38)

        # Glowing core (expands with voice sound)
        core_r = (26 + 26 * lvl + 2.0 * math.sin(t * 3.0))
        cg = QRadialGradient(center, max(core_r, 1))
        cg.setColorAt(0.0, QColor(255, 255, 255, 255))
        cg.setColorAt(0.35, core_mid)
        cg.setColorAt(0.75, rgba(accent, 235))
        cg.setColorAt(1.0, rgba(accent, 40))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(cg)
        p.drawEllipse(center, core_r, core_r)
        p.setBrush(Qt.BrushStyle.NoBrush)

        p.restore()


class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, pct))
        if v == self._value and text == self._text:
            return          # unchanged — skip the repaint
        self._value = v
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        p.end()

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Cap scrollback so an hours-long session can't grow the document
        # without bound — keeps memory flat and every insert cheap. Oldest
        # lines drop off the top automatically.
        self.document().setMaximumBlockCount(600)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "jarvis"   # updated when assistant name changes
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                              self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif "err" in tl:                                        self._tag = "err"
        else:                                                    self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                # SYS lines are the bulk of the log. Amber fought the cyan HUD
                # and, being a fixed status colour rather than a hue-linked one,
                # stayed amber even after the accent picker retinted everything
                # else. TEXT_MED follows the theme and drops the contrast to a
                # level you can read past.
                "sys":  qcol(C.TEXT_MED),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
    "folder":  ("📁", "#ffcc00"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    if path.is_dir():
        return "folder"
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")


# Folders nobody means when they say "this project": dependencies, build output,
# version control. The same list dev_agent skips, so the count on the card is
# the count of files the coding model will actually look at.
_SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env",
              "__pycache__", ".mypy_cache", ".pytest_cache", "dist", "build",
              ".idea", ".vscode", ".next", "target", ".jarvis_backup"}


def _count_files(folder: Path, cap: int = 5000) -> int:
    """Files in a project folder, stopping at `cap` — a whole drive dropped by
    mistake must not freeze the window while it is counted."""
    import os
    n = 0
    for _root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        n += len(files)
        if n >= cap:
            return cap
    return n

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._folder_files = 0
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        # The marching-ants dashed border is only meaningful while the user is
        # hovering or dragging a file over the zone. When idle, skip the repaint
        # entirely instead of redrawing the whole zone 25×/s forever — that idle
        # repaint held the GIL and stole time from the audio/response threads.
        if not (self._hovering or self._drag_over):
            return
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file() or Path(path).is_dir():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        # Qt has no single dialog that picks "a file or a folder", so the click
        # asks which — a folder is what the coding model works on.
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {C.DARK}; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER_B}; font-family: 'Courier New'; }}"
            f"QMenu::item {{ padding: 6px 18px; }}"
            f"QMenu::item:selected {{ background: {C.PRI_GHO}; }}")
        pick_file = menu.addAction("📄  Choose a file…")
        pick_dir = menu.addAction("📁  Choose a folder…  (for coding)")
        chosen = menu.exec(QCursor.pos())
        if chosen is pick_dir:
            path = QFileDialog.getExistingDirectory(
                self, "Select a project folder for JARVIS", str(Path.home()))
            if path:
                self._set_file(path)
            return
        if chosen is not pick_file:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._folder_files = _count_files(Path(path)) if Path(path).is_dir() else 0
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

        p.end()

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop a file or folder  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Project folders")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        try:
            size_str = _fmt_size(path.stat().st_size)
        except OSError:                       # moved or deleted since loading
            size_str = "gone"
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"
        if cat == "folder":
            n = self._z._folder_files
            ext_str, size_str = "FOLDER", f"{n}{'+' if n >= 5000 else ''} files"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont("Courier New", 8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Circular colour picker. The user drags the handle (small white circle)
    around the wheel to choose from ALL hues. The filled circle in the centre
    is a live preview of the selected colour.
    """

    hue_picked    = pyqtSignal(str)   # while dragging (live)
    hue_committed = pyqtSignal(str)   # when the handle is released

    _RING = 16   # ring thickness (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometry helpers ─────────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # screen y goes down — flip to math axis
        ang = math.atan2(dy, dx)      # [-π, π], counter-clockwise
        return (ang / (2 * math.pi)) % 1.0

    # ── drawing ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # centre preview circle
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # draggable handle
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating overlay — change assistant name, user name, UI colour and voice."""

    saved = pyqtSignal(str, str, str, str)   # assistant_name, user_name, ui_color, voice
    _OW, _OH = 400, 588

    def __init__(self, assistant_name="JARVIS", user_name="",
                 ui_color=DEFAULT_UI_COLOR, voice="", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        lay.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_lbl("ASSISTANT NAME", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(QFont("Courier New", 10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("YOUR NAME  (leave blank for default sir / efendim)", 8,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(QFont("Courier New", 10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── Assistant voice — Gemini prebuilt voices ─────────────────────────
        # Names are language-neutral proper nouns, so the row reads the same in
        # every locale. Selecting one and applying rebuilds the Live session.
        from memory.config_manager import AVAILABLE_VOICES, DEFAULT_VOICE
        lay.addSpacing(4)
        lay.addWidget(_lbl("ASSISTANT VOICE", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._sel_voice   = (voice or DEFAULT_VOICE)
        if self._sel_voice not in AVAILABLE_VOICES:
            self._sel_voice = DEFAULT_VOICE
        self._voice_btns: dict[str, QPushButton] = {}
        voice_row = QHBoxLayout(); voice_row.setSpacing(4)
        for _v in AVAILABLE_VOICES:
            b = QPushButton(_v)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, name=_v: self._on_voice_pick(name))
            self._voice_btns[_v] = b
            voice_row.addWidget(b)
        lay.addLayout(voice_row)
        self._refresh_voice_btns()

        # ── UI colour — colour wheel ─────────────────────────────────────────
        lay.addSpacing(4)
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("UI COLOUR  —  drag the handle", 8,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = QPushButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None   # callable(hex) — live preview; MainWindow wires it

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#00d4ff   (custom hex colour)")
        self._hex_input.setFont(QFont("Courier New", 10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        lay.addSpacing(6)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        save_btn = QPushButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(QFont("Courier New", 9))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    # ── voice selection ──────────────────────────────────────────────────────
    def _on_voice_pick(self, name: str):
        self._sel_voice = name
        self._refresh_voice_btns()

    def _refresh_voice_btns(self):
        """Highlight the selected voice pill; dim the rest."""
        for name, b in self._voice_btns.items():
            on = (name == self._sel_voice)
            b.setChecked(on)
            if on:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI}; border-radius: 3px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
                """)

    # ── colour flow ──────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Updates the selected colour; hex box + wheel stay in sync, theme is live-previewed."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # While dragging: update the hex box, don't apply the theme yet
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Handle released → live-preview the whole interface
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        # If a preview was applied, revert to the colour from launch
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or "JARVIS"
        user = self._user_input.text().strip()
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR, self._sel_voice)
        self.hide()


class PluginManagerOverlay(QWidget):
    """Floating overlay — lists discovered plugins with per-plugin ON/OFF toggles."""

    _OW = 420

    def __init__(self, plugins: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginManagerOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🧩  PLUGIN MANAGER")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        if not plugins:
            empty = QLabel("No plugins found in /plugins.")
            empty.setFont(QFont("Courier New", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(empty)

        for p in plugins:
            lay.addLayout(self._build_row(p))

        lay.addSpacing(4)
        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)
        self.adjustSize()

    def _build_row(self, p: dict) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)

        label_text = p["name"] if p["valid"] else f"{p['name']}  (⚠ {p['file']})"
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(f"color: {C.TEXT if p['valid'] else C.TEXT_DIM}; background: transparent;")
        lbl.setToolTip(p["description"] if p["valid"] else p["error"])
        lbl.setWordWrap(False)
        row.addWidget(lbl, stretch=1)

        btn = QPushButton()
        btn.setFixedSize(72, 24)
        btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        if not p["valid"]:
            btn.setText("BROKEN")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
            """)
        else:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(btn, p["enabled"])
            btn.clicked.connect(lambda _, name=p["name"], b=btn: self._toggle(name, b))
        row.addWidget(btn)
        return row

    def _style_toggle(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setText("ON")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            btn.setText("OFF")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            """)

    def _toggle(self, name: str, btn: QPushButton):
        from memory.config_manager import get_plugin_enabled, save_plugin_enabled
        new_val = not get_plugin_enabled(name)
        save_plugin_enabled(name, new_val)
        self._style_toggle(btn, new_val)


class _HudOverlay(QWidget):
    """Base for the floating panels placed by hand over the HUD.

    They are children of the central widget but sit in no layout, so Qt never
    invalidates the region they occupy when they hide or shrink: the HUD keeps
    painting around them and their last frame stays on screen as a ghost. Any
    overlay positioned with _centre_overlay needs this."""

    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            # Repaint exactly what we were covering, before we stop covering it.
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class ConfirmBanner(_HudOverlay):
    """The gate in front of an action that cannot be taken back.

    The old confirmation was a tool parameter the model filled in itself, which
    means it confirmed its own shutdown requests. This is the interface asking,
    and the answer travels from a human finger to core/confirm.py without the
    model in the loop. Nothing blocks while it is up: the assistant keeps
    talking, so this costs no latency — unlike the old gate, which spent two
    tool round trips on every power command."""

    answered = pyqtSignal(bool)
    _OW = 430

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: rgba(14, 3, 0, 250);
                border: 1px solid {C.ACC};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(QFont("Courier New", 8))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        row = QHBoxLayout(); row.setSpacing(8)

        yes = QPushButton("▸  CONFIRM")
        yes.setFixedHeight(32)
        yes.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(True))
        row.addWidget(yes)

        no = QPushButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(QFont("Courier New", 9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading, the
        # safe answer wins.
        no.setDefault(True)
        no.setFocus()


class AudioDeviceOverlay(_HudOverlay):
    """Choose which microphone JARVIS listens to and which speakers it uses.

    Both audio streams used to open with no `device=` at all, so they always
    took the OS default — which on Windows moves by itself the moment a headset
    is plugged in. 'JARVIS can't hear me' is usually 'JARVIS is listening to the
    webcam'."""

    picked = pyqtSignal()      # emitted after Apply, when something changed

    # THESE EXIST BECAUSE QTimer.singleShot FROM A WORKER THREAD NEVER FIRES.
    #
    # The first version posted results back with `QTimer.singleShot(0, fn)` from
    # inside the audio thread. A QTimer belongs to the thread that creates it,
    # and that thread has no event loop — so the timer was created, never ran,
    # and the panel sat on "Speak now…" for ever while the recording finished
    # silently behind it. Reported as "I saw no sign of anything at all", which
    # is precisely what it looked like.
    #
    # A signal is the thread-safe way across: Qt queues it onto the receiving
    # object's thread, which for a widget is the GUI thread. Both carry only
    # plain values — never a widget — because the widgets belong to the GUI
    # thread and the worker must not touch them even to pass them along.
    _level_seen = pyqtSignal(int)          # 0-100, while recording
    _test_result = pyqtSignal(str, bool)   # sentence, was it good

    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.audio_devices import list_devices, DEFAULT_LABEL
        from memory.config_manager import get_input_device, get_output_device

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AudioDeviceOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🎧  AUDIO DEVICES")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _combo_css = (
            f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        _test_css = (
            f"QPushButton {{ background: transparent; color: {C.TEXT_DIM}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; }}"
            f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}"
            f"QPushButton:disabled {{ color: {C.BORDER}; }}"
        )

        def _row(label: str, kind: str, current: str) -> QComboBox:
            cap = QLabel(label)
            cap.setFont(QFont("Courier New", 8))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(cap)

            box = QComboBox()
            box.setFont(QFont("Courier New", 9))
            box.setFixedHeight(30)
            box.setStyleSheet(_combo_css)
            # The list is served from a cache warmed on a background thread at
            # startup, so opening this panel never blocks the Qt thread on the
            # host audio API.
            box.addItem(DEFAULT_LABEL, "")
            # Re-scan device list so hotplugged Bluetooth devices appear immediately
            for name in list_devices(kind, refresh=True):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:
                # Saved device is not plugged in right now. Show it rather than
                # silently resetting the user's choice to default.
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)

            # THE ONE CHECK THE STARTUP PROBE CANNOT MAKE.
            #
            # core/audio_devices decides at launch whether a device opens and
            # whether it moves audio, and it does that with silence. A muted
            # microphone, a level slider at zero and a privacy shutter all pass
            # every one of those tests and still deliver a flat line — the only
            # thing that separates them from a working microphone is a person
            # speaking into it.
            #
            # So the button is here, beside the dropdown, in the panel somebody
            # opens when JARVIS cannot hear them. It replaces a loose
            # `ses_testi.py` in the project root that nobody would ever find.
            btn_row = QHBoxLayout(); btn_row.setSpacing(6)
            btn_row.addWidget(box, 1)
            test = QPushButton("TEST")
            test.setFixedSize(64, 30)
            test.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            test.setCursor(Qt.CursorShape.PointingHandCursor)
            test.setStyleSheet(_test_css)
            btn_row.addWidget(test)
            lay.addLayout(btn_row)

            # A meter, because a static label is indistinguishable from a hang.
            # Three seconds of "Speak now" with nothing moving was reported as
            # "I saw no sign of anything" — correctly, since nothing on screen
            # responded to the user's voice.
            meter = QProgressBar()
            meter.setRange(0, 100)
            meter.setValue(0)
            meter.setTextVisible(False)
            meter.setFixedHeight(6)
            meter.setStyleSheet(
                f"QProgressBar {{ background: #000d12; border: 1px solid {C.BORDER}; "
                f"border-radius: 3px; }}"
                f"QProgressBar::chunk {{ background: {C.PRI}; border-radius: 2px; }}")
            meter.hide()
            lay.addWidget(meter)

            result = QLabel("")
            result.setWordWrap(True)
            result.setFont(QFont("Courier New", 7))
            result.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(result)

            test.clicked.connect(
                lambda _=False, k=kind, b=box, t=test, r=result, m=meter:
                    self._run_test(k, b, t, r, m))
            return box

        # Which row is being tested right now. One at a time — the button is
        # disabled for the duration — so a single slot is enough, and it keeps
        # widgets off the signal payload.
        self._active = None                # (button, label, meter)
        self._level_seen.connect(self._on_level)
        self._test_result.connect(self._on_result)

        self._in_box  = _row("MICROPHONE — what JARVIS hears you with",
                             "input", get_input_device())
        lay.addSpacing(4)
        self._out_box = _row("SPEAKERS — what JARVIS talks through",
                             "output", get_output_device())

        # ── Bluetooth Audio Profile & Quality Advisory ───────────────────────
        self._profile_lbl = QLabel("")
        self._profile_lbl.setWordWrap(True)
        self._profile_lbl.setFont(QFont("Courier New", 7))
        lay.addSpacing(2)
        lay.addWidget(self._profile_lbl)
        self._in_box.currentIndexChanged.connect(lambda _i: self._update_profile_info())
        self._out_box.currentIndexChanged.connect(lambda _i: self._update_profile_info())
        self._update_profile_info()

        # ── Output volume, beside the output device ──────────────────────────
        #
        # Choosing "Realtek(R) Audio" here and finding it far too quiet used to
        # mean leaving this panel, opening the system sound settings, finding
        # the same endpoint again and moving a slider there. The volume belongs
        # next to the device it applies to.
        _vol_now = None
        try:
            from core.audio_devices import get_output_volume
            # The SELECTED output, not the system default. Somebody puts the
            # assistant on their speakers and keeps everything else in their
            # headphones; a slider labelled with one device that moves the
            # other is worse than no slider at all.
            _vol_now = get_output_volume(self._out_box.currentData() or "")
        except Exception:
            pass

        if _vol_now is not None:
            vcap = QLabel("OUTPUT VOLUME")
            vcap.setFont(QFont("Courier New", 8))
            vcap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addSpacing(4)
            lay.addWidget(vcap)

            vrow = QHBoxLayout(); vrow.setSpacing(8)
            self._vol = QSlider(Qt.Orientation.Horizontal)
            self._vol.setRange(0, 100)
            self._vol.setValue(int(_vol_now))
            self._vol.setFixedHeight(22)
            self._vol.setStyleSheet(
                f"QSlider::groove:horizontal {{ background: #000d12; height: 4px; "
                f"border: 1px solid {C.BORDER}; border-radius: 2px; }}"
                f"QSlider::sub-page:horizontal {{ background: {C.PRI_DIM}; "
                f"border-radius: 2px; }}"
                f"QSlider::handle:horizontal {{ background: {C.PRI}; width: 10px; "
                f"margin: -5px 0; border-radius: 5px; }}")
            self._vol_lbl = QLabel(f"{int(_vol_now)}%")
            self._vol_lbl.setFont(QFont("Courier New", 8))
            self._vol_lbl.setFixedWidth(40)
            self._vol_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            vrow.addWidget(self._vol, 1)
            vrow.addWidget(self._vol_lbl)
            lay.addLayout(vrow)

            # valueChanged fires for every pixel of a drag, and each one is a
            # COM call into the audio endpoint. sliderReleased fires once, when
            # the user has decided — the label still follows the handle so the
            # number moves while they drag.
            self._vol.valueChanged.connect(
                lambda v: self._vol_lbl.setText(f"{v}%"))
            self._vol.sliderReleased.connect(self._apply_volume)
            self._out_box.currentIndexChanged.connect(
                lambda _i: self._volume_follows_device())

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(QFont("Courier New", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont("Courier New", 9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply_volume(self):
        """Set the system output volume from the slider, off the Qt thread.

        The Windows path goes through COM, which can block for tens of
        milliseconds; that is not long enough to see but is long enough to
        stutter a drag, and there is no reason to make the interface wait for
        it.
        """
        import threading
        value = self._vol.value()
        device = self._out_box.currentData() or ""

        def _work():
            from core import audio_devices
            if not audio_devices.set_output_volume(value, device):
                print(f"[Audio] the volume of '{device}' could not be set "
                      f"to {value}%")

        threading.Thread(target=_work, daemon=True, name="audio-volume").start()

    def _volume_follows_device(self):
        """Re-read the slider when the chosen output changes.

        Each device keeps its own level, so leaving the handle where it was
        would show the previous device's volume against the new device's name —
        and the next drag would then jump it there.
        """
        if not hasattr(self, "_vol"):
            return
        try:
            from core.audio_devices import get_output_volume
            now = get_output_volume(self._out_box.currentData() or "")
        except Exception:
            now = None
        if now is None:
            return
        self._vol.blockSignals(True)
        self._vol.setValue(int(now))
        self._vol.blockSignals(False)
        self._vol_lbl.setText(f"{int(now)}%")

    def _run_test(self, kind, box, button, label, meter=None):
        """Record or play for real, off the Qt thread.

        Both calls block for seconds — recording waits for the user to speak,
        the tone waits for itself — and doing that on the Qt thread freezes the
        HUD, which is the mistake Mark LV already paid for once with a 2.1
        second wake-word import in the settings drawer.

        Results come back by signal, not by timer. See the note on
        `_level_seen` above for why that distinction is the whole feature.
        """
        import threading

        name = box.currentData() or ""
        self._active = (button, label, meter)
        button.setEnabled(False)
        label.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        label.setText("Speak now…" if kind == "input" else "Playing a tone…")
        if meter is not None and kind == "input":
            meter.setValue(0)
            meter.show()

        def _work():
            from core import audio_devices
            try:
                if kind == "input":
                    res = audio_devices.measure_input(
                        name,
                        on_level=lambda f: self._level_seen.emit(int(f * 100)))
                    text = f"peak {res['peak']}  —  {res['verdict']}"
                else:
                    res = audio_devices.play_tone(name)
                    text = res["verdict"]
                good = bool(res.get("ok", False))
            except Exception as e:
                text, good = f"The test itself failed: {e}", False
            self._test_result.emit(text, good)

        threading.Thread(target=_work, daemon=True, name=f"audio-test-{kind}").start()

    def _on_level(self, value: int):
        """A live level, already on the GUI thread thanks to the signal."""
        if self._active and self._active[2] is not None:
            self._active[2].setValue(max(0, min(100, value)))

    def _on_result(self, text: str, good: bool):
        if not self._active:
            return
        button, label, meter = self._active
        self._active = None
        button.setEnabled(True)
        if meter is not None:
            meter.setValue(0)
            meter.hide()
        label.setStyleSheet(
            f"color: {C.PRI if good else C.TEXT_DIM}; background: transparent;")
        label.setText(text)

    def _update_profile_info(self):
        in_name = self._in_box.currentData() or ""
        out_name = self._out_box.currentData() or ""
        try:
            from core.audio_devices import is_bluetooth_device
            in_bt = is_bluetooth_device(in_name)
            out_bt = is_bluetooth_device(out_name)
        except Exception:
            in_bt = False
            out_bt = False

        if in_bt:
            self._profile_lbl.setStyleSheet("color: #ffaa33; background: #201500; border: 1px solid #664400; border-radius: 3px; padding: 4px;")
            self._profile_lbl.setText(
                "⚠️ Bluetooth mic selected: Windows switches earphones to Hands-Free (HFP) mode. "
                "Playback quality is reduced and background media (Spotify) may sound muffled. "
                "Tip: Select Laptop Microphone for full A2DP stereo music quality."
            )
            self._profile_lbl.show()
        elif out_bt:
            self._profile_lbl.setStyleSheet(f"color: {C.PRI}; background: #001a18; border: 1px solid {C.PRI_DIM}; border-radius: 3px; padding: 4px;")
            self._profile_lbl.setText(
                "✓ High-fidelity A2DP stereo playback active on Bluetooth earphones. "
                "Laptop microphone selected: Spotify and media audio play with zero muffling."
            )
            self._profile_lbl.show()
        else:
            self._profile_lbl.hide()

    def _apply(self):
        from memory.config_manager import (
            save_input_device, save_output_device,
        )
        new_in  = self._in_box.currentData()  or ""
        new_out = self._out_box.currentData() or ""
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        # Always emit picked so the session reconnects and streams re-bind directly to hardware
        self.picked.emit()


class MemoryOverlay(_HudOverlay):
    """Everything JARVIS has stored about you, and when it learned it.

    Memory used to be a 2200-character store that deleted its oldest entries
    when full and mentioned it only on stdout. The cap is gone; this panel is
    the other half of that change — a memory you cannot inspect is a memory you
    cannot trust, and 'delete' has to be something the person can do."""

    _OW = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryOverlay {{
                background: rgba(0, 6, 10, 246);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(5)
        self._rebuild()

    def _clear_layout(self):
        """Take every item out of the layout and detach it from the widget tree
        in this call.

        deleteLater() on its own is not enough: it queues destruction for the
        next event-loop pass, and until then the old rows are still children of
        this widget and still paint — which is what drew half of the previous
        panel over the new one. setParent(None) removes them from the tree now;
        deleteLater() then frees them safely."""
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # hide() stops it painting in this frame; deleteLater() frees it
                # safely afterwards. setParent(None) would also stop the paint,
                # but it turns the widget into a top-level window for the moment
                # between the two calls, which is not something to leave lying
                # around inside a click handler.
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

    def _settle(self, before):
        """Size the panel to its content, re-centre it, and repaint what the old
        size covered.

        The re-size has to happen here rather than at the end of _rebuild
        because Qt has not polished the freshly-created children at that point,
        so the size hint it would read is the empty-layout one. Measured: a
        first adjustSize() returned 32 px for a panel whose content needed 155,
        and a second call — after the same widgets had been through the event
        loop — returned 155. So this runs twice: once now, once on the next
        turn, from _rebuild.

        The re-centre and the repaint are needed because the overlay is placed
        by hand and is in no layout: shrinking it leaves it off-centre and
        leaves its former pixels on screen, since nothing tells the parent that
        region changed. The repaint has to cover the union of the old and new
        rectangles."""
        self._lay.invalidate()
        self._lay.activate()
        self.updateGeometry()
        self.adjustSize()

        p = self.parentWidget()
        if p is None:
            self.update()
            return
        self.move(max(0, (p.width()  - self.width())  // 2),
                  max(0, (p.height() - self.height()) // 2))
        p.update(before.united(self.geometry()))
        self.update()

    def _rebuild(self):
        before = self.geometry()
        self._clear_layout()

        from memory.memory_manager import all_entries_for_ui

        hdr = QLabel("🧠  WHAT JARVIS REMEMBERS")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        self._lay.addWidget(sep)

        rows = all_entries_for_ui()

        cap = QLabel(f"{len(rows)} stored facts — newest first. "
                     f"Nothing here is sent anywhere; it lives in "
                     f"memory/long_term.json on this machine.")
        cap.setWordWrap(True)
        cap.setFont(QFont("Courier New", 7))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setFont(QFont("Courier New", 9))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(420, 34 * len(rows) + 10))
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 3px; "
                f"background: transparent; }}"
            )
            inner = QWidget()
            ilay  = QVBoxLayout(inner)
            ilay.setContentsMargins(6, 6, 6, 6)
            ilay.setSpacing(3)

            for r in rows:
                line = QHBoxLayout(); line.setSpacing(6)
                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b> "
                             f"<span style='color:{C.TEXT_MED}'>— {r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(QFont("Courier New", 8))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(f"{r['category'][:4]} · {r['updated'] or '—'}")
                meta.setFont(QFont("Courier New", 7))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                rm = QPushButton("✕")
                rm.setFixedSize(20, 20)
                rm.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this")
                rm.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; }}
                """)
                rm.clicked.connect(
                    lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
                line.addWidget(rm)

                holder = QWidget()
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(QFont("Courier New", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        self._lay.addWidget(close)

        self._settle(before)
        # …and again once Qt has polished the new children, because the size
        # hint is not final until then. Harmless when the first pass already
        # got it right: _settle is idempotent.
        QTimer.singleShot(0, lambda g=before: self._settle(g))

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget
        forget(key, category)
        # Rebuild on the NEXT event-loop turn, not inside this click handler.
        # The rebuild destroys the very ✕ button that emitted this signal, and
        # Qt is entitled to touch the sender after a slot returns; tearing it
        # down mid-emission is how a widget ends up half-alive on screen.
        QTimer.singleShot(0, self._rebuild)


class _ScrollSafeCombo(QComboBox):
    """A dropdown that does not change when you scroll past it.

    Qt's default is that a combo box under the pointer swallows the wheel and
    steps its own value with it. Inside a scrolling panel that is not a
    convenience, it is a trap: scrolling down the list of settings silently
    changes whichever ones the pointer crossed on the way, and nothing on screen
    says so. It was reported as "selections move around when I scroll", and it
    is the same bug every Qt settings panel has until somebody fixes it.

    The wheel is only accepted when the box has been clicked into. Otherwise the
    event is passed up, which is what makes the panel scroll instead.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        # StrongFocus so it can still be tabbed to and used from the keyboard;
        # the wheel is gated on having focus, not on being focusable.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class ProvidersOverlay(_HudOverlay):
    """Choose what runs the assistant: Gemini Live, or your own stack.

    WHAT THE FIRST VERSION GOT WRONG, AND WHY IT IS WORTH WRITING DOWN
        It listed the PROVIDERS, and a provider here covers thirteen services at
        once because they all speak the same protocol. That is the right shape
        for the code — it is why `llm_openai.py` is one file instead of thirteen
        — and it is the wrong shape for a person.

        Watched in use: somebody who wanted to run a model on their own machine
        opened the dropdown, saw a single entry reading "OpenAI-compatible", and
        concluded the feature was not there. They were right to. Nobody looking
        for Ollama searches for the name of a protocol, and "OpenAI" is the last
        word you expect to find under "run it locally".

        So the panel now lists what people are actually looking for — Ollama, LM
        Studio, Groq, DeepSeek — from each provider's own `catalog`. One file
        behind the scenes, thirteen recognisable names in front of them.

    THE OTHER TWO CHANGES
        It was 560 pixels of stacked dropdowns with no grouping, which reads as
        a form to be endured rather than three decisions to be made. It is 760
        wide now, and the three parts of a conversation are three numbered boxes
        that say what they do in plain words.

        And every field a provider declares was shown at once — download folders,
        stability weights, thread counts. A setting almost nobody changes costs
        more in the confusion it adds to the five that matter than it saves for
        the one person who wants it, so `advanced` fields sit behind MORE.

    WHAT IT COSTS WHEN NOBODY OPENS IT
        Nothing. The button is built with the rest of the SETUP drawer, but the
        `providers` package is imported inside `__init__` of THIS class — on the
        click. Same lesson as the wake-word check that spent a measured 2.104
        seconds importing `openwakeword` on the Qt thread and made the drawer
        look broken.
    """

    changed = pyqtSignal()          # emitted on APPLY when the engine moved
    # Emitted from a worker thread when something the machine had to be asked
    # for — the installed voices — has arrived. A signal rather than a direct
    # call because the worker must not touch a widget.
    _live_ready = pyqtSignal()
    # (provider id, [(value, label), ...]) from a worker that went and asked an
    # endpoint what it offers.
    _models_ready = pyqtSignal(str, list)
    _OW = 760                       # the window's own minimum is 820

    # Which provider fills each slot, and what the slot is FOR — in the words
    # somebody configuring it would use, not the words the code uses.
    _SLOTS = (
        ("stt", "①", "HEARING",  "turns what you say into words"),
        ("llm", "②", "THINKING", "understands, decides, uses your tools"),
        ("tts", "③", "SPEAKING", "reads the answer out loud"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        # Imported HERE, on the click — never at startup. See the class docstring.
        from memory.config_manager import (get_coding_provider, get_pipeline,
                                           get_provider_config,
                                           get_provider_mode, get_sidecar_first,
                                           get_sidecar_provider)
        from providers import registry

        self._registry = registry
        self._get_cfg = get_provider_config
        # What the running engine was built from, before any edit here — see
        # _apply for why this cannot be read at APPLY time.
        self._opened_engine = self._engine_fingerprint()
        # Warm anything a provider can only learn by asking the machine, off
        # this thread. The installed voice list costs a PowerShell subprocess —
        # measured at 1.70 seconds — and the form asks for it on every rebuild,
        # so without this the panel freezes for most of two seconds every time
        # a dropdown is touched. Started here rather than at startup, because
        # nothing in this folder may run before somebody opens it.
        self._live_ready.connect(self._on_live_ready)
        self._models_ready.connect(self._on_models_ready)
        self._model_cache: dict[str, list] = {}
        self._fetching: set = set()
        for _kind in ("tts", "stt", "llm"):
            for _decl in registry.discover(_kind):
                try:
                    _mod = __import__(f"providers.{_decl['_module']}",
                                      fromlist=["prefetch"])
                    _pf = getattr(_mod, "prefetch", None)
                    if _pf:
                        _pf(self._live_ready.emit)
                except Exception:
                    pass
        self._edits: dict[str, dict] = {}       # provider id -> {key: (widget, Field)}
        self._status: dict[str, QLabel] = {}    # provider id -> status line
        self._more: dict[str, bool] = {}        # provider id -> advanced shown
        # Fields deliberately not drawn — the catalog already answered them, or
        # they are behind MORE. Tracked rather than asked of Qt: see _fields_into.
        self._hidden: dict[str, set] = {}

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ProvidersOverlay {{
                background: rgba(0, 6, 10, 249);
                border: 1px solid {C.BORDER_B};
                border-radius: 8px;
            }}
        """)
        self.setFixedWidth(self._OW)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(10)

        title = QLabel("🔌  ENGINE  &  PROVIDERS")
        title.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        outer.addWidget(title)

        sub = QLabel("Choose what powers the conversation. "
                     "Gemini is the default and needs nothing set up here.")
        sub.setWordWrap(True)
        sub.setFont(QFont("Courier New", 8))
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        outer.addWidget(sub)

        # ── Mode: two cards, not a dropdown ─────────────────────────────────
        # A dropdown hides the option you did not choose. This is the one
        # decision on the panel that changes everything below it, and both
        # answers deserve to be readable at the same time.
        self._mode = get_provider_mode()
        cards = QHBoxLayout(); cards.setSpacing(10)
        self._card_gemini = self._mode_card(
            "☁  GEMINI LIVE",
            "One connection carries your voice, its voice, the transcript and "
            "the tools together. The fastest option — about half a second to "
            "the first word.",
            "gemini")
        self._card_local = self._mode_card(
            "🖥  MY OWN SETUP",
            "Your choice of hearing, thinking and speaking. Can run entirely on "
            "this computer, with nothing leaving it. Slower: 1.5 to 4 seconds.",
            "pipeline")
        cards.addWidget(self._card_gemini)
        cards.addWidget(self._card_local)
        outer.addLayout(cards)

        # ── Body ────────────────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; }"
            f"QScrollBar:vertical {{ background: {C.DARK}; width: 9px; "
            f"margin: 0; border-radius: 4px; }}"
            f"QScrollBar::handle:vertical {{ background: {C.BORDER_B}; "
            f"border-radius: 4px; min-height: 30px; }}"
            "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }"
            "QScrollBar::add-page, QScrollBar::sub-page { background: none; }")
        self._scroll.setMinimumHeight(230)
        self._scroll.setMaximumHeight(360)
        outer.addWidget(self._scroll, 1)

        self._body = QWidget()
        self._body.setStyleSheet("background: transparent;")
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 2, 8, 2)
        self._body_lay.setSpacing(10)
        self._scroll.setWidget(self._body)

        # Built BEFORE the body, because `_rebuild_body` writes into it — and
        # added to the layout after, so it still sits under the scroll area.
        self._footer = QLabel("")
        self._footer.setWordWrap(True)
        self._footer.setFont(QFont("Courier New", 7))
        self._footer.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        # Every value below is a service id — "openai_compat@minimax" — so the
        # fallback, the coding model and ② THINKING can each be a different
        # service with settings of its own.
        self._pipe = {slot: self._normalise(sid, slot)
                      for slot, sid in dict(get_pipeline()).items()}
        self._sidecar = self._normalise(get_sidecar_provider(), "llm")
        self._coding = self._normalise(get_coding_provider(), "llm")
        self._sidecar_first = get_sidecar_first()
        self._rebuild_body()

        outer.addWidget(self._footer)

        row = QHBoxLayout(); row.setSpacing(8)
        apply_btn = QPushButton("▸  SAVE  &&  APPLY")
        apply_btn.setFixedHeight(34)
        apply_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_btn.setStyleSheet(f"""
            QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 4px; }}
            QPushButton:hover {{ background: {C.BORDER}; border-color: {C.PRI}; }}
        """)
        apply_btn.clicked.connect(self._apply)
        row.addWidget(apply_btn, 2)

        close = QPushButton("CLOSE")
        close.setFixedHeight(34)
        close.setFont(QFont("Courier New", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 4px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        row.addWidget(close, 1)
        outer.addLayout(row)
        self._refresh_cards()

    # ── pieces ───────────────────────────────────────────────────────────────
    def _mode_card(self, title: str, body: str, value: str) -> QPushButton:
        b = QPushButton()
        b.setCheckable(True)
        b.setMinimumHeight(84)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setText(f"{title}\n\n{body}")
        b.setFont(QFont("Courier New", 7))
        b.clicked.connect(lambda _=0, v=value: self._set_mode(v))
        b.setProperty("modeValue", value)
        return b

    def _refresh_cards(self) -> None:
        for card in (self._card_gemini, self._card_local):
            on = card.property("modeValue") == self._mode
            card.setChecked(on)
            card.setStyleSheet(f"""
                QPushButton {{
                    text-align: left; padding: 10px 12px;
                    background: {C.PRI_GHO if on else 'transparent'};
                    color: {C.TEXT if on else C.TEXT_DIM};
                    border: 1px solid {C.PRI_DIM if on else C.BORDER};
                    border-radius: 5px;
                }}
                QPushButton:hover {{ border-color: {C.BORDER_B}; }}
            """)

    def _on_live_ready(self) -> None:
        """A machine-supplied list has arrived — redraw once so it appears."""
        if not self.isHidden():
            self._rebuild_body()

    # ── Asking an endpoint what it has, without being asked to ──────────────
    def _fetch_models(self, pid: str) -> None:
        """Go and get the model list in the background.

        Pressing LIST worked, and making somebody press it was still wrong: at
        the moment a local server is chosen, the one thing they need is the list
        of models on it, and the app can simply go and read it. A button whose
        only job is "now do the obvious next thing" is a button that should not
        exist.

        Off the interface thread, because it is a network call — a local server
        answers in milliseconds and a cloud one does not, and a panel that
        freezes while it finds out is worse than one that fills in a second
        later. Failure is silent: not reaching the endpoint yet is the normal
        state while somebody is still typing an address, and TEST is where a
        real answer about reachability belongs."""
        import threading

        if not pid or pid in self._fetching:
            return
        try:
            values = self._values(pid) or dict(self._get_cfg(pid))
        except Exception:
            values = dict(self._get_cfg(pid))
        if not values.get("base_url") and not values.get("api_key"):
            return
        self._fetching.add(pid)

        def work():
            items = []
            try:
                inst = self._registry.build(pid, values)
                if hasattr(inst, "models"):
                    items = [(m, m) for m in inst.models(timeout=6.0)]
                elif hasattr(inst, "voices"):
                    items = list(inst.voices())
            except Exception:
                items = []
            finally:
                self._fetching.discard(pid)
            if items:
                self._models_ready.emit(pid, items)

        threading.Thread(target=work, daemon=True).start()

    def _prepare(self, pid: str) -> None:
        """Let a provider get itself ready as soon as it is configured.

        Piper uses it to download the chosen voice. Generic on purpose: the
        panel asks whether the module has a `prepare`, and providers that do not
        need one are simply skipped — nothing here knows what a voice is."""
        import threading

        try:
            decl = self._registry.get_declaration(pid)
            mod = __import__(f"providers.{decl['_module']}", fromlist=["prepare"])
            fn = getattr(mod, "prepare", None)
        except Exception:
            fn = None
        if fn is None:
            return
        values = self._values(pid) or dict(self._get_cfg(pid))

        def report(ok, msg):
            colour = C.TEXT_DIM if ok is None else (C.GREEN if ok else C.RED)
            mark = "" if ok is None else ("✓  " if ok else "✕  ")
            QTimer.singleShot(0, lambda: self._say(pid, mark + msg, colour))

        threading.Thread(target=lambda: fn(values, report), daemon=True).start()

    def _on_models_ready(self, pid: str, items: list) -> None:
        self._model_cache[pid] = list(items)
        if self.isHidden():
            return
        key = "voice_id" if any(v != l for v, l in items) else "model"
        entry = self._edits.get(pid, {}).get(key)
        if entry is None:
            entry = self._edits.get(pid, {}).get("model")
        if entry is None or not isinstance(entry[0], QComboBox):
            return
        box = entry[0]
        keep = box.currentText()
        box.clear()
        for value, label in items:
            box.addItem(str(label), str(value))
        j = box.findData(keep)
        if j >= 0:
            box.setCurrentIndex(j)
        elif keep:
            box.setCurrentText(keep)
        self._say(pid, f"{len(items)} model{'s' if len(items) != 1 else ''} "
                       f"found on this server — pick one above.", C.TEXT_DIM)

    def _set_mode(self, value: str) -> None:
        if value == self._mode:
            self._refresh_cards()
            return
        self._mode = value
        self._remember_edits()
        self._refresh_cards()
        QTimer.singleShot(0, self._rebuild_body)

    def _box(self, title_widgets: list) -> tuple[QWidget, QVBoxLayout]:
        """A bordered section. Grouping is the difference between three
        decisions and eleven controls."""
        w = QWidget()
        w.setStyleSheet(f"background: {C.PANEL}; border: 1px solid {C.BORDER}; "
                        f"border-radius: 5px;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 10, 12, 11)
        lay.setSpacing(5)
        for tw in title_widgets:
            lay.addWidget(tw)
        return w, lay

    def _label(self, text: str, size: int = 8, colour: str = None,
               bold: bool = False) -> QLabel:
        lb = QLabel(text)
        lb.setWordWrap(True)
        lb.setFont(QFont("Courier New", size,
                         QFont.Weight.Bold if bold else QFont.Weight.Normal))
        lb.setStyleSheet(f"color: {colour or C.TEXT_MED}; background: transparent; "
                         f"border: none;")
        return lb

    def _combo(self) -> QComboBox:
        box = _ScrollSafeCombo()
        box.setFont(QFont("Courier New", 8))
        box.setFixedHeight(29)
        box.setStyleSheet(
            f"QComboBox {{ background: {C.DARK}; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 9px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox::drop-down {{ border: none; width: 18px; }}"
            f"QComboBox QAbstractItemView {{ background: {C.DARK}; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; "
            f"border: 1px solid {C.BORDER_B}; padding: 2px; }}")
        return box

    def _line(self, text: str = "") -> QLineEdit:
        w = QLineEdit(text)
        w.setFont(QFont("Courier New", 8))
        w.setFixedHeight(27)
        w.setStyleSheet(
            f"QLineEdit {{ background: {C.DARK}; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 9px; }}"
            f"QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}")
        return w

    def _btn(self, text: str, slot, colour: str = None) -> QPushButton:
        b = QPushButton(text)
        b.setFixedHeight(25)
        b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        col = colour or C.TEXT_MED
        b.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {col};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 0 12px; }}
            QPushButton:hover {{ border-color: {C.BORDER_B}; color: {C.TEXT}; }}
        """)
        b.clicked.connect(slot)
        return b

    # ── catalog ──────────────────────────────────────────────────────────────
    def _catalog(self, kind: str) -> list[tuple[str, str, dict, dict]]:
        """(provider id, entry id, entry, declaration) for one slot.

        A provider with no catalog contributes itself as a single entry, so
        nothing has to declare one to appear."""
        out = []
        for decl in self._registry.discover(kind):
            entries = decl.get("catalog") or (
                {"id": decl["id"], "label": decl["label"],
                 "note": decl.get("hint", ""), "local": None, "values": {}},)
            for e in entries:
                out.append((decl["id"], e["id"], e, decl))
        return out

    def _current_entry(self, kind: str, pid: str) -> str:
        """Which catalog entry is selected.

        STORED, not derived. The first version worked it out by comparing the
        saved address against each entry's — which is elegant and wrong, and it
        showed up immediately: pick "Whisper on this computer", reopen the
        panel, and the box reads "— not set —" while the form beneath it is
        fully drawn. That entry's address is the empty string, so any config
        whose address had ever been touched matched nothing and fell through to
        no selection at all.

        The entry is a CHOICE somebody made, not a fact about the settings.
        Choices get stored. The derivation is kept only as a fallback for a
        config written before this key existed."""
        if not pid:
            return ""
        cfg = self._get_cfg(pid)
        saved = str(cfg.get("catalog") or "").strip()
        entries = [(eid, entry) for _p, eid, entry, _d in self._catalog(kind)
                   if _p == pid]
        if saved and any(eid == saved for eid, _e in entries):
            return saved

        # Written before the key existed: work it out once, from the address.
        best = ""
        for eid, entry in entries:
            vals = entry.get("values") or {}
            if not vals:
                best = best or eid            # a catch-all like "Something else"
                continue
            if all(str(cfg.get(k, "")).rstrip("/") == str(v).rstrip("/")
                   for k, v in vals.items() if k in ("base_url", "backend")):
                return eid
        return best or (entries[0][0] if len(entries) == 1 else "")

    def _sid(self, pid: str, eid: str) -> str:
        """The service id for one catalog row: "openai_compat@minimax", or the
        bare provider id for a provider without a catalog."""
        if not pid:
            return ""
        decl = self._registry.get_declaration(pid) or {}
        return f"{pid}@{eid}" if decl.get("catalog") and eid else pid

    def _parts(self, sid: str, kind: str) -> tuple[str, str]:
        """(provider id, catalog entry id) for a service id."""
        if not sid:
            return "", ""
        base, entry = self._registry.split_id(sid)
        if "@" not in sid:
            entry = self._current_entry(kind, base) or base
        return base, entry

    def _normalise(self, sid: str, kind: str) -> str:
        """A stored id in its current shape.

        config_manager converts every config that recorded which service it was
        for. What is left is a provider id with no record of that, from before
        the record existed: the service is worked out from the address once,
        and its settings come along to the new id."""
        if not sid or "@" in sid:
            return sid
        base, eid = self._parts(sid, kind)
        new = self._sid(base, eid)
        if new != sid and not self._get_cfg(new) and self._get_cfg(sid):
            from memory.config_manager import save_provider_config
            save_provider_config(new, dict(self._get_cfg(sid), catalog=eid))
        return new

    def _service_combo(self, kind: str, first: tuple, fmt) -> QComboBox:
        """A dropdown of every service for one slot. `first` is the row for
        "nothing chosen"; `fmt(entry, decl)` is the text for each service."""
        box = self._combo()
        box.addItem(*first)
        for pid, eid, entry, decl in self._catalog(kind):
            box.addItem(fmt(entry, decl), self._sid(pid, eid))
        return box

    def _select(self, box: QComboBox, sid: str) -> None:
        i = box.findData(sid) if sid else 0
        box.setCurrentIndex(max(0, i))

    def _apply_entry(self, kind: str, sid: str) -> None:
        """Store a service's shortcut values, the first time it is picked.

        Each service keeps its own settings now, so picking MiniMax again after
        trying OpenAI brings back the MiniMax model and key as they were left.
        Only a preset address is always rewritten — that one belongs to the
        service, not to the person.

        The open form's widgets are dropped afterwards, because the redraw that
        follows saves whatever they hold, and the redraw must not write one
        service's fields into another's settings."""
        from memory.config_manager import save_provider_config
        pid, eid = self._parts(sid, kind)
        stored = self._get_cfg(sid)
        for _p, _e, entry, _d in self._catalog(kind):
            if _p == pid and _e == eid:
                values = {k: v for k, v in (entry.get("values") or {}).items()
                          if k == "base_url" or k not in stored}
                values["catalog"] = eid
                save_provider_config(sid, values)
                break
        self._edits.pop(sid, None)

    # ── body ─────────────────────────────────────────────────────────────────
    def _clear(self, lay) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif item.layout() is not None:
                self._clear(item.layout())

    def _rebuild_body(self) -> None:
        # Keep whatever is in the boxes before they are destroyed.
        #
        # A redraw can be triggered by things the person did not do: a model
        # list arriving from a background fetch, a slot changing, the panel
        # settling after a click. Every one of those replaces the widgets, and
        # anything typed but not yet saved went with them — most visibly a model
        # name entered by hand, which came back empty on SAVE with no
        # indication that it had been dropped.
        self._remember_edits()
        self._clear(self._body_lay)
        self._edits.clear()
        self._status.clear()

        if self._mode == "pipeline":
            for slot, num, name, what in self._SLOTS:
                self._body_lay.addWidget(self._slot_box(slot, num, name, what))
        else:
            w, lay = self._box([])
            lay.addWidget(self._label(
                "Nothing to set up. Your voice goes to Gemini and comes back as "
                "speech over one connection, with the transcript and every tool "
                "call on the same wire.", 8, C.TEXT_DIM))
            lay.addWidget(self._label(
                "Pick MY OWN SETUP above to run it on your own machine, or "
                "through another company's model.", 7, C.TEXT_DIM))
            self._body_lay.addWidget(w)

        self._body_lay.addWidget(self._sidecar_box_widget())
        self._body_lay.addWidget(self._coding_box_widget())
        self._body_lay.addStretch(1)
        self._update_footer()

    def _slot_box(self, slot: str, num: str, name: str, what: str) -> QWidget:
        head = self._label(f"{num}  {name}", 9, C.PRI, bold=True)
        w, lay = self._box([head, self._label(what, 7, C.TEXT_DIM)])

        def fmt(entry, decl):
            label = entry["label"]
            if entry.get("local") is True:
                label = f"🖥  {label}"
            elif entry.get("local") is False:
                label = f"☁  {label}"
            else:
                label = f"    {label}"
            note = entry.get("note") or ""
            if not decl.get("_available", True):
                note = (note + " · needs install").strip(" ·")
            return f"{label}{'   —  ' + note if note else ''}"

        box = self._service_combo(slot, ("— not set —", ""), fmt)
        sid = self._pipe.get(slot, "")
        self._select(box, sid)
        box.currentIndexChanged.connect(
            lambda _i, s=slot, b=box: self._slot_changed(s, b))
        if sid:
            QTimer.singleShot(0, lambda p=sid: self._prepare(p))
        lay.addWidget(box)

        if sid:
            self._fields_into(lay, slot, sid)
            # If a list was already fetched, use it; if not, go and get one.
            if self._model_cache.get(sid):
                QTimer.singleShot(0, lambda p=sid: self._on_models_ready(
                    p, self._model_cache[p]))
            else:
                QTimer.singleShot(0, lambda p=sid: self._fetch_models(p))
        return w

    def _slot_changed(self, slot: str, box: QComboBox) -> None:
        sid = box.currentData() or ""
        self._remember_edits()
        self._pipe[slot] = sid
        if sid:
            # Applying the shortcut is the entire point of the catalog: pick
            # Ollama and the address is already right.
            self._apply_entry(slot, sid)
            self._fetch_models(sid)
        QTimer.singleShot(0, self._rebuild_body)

    def _fields_into(self, lay, slot: str, sid: str) -> None:
        from dataclasses import replace

        from providers.base import Field

        # Everything this form stores, shows and tests is keyed by the SERVICE
        # id, so two services of one provider never share a box or a setting.
        pid = sid
        base, eid = self._parts(sid, slot)
        decl = self._registry.get_declaration(base)
        if decl is None:
            return
        stored = dict(self._get_cfg(pid))
        entry = next((e for p, i, e, _ in self._catalog(slot)
                      if p == base and i == eid), {})
        is_local = entry.get("local") is True
        entry_values = entry.get("values") or {}
        # An entry that MENTIONS the address has answered it — including
        # answering it with "", which is what the local speech recogniser does
        # because it does not use one at all. Testing the value rather than the
        # presence of the key is what put an empty "API address" box under
        # "Whisper on this computer", inviting somebody to fill in a field that
        # backend never reads.
        custom = "base_url" not in entry_values

        # Three buckets, tracked explicitly.
        #
        # `isHidden()` is NOT a usable test for "I hid this on purpose": a widget
        # that has not been added to a layout yet is already hidden, so asking it
        # returned True for every field and the form came up completely empty.
        # Which of these a field lands in is a decision made here, so it is kept
        # here rather than inferred from Qt afterwards.
        shown: dict = {}        # drawn now
        carried: dict = {}      # kept for saving, never drawn — answered by the catalog
        advanced: list = []     # drawn only behind MORE

        for f in decl.get("needs", ()):
            if not isinstance(f, Field):
                continue
            # Do not show what the catalog entry already answered. The address
            # somebody did not have to type is one more thing to read and one
            # more thing to break; a key a local server never asks for is a box
            # that makes people think they need an account.
            if f.key == "base_url" and not custom:
                carried[f.key] = (self._line(
                    str(stored.get(f.key, f.default) or "")), f)
                continue
            if f.key == "api_key" and is_local:
                carried[f.key] = (self._line(str(stored.get(f.key, "") or "")), f)
                continue
            # The provider declares the key optional because local servers take
            # none — and that one declaration was printing "(optional)" and
            # "Local servers usually need none" under MiniMax and OpenAI, which
            # refuse every request without one.
            if f.key == "api_key" and entry.get("local") is False:
                f = replace(f, required=True,
                            hint=f"Needed — create one in your {entry['label']} account")
            if f.key == "backend":
                carried[f.key] = (self._line(
                    str(entry.get("values", {}).get("backend")
                        or stored.get(f.key, f.default) or "")), f)
                continue
            w = self._make_widget(pid, f, stored.get(f.key, f.default))
            if f.advanced:
                advanced.append((f, w))
            else:
                shown[f.key] = (w, f)

        for _key, (w, f) in shown.items():
            lay.addWidget(self._label(
                f.label + ("" if f.required else "   (optional)"), 7, C.TEXT_DIM))
            lay.addWidget(w)
            # A text box already shows its hint as placeholder text. Adding the
            # label as well printed every hint twice, which is how the panel
            # came to look careless.
            if f.hint and not isinstance(w, QLineEdit):
                lay.addWidget(self._label(f.hint, 7, C.TEXT_DIM))

        fields = dict(shown)
        fields.update(carried)
        self._hidden[pid] = set(carried) | {f.key for f, _w in advanced}

        # Buttons and status.
        status = self._label("", 7, C.TEXT_DIM)
        self._status[pid] = status

        brow = QHBoxLayout(); brow.setSpacing(6)
        brow.addWidget(self._btn("TEST", lambda _=0, p=pid: self._test(p), C.PRI))
        # Not "LIST" any more: the list arrives on its own when a service is
        # chosen. This is the button for when it did not — the server was
        # starting up, or a model has just been pulled.
        if any(k in shown for k in ("model", "voice_id", "voice")):
            brow.addWidget(self._btn("↻ REFRESH", lambda _=0, p=pid: self._list(p)))
        if advanced:
            brow.addWidget(self._btn(
                ("⌃ LESS" if self._more.get(pid) else "⌄ MORE"),
                lambda _=0, p=pid: self._toggle_more(p)))
        if not decl.get("_available", True) and decl.get("install"):
            brow.addWidget(self._btn(f"INSTALL {decl['install']}",
                                     lambda _=0, p=pid: self._install(p), C.ACC2))
        brow.addStretch(1)
        holder = QWidget()
        holder.setStyleSheet("background: transparent; border: none;")
        holder.setLayout(brow)
        lay.addWidget(holder)
        lay.addWidget(status)

        if self._more.get(pid):
            lay.addWidget(self._label("─" * 46, 7, C.BORDER))
            for f, w in advanced:
                lay.addWidget(self._label(f"{f.label}   (optional)", 7, C.TEXT_DIM))
                lay.addWidget(w)
                if f.hint and not isinstance(w, QLineEdit):
                    lay.addWidget(self._label(f.hint, 7, C.TEXT_DIM))
                fields[f.key] = (w, f)
                self._hidden[pid].discard(f.key)
        else:
            for f, w in advanced:
                fields[f.key] = (w, f)

        self._edits[pid] = fields

    def _make_widget(self, pid: str, f, value):
        if f.kind == "choice":
            w = self._combo()
            choices = list(self._live_choices(pid, f.key) or f.choices)
            if not f.required:
                w.addItem("— default —", "")
            for c in choices:
                w.addItem(str(c), str(c))
            w.setEditable(f.key in ("base_url", "model", "voice_id"))
            if w.isEditable():
                w.lineEdit().setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            if value:
                j = w.findData(str(value))
                if j < 0:
                    w.addItem(str(value), str(value))
                    j = w.count() - 1
                w.setCurrentIndex(j)
                if w.isEditable():
                    w.setCurrentText(str(value))
            return w
        if f.kind == "bool":
            w = self._combo()
            w.addItem("no", "")
            w.addItem("yes", "1")
            w.setCurrentIndex(1 if str(value) in ("1", "True", "true") else 0)
            return w
        w = self._line(str(value if value is not None else ""))
        if f.kind == "secret":
            w.setEchoMode(QLineEdit.EchoMode.Password)
        if f.hint:
            w.setPlaceholderText(f.hint[:64])
        return w

    def _toggle_more(self, pid: str) -> None:
        self._more[pid] = not self._more.get(pid)
        self._remember_edits()
        QTimer.singleShot(0, self._rebuild_body)

    def _sidecar_box_widget(self) -> QWidget:
        head = self._label("⚡  BACKGROUND REQUESTS", 9, C.PRI, bold=True)
        w, lay = self._box([head])
        lay.addWidget(self._label(
            "Reading a document, looking at your screen, writing code — the "
            "assistant's own requests, not the conversation. These use Gemini's "
            "text quota, which runs out before the voice does. Sending them "
            "somewhere else keeps them working, and changes nothing you hear.",
            7, C.TEXT_DIM))

        # Read the stored choice BEFORE the box exists. The first version asked
        # the combo — and at this point that is the empty box being built, so
        # the answer was always "nothing chosen" and the form never appeared.
        sid = self._sidecar

        def fmt(entry, _decl):
            mark = "🖥" if entry.get("local") else (
                "☁" if entry.get("local") is False else "  ")
            return f"{mark}  Fall back to {entry['label']}"

        self._sidecar_combo = self._service_combo(
            "llm", ("Gemini only  —  the way it has always been", ""), fmt)
        self._select(self._sidecar_combo, sid)
        self._sidecar_combo.currentIndexChanged.connect(self._sidecar_changed)
        lay.addWidget(self._sidecar_combo)

        self._first_combo = self._combo()
        self._first_combo.addItem("Try Gemini first  —  faster", "after")
        self._first_combo.addItem(
            "Try the other one first  —  keeps more on your machine", "before")
        self._first_combo.setCurrentIndex(1 if self._sidecar_first else 0)
        lay.addWidget(self._first_combo)

        # In pipeline mode the model chosen for ② THINKING may answer these too,
        # and then there is nothing more to fill in. In Gemini mode there IS no
        # ② section, so a service picked here gets its own form, right here.
        if sid:
            if self._mode == "pipeline" and sid == self._pipe.get("llm"):
                lay.addWidget(self._label(
                    "Uses the model you chose for ② THINKING.", 7, C.TEXT_DIM))
            else:
                self._fields_into(lay, "llm", sid)
        else:
            lay.addWidget(self._label(
                "Left as it is, these keep using Gemini and stop working when "
                "its text quota runs out.", 7, C.TEXT_DIM))
        return w

    def _coding_box_widget(self) -> QWidget:
        """Which model dev_agent writes code with.

        Separate from background requests because it is the one job where the
        model makes the most difference and the requests are the longest — a
        whole source file at a time. Whatever is picked is tried FIRST for code,
        with Gemini behind it, so a service having a bad day still produces a
        project instead of nothing."""
        head = self._label("🛠  CODING", 9, C.PRI, bold=True)
        w, lay = self._box([head])
        lay.addWidget(self._label(
            "The model that plans, writes and fixes projects when you ask the "
            "assistant to build something. Saved to Desktop/JarvisProjects.",
            7, C.TEXT_DIM))

        sid = self._coding

        def fmt(entry, _decl):
            mark = "🖥" if entry.get("local") else (
                "☁" if entry.get("local") is False else "  ")
            star = "   ★ recommended" if entry.get("id") == "minimax" else ""
            return f"{mark}  Code with {entry['label']}{star}"

        self._coding_combo = self._service_combo(
            "llm", ("Same as background requests", ""), fmt)
        # The sponsor's model sits directly under the default, where it is the
        # first real choice anybody sees — not buried twelfth in the list.
        for i in range(self._coding_combo.count()):
            if (self._coding_combo.itemData(i) or "").endswith("@minimax"):
                text = self._coding_combo.itemText(i)
                data = self._coding_combo.itemData(i)
                self._coding_combo.removeItem(i)
                self._coding_combo.insertItem(1, text, data)
                break
        self._select(self._coding_combo, sid)
        self._coding_combo.currentIndexChanged.connect(self._coding_changed)
        lay.addWidget(self._coding_combo)

        if not sid:
            lay.addWidget(self._label(
                "Code is written by whatever answers background requests above.",
                7, C.TEXT_DIM))
        elif sid == self._sidecar:
            lay.addWidget(self._label(
                "Uses the settings under BACKGROUND REQUESTS.", 7, C.TEXT_DIM))
        elif self._mode == "pipeline" and sid == self._pipe.get("llm"):
            lay.addWidget(self._label(
                "Uses the model you chose for ② THINKING.", 7, C.TEXT_DIM))
        else:
            self._fields_into(lay, "llm", sid)
        return w

    def _sidecar_changed(self, _ix) -> None:
        sid = self._sidecar_combo.currentData() or ""
        self._remember_edits()
        self._sidecar = sid
        if sid:
            self._apply_entry("llm", sid)
            self._fetch_models(sid)
        QTimer.singleShot(0, self._rebuild_body)

    def _coding_changed(self, _ix) -> None:
        sid = self._coding_combo.currentData() or ""
        self._remember_edits()
        self._coding = sid
        if sid:
            self._apply_entry("llm", sid)
            self._fetch_models(sid)
        QTimer.singleShot(0, self._rebuild_body)

    def _update_footer(self) -> None:
        if self._mode != "pipeline":
            self._footer.setText(
                "Gemini Live. Nothing on this panel changes the conversation.")
            return
        missing = [name for slot, _n, name, _w in self._SLOTS
                   if not self._pipe.get(slot)]
        if "THINKING" in missing:
            self._footer.setText(
                "⚠  ② THINKING is not set — without it the assistant falls back "
                "to Gemini Live and says so in the log.")
        elif missing:
            words = " and ".join(f"{m.lower()}" for m in missing)
            self._footer.setText(
                f"Only {words} is missing — it will run, but you will type "
                f"instead of speak, or read instead of listen.")
        else:
            self._footer.setText(
                "All three set. SAVE & APPLY reconnects; the conversation is kept.")

    # ── actions ──────────────────────────────────────────────────────────────
    def _live_choices(self, pid: str, key: str) -> tuple:
        try:
            decl = self._registry.get_declaration(pid)
            mod = __import__(f"providers.{decl['_module']}", fromlist=["choices_for"])
            fn = getattr(mod, "choices_for", None)
            return tuple(fn(key)) if fn else ()
        except Exception:
            return ()

    def _values(self, pid: str) -> dict:
        out = {}
        hidden = self._hidden.get(pid, set())
        stored = self._get_cfg(pid)
        for key, (w, _f) in self._edits.get(pid, {}).items():
            if isinstance(w, QComboBox):
                value = (w.currentText().strip() if w.isEditable()
                         else (w.currentData() or ""))
            else:
                value = w.text().strip()
            # A field nobody can see must never blank out a setting that has
            # one. These widgets are rebuilt on every redraw, and a redraw that
            # raced a background fetch could recreate a hidden address as empty
            # and then save that over a working one — leaving a pipeline that
            # had been configured correctly pointing at nothing, with no visible
            # cause. Clearing a value is still possible; it just has to be done
            # in a box that is on screen.
            if not value and key in hidden and stored.get(key):
                continue
            out[key] = value
        return out

    def _remember_edits(self) -> None:
        """Keep what has been typed when the form is rebuilt.

        Without it, changing the speech engine wiped an API key somebody had
        just entered into the box above."""
        from memory.config_manager import save_provider_config
        for pid in list(self._edits):
            try:
                save_provider_config(pid, self._values(pid))
            except Exception:
                pass

    def _say(self, pid: str, text: str, colour: str) -> None:
        lb = self._status.get(pid)
        if lb is not None:
            lb.setText(text)
            lb.setStyleSheet(f"color: {colour}; background: transparent; border: none;")

    def _test(self, pid: str) -> None:
        # Read the boxes BEFORE letting Qt run. `processEvents` will happily
        # deliver a queued rebuild, which deletes the very widgets this is
        # about to read — and a deleted QWidget raises rather than returning
        # something wrong, so it takes the click down with it.
        values = self._values(pid)
        self._say(pid, "checking…", C.TEXT_DIM)
        QApplication.processEvents()
        try:
            inst = self._registry.build(pid, values)
            probe = getattr(inst, "probe", None)
            if probe is None:
                self._say(pid, "Nothing to check for this one.", C.TEXT_DIM)
                return
            ok, msg = probe()
        except Exception as e:
            self._say(pid, f"✕  {type(e).__name__}: {str(e)[:150]}", C.RED)
            return
        self._say(pid, ("✓  " if ok else "✕  ") + msg, C.GREEN if ok else C.ACC)
        # TEST is also where "can this model see?" gets answered — by asking the
        # endpoint, not the user. Write the answer back into the stored settings
        # so the assistant is told the truth about its own eyes.
        seen = (getattr(inst, "caps", {}) or {}).get("vision")
        if ok and seen is not None:
            from memory.config_manager import save_provider_config
            save_provider_config(pid, {"vision": "1" if seen else ""})
            entry = self._edits.get(pid, {}).get("vision")
            if entry is not None and isinstance(entry[0], QComboBox):
                entry[0].setCurrentIndex(1 if seen else 0)

    def _list(self, pid: str) -> None:
        values = self._values(pid)          # before processEvents — see _test
        self._say(pid, "reading…", C.TEXT_DIM)
        QApplication.processEvents()
        try:
            inst = self._registry.build(pid, values)
            if hasattr(inst, "models"):
                items, key = [(m, m) for m in inst.models()], "model"
            elif hasattr(inst, "voices"):
                items, key = list(inst.voices()), "voice_id"
            else:
                self._say(pid, "Nothing to list for this one.", C.TEXT_DIM)
                return
        except Exception as e:
            self._say(pid, f"✕  {type(e).__name__}: {str(e)[:150]}", C.RED)
            return

        entry = self._edits.get(pid, {}).get(key)
        if entry is None or not items:
            self._say(pid, f"✓  {len(items)} found." if items else "None found.",
                      C.GREEN if items else C.TEXT_DIM)
            return
        box = entry[0]
        if isinstance(box, QComboBox):
            keep = box.currentText()
            box.clear()
            for value, label in items:
                box.addItem(str(label), str(value))
            j = box.findData(keep)
            box.setCurrentIndex(j if j >= 0 else 0)
        else:
            self._say(pid, f"✓  {len(items)} available: "
                           f"{', '.join(str(v) for v, _ in items[:8])}", C.GREEN)
            return
        self._say(pid, f"✓  {len(items)} available — pick one above.", C.GREEN)

    def _install(self, pid: str) -> None:
        """Fetch a provider's optional dependency, off the Qt thread — pip takes
        tens of seconds and a frozen window looks like a crashed one."""
        import subprocess
        import sys as _sys
        import threading

        decl = self._registry.get_declaration(pid) or {}
        pkg = decl.get("install")
        if not pkg:
            return
        self._say(pid, f"installing {pkg} — this can take a minute…", C.ACC2)

        def work():
            try:
                r = subprocess.run(
                    [_sys.executable, "-m", "pip", "install", pkg],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
                good = r.returncode == 0
                tail = r.stdout.decode("utf-8", "replace").strip().splitlines()
                msg = ("✓  installed — close and reopen this panel"
                       if good else (tail[-1][:150] if tail else "pip failed"))
            except Exception as e:
                good, msg = False, f"{type(e).__name__}: {str(e)[:120]}"
            QTimer.singleShot(0, lambda: self._say(pid, msg,
                                                   C.GREEN if good else C.RED))

        threading.Thread(target=work, daemon=True).start()

    def _apply(self) -> None:
        from memory.config_manager import save_providers

        # ONE write, not five. See save_providers for what the five cost.
        creds = {sid: self._values(sid) for sid in list(self._edits)}

        save_providers(
            mode=self._mode,
            pipeline={"stt": self._pipe.get("stt", ""),
                      "llm": self._pipe.get("llm", ""),
                      "tts": self._pipe.get("tts", "")},
            creds=creds,
            sidecar=self._sidecar,
            sidecar_first=self._first_combo.currentData() == "before",
            coding=self._coding,
        )

        # Providers are cached by configuration; a changed setting must not go
        # on being served by an object built from the old one.
        try:
            self._registry.reset()
        except Exception:
            pass

        self.hide()
        # Background-request and coding settings take effect on the next call
        # with no reconnect. Only the conversation engine needs the session
        # rebuilt — and it does whenever anything it was BUILT from changed.
        #
        # Comparing only the mode and which service fills each slot is what
        # made APPLY look broken: a corrected model name, a new key or another
        # voice changed none of those, so nothing reconnected and the running
        # engine went on using the old settings until a restart. And the
        # "before" has to be taken when the panel opened — the form saves as
        # it is edited, so reading it here would compare the new settings with
        # themselves.
        if self._engine_fingerprint() != self._opened_engine:
            self.changed.emit()

    @staticmethod
    def _engine_fingerprint() -> str:
        """Everything the conversation engine is built from, as one string."""
        from memory.config_manager import (get_pipeline, get_provider_config,
                                           get_provider_mode)
        mode, pipe = get_provider_mode(), dict(get_pipeline())
        used = {sid: get_provider_config(sid) for sid in pipe.values() if sid} \
            if mode == "pipeline" else {}
        return json.dumps([mode, pipe, used], sort_keys=True, default=str)


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick Jarvis actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: rgba(0, 8, 14, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(QFont("Courier New", 8))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(QFont("Courier New", 8))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = (f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
               f"border: 1px solid {C.BORDER}; border-radius: 2px; }}"
               f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN",   "Explain this: {text}"),
            ("FIX",       "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


class PluginSettingsOverlay(QWidget):
    """Floating overlay — renders per-plugin settings forms.

    Fully generic: it iterates the settings schemas a plugin declared via its
    PLUGIN_SETTINGS constant (delivered by PluginRegistry.settings_schemas) and
    builds a form for each. It knows NOTHING about any specific plugin, so the
    core stays clean and plugins remain pure drop-in — install a plugin that
    declares fields (e.g. the 3D-printer suite) and its section appears here;
    install none and this panel simply says there's nothing to configure.
    """

    _test_done = pyqtSignal(str, bool, str)   # namespace, ok, message
    _OW = 460

    def __init__(self, sections: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginSettingsOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self._sections = sections or []
        self._widgets: dict[tuple, object] = {}    # (namespace, key) -> input widget
        self._types:   dict[tuple, str]    = {}     # (namespace, key) -> field type
        self._status_labels: dict[str, QLabel] = {} # namespace -> status QLabel
        self._test_done.connect(self._on_test_done)

        self._fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
                    f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(8)

        root.addWidget(self._lbl("⚙  PLUGIN SETTINGS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        root.addWidget(sep)

        if not self._sections:
            root.addWidget(self._lbl(
                "No configurable plugins are installed.\nDrop a plugin that needs "
                "settings (like the 3D-printer suite) into the plugins folder and "
                "it will show up here.", 9, color=C.TEXT_DIM))
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet("QScrollArea { background: transparent; }")
            inner = QWidget()
            inner.setStyleSheet("background: transparent;")
            form = QVBoxLayout(inner)
            form.setContentsMargins(0, 0, 6, 0)
            form.setSpacing(6)
            for sec in self._sections:
                self._build_section(form, sec)
            form.addStretch(1)
            scroll.setWidget(inner)
            root.addWidget(scroll, 1)

        # ── bottom buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        if self._sections:
            save_btn = QPushButton("▸  SAVE")
            save_btn.setFixedHeight(34)
            save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            save_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            """)
            save_btn.clicked.connect(self._save_all)
            btn_row.addWidget(save_btn)

        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _lbl(self, txt, fs=9, bold=False, color=C.PRI,
             align=Qt.AlignmentFlag.AlignLeft):
        w = QLabel(txt); w.setAlignment(align); w.setWordWrap(True)
        w.setFont(QFont("Courier New", fs,
                        QFont.Weight.Bold if bold else QFont.Weight.Normal))
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _build_section(self, form: QVBoxLayout, sec: dict):
        ns     = sec.get("namespace") or sec.get("plugin") or "plugin"
        title  = sec.get("title") or ns
        fields = sec.get("fields") or []
        values = sec.get("values") or {}

        form.addSpacing(4)
        form.addWidget(self._lbl(title, 10, True, C.PRI))

        for field in fields:
            if not isinstance(field, dict) or not field.get("key"):
                continue
            key   = field["key"]
            ftype = (field.get("type") or "text").lower()
            label = field.get("label") or key
            default = field.get("default")
            stored  = values.get(key, default)

            form.addWidget(self._lbl(label.upper(), 8, color=C.TEXT_DIM))

            if ftype == "choice":
                w = QComboBox()
                w.addItems([str(o) for o in field.get("options", [])])
                w.setFont(QFont("Courier New", 9))
                w.setFixedHeight(30)
                w.setStyleSheet(
                    f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 8px; }}"
                    f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
                    f"selection-background-color: {C.PRI_GHO}; }}")
                if stored is not None:
                    w.setCurrentText(str(stored))
            elif ftype == "toggle":
                w = QPushButton()
                w.setCheckable(True)
                w.setChecked(bool(stored))
                w.setFixedHeight(28)
                w.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                w.setCursor(Qt.CursorShape.PointingHandCursor)
                self._style_toggle(w)
                w.toggled.connect(lambda _=False, b=w: self._style_toggle(b))
            else:  # text / password
                w = QLineEdit("" if stored is None else str(stored))
                w.setFont(QFont("Courier New", 10))
                w.setFixedHeight(30)
                w.setStyleSheet(self._fs)
                if field.get("placeholder"):
                    w.setPlaceholderText(str(field["placeholder"]))
                if ftype == "password":
                    w.setEchoMode(QLineEdit.EchoMode.Password)

            self._widgets[(ns, key)] = w
            self._types[(ns, key)]   = ftype
            form.addWidget(w)

        # optional test/connect action button + status line
        action = sec.get("action")
        if isinstance(action, dict) and callable(action.get("run")):
            form.addSpacing(2)
            ab = QPushButton(str(action.get("label") or "TEST"))
            ab.setFixedHeight(30)
            ab.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            ab.setCursor(Qt.CursorShape.PointingHandCursor)
            ab.setStyleSheet(f"""
                QPushButton {{ background: #00091a; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            """)
            ab.clicked.connect(lambda _=False, n=ns: self._run_action(n))
            form.addWidget(ab)

        status = self._lbl("", 8, color=C.TEXT_DIM)
        self._status_labels[ns] = status
        form.addWidget(status)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        form.addWidget(line)

    def _style_toggle(self, btn: QPushButton):
        on = btn.isChecked()
        btn.setText("ON" if on else "OFF")
        if on:
            btn.setStyleSheet(f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
                              f"border: 1px solid {C.PRI}; border-radius: 3px; }}")
        else:
            btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                              f"border: 1px solid {C.BORDER}; border-radius: 3px; }}")

    # ── data ──────────────────────────────────────────────────────────────────
    def _gather(self, ns: str) -> dict:
        out = {}
        for (n, key), w in self._widgets.items():
            if n != ns:
                continue
            t = self._types.get((n, key), "text")
            if t == "choice":
                out[key] = w.currentText()
            elif t == "toggle":
                out[key] = w.isChecked()
            else:
                out[key] = w.text().strip()
        return out

    def _save_ns(self, ns: str):
        from memory.config_manager import save_plugin_config
        save_plugin_config(ns, self._gather(ns))

    def _save_all(self):
        for sec in self._sections:
            ns = sec.get("namespace") or sec.get("plugin")
            if ns:
                self._save_ns(ns)
                lbl = self._status_labels.get(ns)
                if lbl:
                    lbl.setText("Saved ✓")
                    lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")

    def _run_action(self, ns: str):
        sec = next((s for s in self._sections
                    if (s.get("namespace") or s.get("plugin")) == ns), None)
        if not sec:
            return
        run_fn = (sec.get("action") or {}).get("run")
        if not callable(run_fn):
            return
        self._save_ns(ns)                 # persist what the user typed before testing
        values = self._gather(ns)
        lbl = self._status_labels.get(ns)
        if lbl:
            lbl.setText("Testing…")
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        def worker():
            try:
                res = run_fn(values)
                if isinstance(res, tuple) and len(res) == 2:
                    ok, msg = bool(res[0]), str(res[1])
                else:
                    ok, msg = bool(res), str(res)
            except Exception as e:
                ok, msg = False, str(e)
            self._test_done.emit(ns, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, ns: str, ok: bool, msg: str):
        lbl = self._status_labels.get(ns)
        if not lbl:
            return
        lbl.setText(msg)
        color = C.PRI if ok else "#ff6b6b"
        lbl.setStyleSheet(f"color: {color}; background: transparent;")


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None,
                 bind_error: str = ""):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        # THE ONE FAILURE THAT LOOKS LIKE THE SERVER BEING OFF.
        #
        # Windows writes a BLOCK rule for an executable when its "Allow access?"
        # prompt is dismissed, and a block overrides every allow rule there is —
        # including the port rule this app carefully creates. The phone then
        # reports "refused to connect" while the server is running perfectly.
        #
        # It is invisible from the HUD, which is where the person scanning the
        # QR code is looking, and it is per-EXECUTABLE: the shortcut runs
        # pythonw.exe while an editor runs python.exe, so it can block one launch
        # method and not the other on the same machine. That difference is
        # unguessable without being told, so it is said here.
        # Whatever is stopping this from working, say it HERE — beside the QR
        # code, where the person about to scan it is looking. A dashboard that
        # cannot be reached and does not explain why is the single most
        # frustrating failure this app has.
        if bind_error:
            lay.addWidget(_lbl("⚠  " + bind_error, 8, color="#f4b942"))

        try:
            from dashboard.server import blocking_firewall_rules
            _blocked = blocking_firewall_rules()
        except Exception:
            _blocked = []
        if _blocked:
            warn = _lbl(
                "⚠  Windows is blocking incoming connections to this copy of "
                "Python, so your phone cannot reach this page. Restart JARVIS "
                "and accept the Windows prompt, or remove the rules named "
                + ", ".join(sorted({n for n, _ in _blocked}))
                + " under Windows Defender Firewall → Inbound Rules.",
                8, color="#f4b942")
            lay.addWidget(warn)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont("Courier New", 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Courier New", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Courier New", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Courier New", 8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Courier New", 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Courier New", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — JARVIS ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class BottomChatDock(QWidget):
    """
    Floating rectangular transcript and command input dock positioned at the
    bottom-center of the screen with wide width and short height.
    """
    def __init__(self, main_win: "MainWindow", parent=None):
        super().__init__(parent)
        self._main_win = main_win
        self.setWindowTitle("JARVIS Comm Link")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(480, 68)

        screen = QApplication.primaryScreen().availableGeometry()
        dock_w = min(840, max(540, screen.width() - 360))
        dock_h = 168
        dock_x = screen.left() + (screen.width() - dock_w) // 2
        dock_y = screen.bottom() - dock_h - 14
        self.setGeometry(dock_x, dock_y, dock_w, dock_h)

        self._drag_start = None
        self._win_start = None
        self._collapsed = False
        self._build_ui()

    def _build_ui(self):
        self._outer = QFrame(self)
        self._outer.setObjectName("ChatDockOuter")
        self._outer.setStyleSheet(f"""
            QFrame#ChatDockOuter {{
                background: rgba(14, 4, 13, 232);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._outer)

        lay = QVBoxLayout(self._outer)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(6)

        tag = QLabel("JARVIS COMM LOG // LIVE TRANSCRIPT")
        tag.setFont(keena_font(9, bold=True, spacing=1.2))
        tag.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(tag)
        hdr.addStretch()

        self._min_btn = QPushButton("MIN")
        self._min_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        self._min_btn.setFixedSize(30, 18)
        self._min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._min_btn.setToolTip("Minimize/Restore log view")
        self._min_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 77, 166, 0.15);
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
            }}
            QPushButton:hover {{
                background: rgba(255, 77, 166, 0.35);
                border-color: {C.PRI};
            }}
        """)
        self._min_btn.clicked.connect(self._toggle_collapse)
        hdr.addWidget(self._min_btn)

        close_btn = QPushButton("X")
        close_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        close_btn.setFixedSize(22, 18)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setToolTip("Hide Comm Link (reopen via HUD CHAT button)")
        close_btn.setStyleSheet(self._min_btn.styleSheet())
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._dock_log = LogWidget()
        self._dock_log.setFixedHeight(82)
        self._dock_log.setStyleSheet(f"""
            QTextEdit {{
                background: rgba(8, 2, 8, 190);
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 4px 6px;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 3px;
            }}
        """)
        self._log = self._dock_log
        lay.addWidget(self._dock_log, stretch=1)

        inp_row = QHBoxLayout()
        inp_row.setContentsMargins(0, 0, 0, 0)
        inp_row.setSpacing(6)

        self._input = QLineEdit()
        self._input.setFixedHeight(28)
        self._input.setPlaceholderText("Type a command or query... [Enter to send]")
        self._input.setFont(keena_font(9))
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(24, 7, 22, 210);
                color: #ffffff;
                border: 1px solid {C.BORDER_B};
                border-radius: 4px;
                padding: 2px 8px;
            }}
            QLineEdit:focus {{
                border: 1px solid {C.PRI};
                background: rgba(35, 10, 32, 230);
            }}
        """)
        self._input.returnPressed.connect(self._on_dock_send)
        inp_row.addWidget(self._input, stretch=1)

        self._attach_btn = QPushButton("FILE")
        self._attach_btn.setFixedHeight(28)
        self._attach_btn.setFont(keena_font(8, bold=True))
        self._attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._attach_btn.setToolTip("Attach or browse file")
        self._attach_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(35, 10, 32, 210);
                color: {C.TEXT_MED};
                border: 1px solid {C.BORDER_B};
                border-radius: 4px;
                padding: 2px 8px;
            }}
            QPushButton:hover {{
                border-color: {C.PRI};
                color: #ffffff;
            }}
        """)
        self._attach_btn.clicked.connect(self._main_win._browse_file)
        inp_row.addWidget(self._attach_btn)

        self._send_btn = QPushButton("SEND")
        self._send_btn.setFixedSize(50, 28)
        self._send_btn.setFont(keena_font(8, bold=True))
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 77, 166, 0.25);
                color: #ffffff;
                border: 1px solid {C.PRI};
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background: rgba(255, 77, 166, 0.45);
            }}
        """)
        self._send_btn.clicked.connect(self._on_dock_send)
        inp_row.addWidget(self._send_btn)
        lay.addLayout(inp_row)

    def _on_dock_send(self):
        txt = self._input.text().strip()
        if not txt:
            return
        if hasattr(self._main_win, "_input") and hasattr(self._main_win, "_send"):
            self._main_win._input.setText(txt)
            self._main_win._send()
        elif hasattr(self._main_win, "_send_text_command"):
            self._main_win._send_text_command(txt)
        self._input.clear()

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._log.hide()
            self.setFixedHeight(68)
            self._min_btn.setText("+")
        else:
            self._log.show()
            self.setFixedHeight(168)
            self._min_btn.setText("—")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.globalPosition().toPoint()
            self._win_start = self.pos()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_start and e.buttons() == Qt.MouseButton.LeftButton:
            delta = e.globalPosition().toPoint() - self._drag_start
            self.move(self._win_start + delta)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_start = None
        super().mouseReleaseEvent(e)


class LeftSearchDock(QWidget):
    """
    Floating pop-up panel for search results, briefings, document reviews,
    and quizzes, positioned along the left edge of the screen.
    """
    def __init__(self, main_win: "MainWindow", parent=None):
        super().__init__(parent)
        self._main_win = main_win
        self.setWindowTitle("JARVIS Search & Intel")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(380, 400)

        screen = QApplication.primaryScreen().availableGeometry()
        dock_w = min(480, max(380, screen.width() // 3))
        dock_h = min(580, screen.height() - 140)
        dock_x = screen.left() + 16
        dock_y = screen.top() + (screen.height() - dock_h) // 2
        self.setGeometry(dock_x, dock_y, dock_w, dock_h)

        self._drag_start = None
        self._win_start = None
        self._build_ui()

    def _build_ui(self):
        self._outer = QFrame(self)
        self._outer.setObjectName("SearchDockOuter")
        self._outer.setStyleSheet(f"""
            QFrame#SearchDockOuter {{
                background: rgba(14, 4, 13, 238);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._outer)

        lay = QVBoxLayout(self._outer)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(6)

        self._title_lbl = QLabel("SEARCH & INTEL MATRIX")
        self._title_lbl.setFont(keena_font(9, bold=True, spacing=1.2))
        self._title_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(self._title_lbl)
        hdr.addStretch()

        self._ts_lbl = QLabel("")
        self._ts_lbl.setFont(keena_font(8))
        self._ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._ts_lbl)

        dismiss = QPushButton("DISMISS")
        dismiss.setFont(keena_font(8, bold=True))
        dismiss.setFixedHeight(22)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 77, 166, 0.15);
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 0 8px;
            }}
            QPushButton:hover {{
                background: rgba(255, 77, 166, 0.35);
                border-color: {C.PRI};
                color: #ffffff;
            }}
        """)
        dismiss.clicked.connect(self.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        self._panel_cont = QWidget()
        self._panel_cont.setStyleSheet("background: transparent;")
        p_lay = QVBoxLayout(self._panel_cont)
        p_lay.setContentsMargins(0, 0, 0, 0)
        p_lay.setSpacing(0)
        p_lay.addWidget(self._main_win._content_panel)
        p_lay.addWidget(self._main_win._quiz_panel)
        lay.addWidget(self._panel_cont, stretch=1)

    def popup(self, title: str = "", timestamp: str = ""):
        if title:
            self._title_lbl.setText(f"SEARCH — {title.upper()[:32]}")
        if timestamp:
            self._ts_lbl.setText(timestamp)
        screen = QApplication.primaryScreen().availableGeometry()
        dock_w = min(480, max(380, screen.width() // 3))
        dock_h = min(580, screen.height() - 140)
        dock_x = screen.left() + 16
        dock_y = screen.top() + (screen.height() - dock_h) // 2
        self.setGeometry(dock_x, dock_y, dock_w, dock_h)
        self.show()
        self.raise_()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.globalPosition().toPoint()
            self._win_start = self.pos()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_start and e.buttons() == Qt.MouseButton.LeftButton:
            delta = e.globalPosition().toPoint() - self._drag_start
            self.move(self._win_start + delta)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_start = None
        super().mouseReleaseEvent(e)


class ArmouryCrateTelemetryWidget(QWidget):
    """
    ASUS ROG / TUF Armoury Crate inspired hardware telemetry matrix.
    Displays CPU, GPU, Fan Cooling, and Memory/Storage with dynamic gauges
    and interactive Operating Mode profiles (Windows, Silent, Performance, Turbo).
    """

    def __init__(self, main_win=None, parent=None):
        super().__init__(parent)
        self._main_win = main_win
        self._mode = "Performance"
        self.setStyleSheet("background: transparent;")
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 4)
        root.setSpacing(6)

        # ── Section 1: Header (Armoury Crate diagonal hazard slashes)
        sec1_hdr = QHBoxLayout()
        sec1_hdr.setContentsMargins(0, 0, 0, 0)
        sec1_hdr.setSpacing(6)
        slashes1 = QLabel("\\\\\\")
        slashes1.setFont(keena_font(9, bold=True))
        slashes1.setStyleSheet("color: #ff7a00; background: transparent;")
        sec1_hdr.addWidget(slashes1)
        t1 = QLabel("System Usage")
        t1.setFont(keena_font(9, bold=True, spacing=1.2))
        t1.setStyleSheet("color: #ffffff; background: transparent;")
        sec1_hdr.addWidget(t1)
        sec1_hdr.addStretch()
        root.addLayout(sec1_hdr)

        def _make_card(title: str):
            c = QFrame()
            c.setStyleSheet(f"""
                QFrame {{
                    background: rgba(18, 5, 18, 225);
                    border: 1px solid rgba(255, 77, 166, 0.28);
                    border-radius: 5px;
                }}
            """)
            cv = QVBoxLayout(c)
            cv.setContentsMargins(8, 6, 8, 6)
            cv.setSpacing(3)
            hdr = QHBoxLayout()
            hdr.setContentsMargins(0, 0, 0, 0)
            tl = QLabel(title)
            tl.setFont(keena_font(9, bold=True))
            tl.setStyleSheet("color: #ffffff; background: transparent;")
            hdr.addWidget(tl)
            hdr.addStretch()
            sl = QLabel("\\\\\\")
            sl.setFont(keena_font(7, bold=True))
            sl.setStyleSheet("color: #ff7a00; background: transparent;")
            hdr.addWidget(sl)
            cv.addLayout(hdr)
            return c, cv

        def _make_row(name: str):
            r = QHBoxLayout()
            r.setContentsMargins(0, 0, 0, 0)
            nl = QLabel(name)
            nl.setFont(keena_font(7))
            nl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            vl = QLabel("--")
            vl.setFont(keena_font(8, bold=True))
            vl.setStyleSheet("color: #ffffff; background: transparent;")
            r.addWidget(nl)
            r.addStretch()
            r.addWidget(vl)
            return r, vl

        def _make_bar(color="#ff7a00"):
            pb = QProgressBar()
            pb.setFixedHeight(4)
            pb.setTextVisible(False)
            pb.setRange(0, 100)
            pb.setValue(0)
            pb.setStyleSheet(f"""
                QProgressBar {{
                    background: rgba(255, 255, 255, 0.08);
                    border: none;
                    border-radius: 2px;
                }}
                QProgressBar::chunk {{
                    background: {color};
                    border-radius: 2px;
                }}
            """)
            return pb

        # ── 2x2 Grid of Cards
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        # 1. CPU Card
        c_cpu, v_cpu = _make_card("CPU")
        r_cf, self.lbl_cpu_freq = _make_row("Frequency")
        r_cu, self.lbl_cpu_usage = _make_row("Usage")
        self.bar_cpu = _make_bar("#ff7a00")
        r_cm, self.lbl_cpu_mem_freq = _make_row("Memory Frequency")
        r_ct, self.lbl_cpu_temp = _make_row("Temperature")
        r_cv, self.lbl_cpu_volt = _make_row("Voltage")
        v_cpu.addLayout(r_cf)
        v_cpu.addLayout(r_cu)
        v_cpu.addWidget(self.bar_cpu)
        v_cpu.addLayout(r_cm)
        v_cpu.addLayout(r_ct)
        v_cpu.addLayout(r_cv)
        grid.addWidget(c_cpu, 0, 0)

        # 2. GPU Card
        c_gpu, v_gpu = _make_card("GPU")
        r_gf, self.lbl_gpu_freq = _make_row("Frequency")
        r_gu, self.lbl_gpu_usage = _make_row("Usage")
        self.bar_gpu = _make_bar("#ff4da6")
        r_gm, self.lbl_gpu_mem_freq = _make_row("Memory Frequency")
        r_gt, self.lbl_gpu_temp = _make_row("Temperature")
        r_gv, self.lbl_gpu_volt = _make_row("Voltage")
        v_gpu.addLayout(r_gf)
        v_gpu.addLayout(r_gu)
        v_gpu.addWidget(self.bar_gpu)
        v_gpu.addLayout(r_gm)
        v_gpu.addLayout(r_gt)
        v_gpu.addLayout(r_gv)
        grid.addWidget(c_gpu, 0, 1)

        # 3. Fan Card
        c_fan, v_fan = _make_card("Fan")
        r_fc, self.lbl_fan_cpu = _make_row("CPU Fan")
        self.bar_fan_cpu = _make_bar("#ff7a00")
        r_fg, self.lbl_fan_gpu = _make_row("GPU Fan")
        self.bar_fan_gpu = _make_bar("#ff7a00")
        r_fa, self.lbl_fan_acoustics = _make_row("Fan Acoustics")
        v_fan.addLayout(r_fc)
        v_fan.addWidget(self.bar_fan_cpu)
        v_fan.addLayout(r_fg)
        v_fan.addWidget(self.bar_fan_gpu)
        v_fan.addLayout(r_fa)
        grid.addWidget(c_fan, 1, 0)

        # 4. Memory & Storage Card
        c_mem, v_mem = _make_card("Memory")
        r_ms, self.lbl_storage = _make_row("Storage")
        self.bar_storage = _make_bar("#00e5ff")
        r_mr, self.lbl_ram = _make_row("RAM")
        self.bar_ram = _make_bar("#ff4da6")
        r_mn, self.lbl_net = _make_row("Network I/O")
        v_mem.addLayout(r_ms)
        v_mem.addWidget(self.bar_storage)
        v_mem.addLayout(r_mr)
        v_mem.addWidget(self.bar_ram)
        v_mem.addLayout(r_mn)
        grid.addWidget(c_mem, 1, 1)

        root.addLayout(grid)

        # ── Section 2: Operating Mode
        sec2_hdr = QHBoxLayout()
        sec2_hdr.setContentsMargins(0, 4, 0, 0)
        sec2_hdr.setSpacing(6)
        slashes2 = QLabel("\\\\\\")
        slashes2.setFont(keena_font(9, bold=True))
        slashes2.setStyleSheet("color: #ff7a00; background: transparent;")
        sec2_hdr.addWidget(slashes2)
        t2 = QLabel("Operating Mode")
        t2.setFont(keena_font(9, bold=True, spacing=1.2))
        t2.setStyleSheet("color: #ffffff; background: transparent;")
        sec2_hdr.addWidget(t2)
        sec2_hdr.addStretch()
        root.addLayout(sec2_hdr)

        modes_row = QHBoxLayout()
        modes_row.setSpacing(6)
        self._mode_btns = {}
        for m_name in ["Windows®", "Silent", "Performance", "Turbo"]:
            b = QPushButton(m_name)
            b.setFont(keena_font(8, bold=True))
            b.setFixedHeight(30)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda checked=False, name=m_name: self.set_operating_mode(name))
            self._mode_btns[m_name] = b
            modes_row.addWidget(b)
        root.addLayout(modes_row)

        self.set_operating_mode("Performance")

    def set_operating_mode(self, mode: str):
        self._mode = mode
        for name, b in self._mode_btns.items():
            if name == mode:
                b.setStyleSheet("""
                    QPushButton {
                        background: rgba(255, 122, 0, 0.22);
                        color: #ffffff;
                        border: 1px solid #ff7a00;
                        border-radius: 4px;
                        padding: 4px 8px;
                    }
                """)
            else:
                b.setStyleSheet("""
                    QPushButton {
                        background: rgba(22, 6, 22, 0.65);
                        color: #d6a0be;
                        border: 1px solid rgba(255, 77, 166, 0.22);
                        border-radius: 4px;
                        padding: 4px 8px;
                    }
                    QPushButton:hover {
                        border-color: #ff7a00;
                        color: #ffffff;
                        background: rgba(255, 122, 0, 0.12);
                    }
                """)

    def update_metrics(self, snap: dict):
        cpu = snap.get("cpu", 0.0)
        gpu = snap.get("gpu", -1.0)
        tmp = snap.get("tmp", -1.0)
        net = snap.get("net", 0.0)

        # Real CPU Frequency
        try:
            cf = psutil.cpu_freq()
            cf_val = int(cf.current) if cf and cf.current else 2827
        except Exception:
            cf_val = 2827
        self.lbl_cpu_freq.setText(f"{cf_val} MHz")

        # CPU Usage
        self.lbl_cpu_usage.setText(f"{cpu:.0f} %")
        self.bar_cpu.setValue(int(max(0, min(100, cpu))))
        self.lbl_cpu_mem_freq.setText("4800 MT/s")

        # CPU Temp & Voltage
        c_temp = int(tmp if tmp > 0 else (46 + (cpu / 100.0) * 36))
        self.lbl_cpu_temp.setText(f"{c_temp} °C")
        self.lbl_cpu_volt.setText(f"{1.05 + (cpu / 100.0) * 0.31:.2f} V")

        # GPU
        if gpu >= 0:
            g_freq = int(210 + (gpu / 100.0) * 1650)
            self.lbl_gpu_freq.setText(f"{g_freq} MHz")
            self.lbl_gpu_usage.setText(f"{gpu:.0f} %")
            self.bar_gpu.setValue(int(max(0, min(100, gpu))))
            g_temp = int(tmp if tmp > 0 else (42 + (gpu / 100.0) * 32))
            self.lbl_gpu_temp.setText(f"{g_temp} °C")
            self.lbl_gpu_mem_freq.setText(f"{int(405 + (gpu / 100.0) * 1200)} MHz")
            self.lbl_gpu_volt.setText(f"{0.72 + (gpu / 100.0) * 0.38:.2f} V")
        else:
            self.lbl_gpu_freq.setText("210 MHz")
            self.lbl_gpu_usage.setText("0 %")
            self.bar_gpu.setValue(0)
            self.lbl_gpu_mem_freq.setText("405 MHz")
            self.lbl_gpu_temp.setText("42 °C")
            self.lbl_gpu_volt.setText("0.65 V")

        # Fan & Acoustics based on Mode
        if self._mode == "Silent":
            base_rpm = 1900
            acoustic = 22.0 + (cpu / 100.0) * 3.5
        elif self._mode == "Turbo":
            base_rpm = 3600
            acoustic = 34.0 + (cpu / 100.0) * 7.5
        elif self._mode == "Windows®":
            base_rpm = 2200
            acoustic = 23.5 + (cpu / 100.0) * 4.5
        else:  # Performance
            base_rpm = 2800
            acoustic = 25.5 + (cpu / 100.0) * 5.5

        cpu_fan = int(base_rpm + (cpu / 100.0) * 900)
        gpu_fan = int(max(1500, cpu_fan - 80))
        self.lbl_fan_cpu.setText(f"{cpu_fan} RPM")
        self.bar_fan_cpu.setValue(int(min(100, (cpu_fan / 5200.0) * 100)))
        self.lbl_fan_gpu.setText(f"{gpu_fan} RPM")
        self.bar_fan_gpu.setValue(int(min(100, (gpu_fan / 5200.0) * 100)))
        self.lbl_fan_acoustics.setText(f"{acoustic:.1f} dBA")

        # Storage & RAM
        try:
            drive = "C:\\" if os.name == "nt" else "/"
            du = psutil.disk_usage(drive)
            s_used = du.used / (1024**3)
            s_total = du.total / (1024**3)
            s_pct = du.percent
        except Exception:
            s_used, s_total, s_pct = 105.7, 446.4, 23.7
        self.lbl_storage.setText(f"{s_used:.1f} / {s_total:.1f} GB")
        self.bar_storage.setValue(int(s_pct))

        try:
            vm = psutil.virtual_memory()
            r_used = vm.used / (1024**3)
            r_total = vm.total / (1024**3)
            r_pct = vm.percent
        except Exception:
            r_used, r_total, r_pct = 9.0, 15.8, 57.0
        self.lbl_ram.setText(f"{r_used:.1f} / {r_total:.1f} GB")
        self.bar_ram.setValue(int(r_pct))

        # Network
        if net < 1.0:
            net_str = f"{net * 1024:.0f} KB/s"
        else:
            net_str = f"{net:.1f} MB/s"
        self.lbl_net.setText(net_str)


class MinimizedDisplayPill(QPushButton):
    def __init__(self, display_id: int, title: str, content_type: str = "image", parent=None):
        super().__init__(parent)
        self.display_id = display_id
        self.display_title = title or f"Display {display_id}"
        self.content_type = content_type
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

        self.setToolTip(f"Display {self.display_id}: {self.display_title}\n(Click to open)")

        self.setFixedHeight(34)
        self._collapsed_w = 40
        self._expanded_w = min(280, max(160, 50 + len(self.display_title) * 8))
        self.setFixedWidth(self._collapsed_w)

        self._is_hovered = False
        self._update_style()
        self._update_text()

    def _update_text(self):
        if self._is_hovered:
            clean_title = self.display_title.strip()
            if len(clean_title) > 24:
                clean_title = clean_title[:22] + "..."
            self.setText(f" {self.display_id} │ {clean_title} ")
        else:
            self.setText(str(self.display_id))

    def _update_style(self):
        if self._is_hovered:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 rgba(40, 10, 35, 250), stop:1.0 rgba(14, 4, 12, 240));
                    color: #ffffff;
                    border: 1.5px solid #ffd700;
                    border-left: 3px solid #ffd700;
                    border-top-right-radius: 6px;
                    border-bottom-right-radius: 6px;
                    border-top-left-radius: 0px;
                    border-bottom-left-radius: 0px;
                    font-family: 'Courier New';
                    font-size: 11px;
                    font-weight: bold;
                    text-align: left;
                    padding-left: 8px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(18, 5, 16, 235);
                    color: {C.PRI};
                    border: 1.5px solid {C.PRI};
                    border-left: 3px solid {C.PRI};
                    border-top-right-radius: 6px;
                    border-bottom-right-radius: 6px;
                    border-top-left-radius: 0px;
                    border-bottom-left-radius: 0px;
                    font-family: 'Courier New';
                    font-size: 12px;
                    font-weight: bold;
                    text-align: center;
                }}
            """)

    def enterEvent(self, e):
        super().enterEvent(e)
        self._is_hovered = True
        self.setFixedWidth(self._expanded_w)
        self._update_text()
        self._update_style()
        if self.parentWidget() and hasattr(self.parentWidget(), "recalculate_size"):
            self.parentWidget().recalculate_size()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._is_hovered = False
        self.setFixedWidth(self._collapsed_w)
        self._update_text()
        self._update_style()
        if self.parentWidget() and hasattr(self.parentWidget(), "recalculate_size"):
            self.parentWidget().recalculate_size()


class MinimizedDisplaysDock(QWidget):
    dot_clicked = pyqtSignal(int)

    def __init__(self, parent_main_win=None):
        super().__init__(None)
        self._main_win = parent_main_win
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._vbox = QVBoxLayout(self)
        self._vbox.setContentsMargins(0, 6, 6, 6)
        self._vbox.setSpacing(6)
        self._vbox.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self._buttons: dict[int, MinimizedDisplayPill] = {}

    def update_displays(self, display_items: list[tuple[int, str, str]]):
        while self._vbox.count():
            item = self._vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._buttons.clear()

        if not display_items:
            self.hide()
            return

        for disp_id, disp_title, c_type in sorted(display_items, key=lambda x: x[0]):
            pill = MinimizedDisplayPill(disp_id, disp_title, c_type, self)
            pill.clicked.connect(lambda _, did=disp_id: self.dot_clicked.emit(did))
            self._vbox.addWidget(pill)
            self._buttons[disp_id] = pill

        self._vbox.activate()
        self.show()
        self.recalculate_size()
        self.raise_()

    def recalculate_size(self):
        if not self._buttons:
            return
        max_w = max((p.width() for p in self._buttons.values()), default=40) + 8
        pill_count = len(self._buttons)
        total_h = pill_count * 34 + (pill_count - 1) * 6 + 12
        self.resize(max_w, total_h)
        self.reposition()

    def reposition(self):
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.left()
        y = max(screen.top() + 60, screen.center().y() - self.height() // 2)
        self.move(x, y)


MinimizedDisplaysOverlay = MinimizedDisplaysDock


class JarvisDisplayWindow(QWidget):
    closed = pyqtSignal(int)
    minimized_requested = pyqtSignal(int)
    _img_data_sig = pyqtSignal(bytes)

    def __init__(self, display_id: int, name: str, content_type: str, source: str, title: str = "", parent=None):
        super().__init__(parent)
        self.display_id = display_id
        self.display_name = name
        self.content_type = (content_type or "image").lower()
        self.source = source or ""
        self.title_text = title or (Path(source).name if (source and os.path.exists(source)) else (source[:36] if source else "MEDIA"))

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.SubWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(680, 500)
        self.setMinimumSize(320, 240)
        self._drag_start = None

        self._outer = QWidget(self)
        self._outer.setObjectName("DisplayOuter")
        self._outer.setStyleSheet(f"""
            QWidget#DisplayOuter {{
                background: rgba(10, 3, 10, 245);
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)

        main_v = QVBoxLayout(self)
        main_v.setContentsMargins(0, 0, 0, 0)
        main_v.addWidget(self._outer)

        v = QVBoxLayout(self._outer)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(2, 0, 2, 0)
        hdr.setSpacing(8)

        tag_lbl = QLabel(f"{self.display_name} // {self.title_text.upper()}")
        tag_lbl.setFont(keena_font(8, bold=True, spacing=1.2))
        tag_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(tag_lbl)
        hdr.addStretch()

        self._min_btn = QPushButton("MIN")
        self._min_btn.setFont(keena_font(7, bold=True))
        self._min_btn.setFixedSize(32, 20)
        self._min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._min_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(30, 8, 26, 210);
                color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
            }}
            QPushButton:hover {{
                border-color: {C.PRI};
                color: #ffffff;
            }}
        """)
        self._min_btn.clicked.connect(self._on_min_btn_clicked)
        hdr.addWidget(self._min_btn)

        close_btn = QPushButton("X")
        close_btn.setFont(keena_font(8, bold=True))
        close_btn.setFixedSize(22, 20)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(35, 8, 20, 210);
                color: #ff6688;
                border: 1px solid rgba(255, 102, 136, 0.4);
                border-radius: 3px;
            }}
            QPushButton:hover {{
                background: rgba(255, 51, 102, 0.45);
                border-color: #ff3366;
                color: #ffffff;
            }}
        """)
        close_btn.clicked.connect(self.close)
        hdr.addWidget(close_btn)
        v.addLayout(hdr)

        self._content_cont = QWidget()
        self._content_cont.setStyleSheet("background: #020003; border-radius: 4px;")
        cont_lay = QVBoxLayout(self._content_cont)
        cont_lay.setContentsMargins(4, 4, 4, 4)

        if self.content_type == "video" and HAVE_VIDEO:
            self._setup_video(cont_lay)
        elif self.content_type == "text":
            self._setup_text(cont_lay)
        else:
            self._setup_image(cont_lay)

        v.addWidget(self._content_cont, stretch=1)

    def _setup_text(self, lay: QVBoxLayout):
        self._text_edit = QTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setFrameShape(QFrame.Shape.NoFrame)
        self._text_edit.setFont(QFont("Courier New", 10))
        self._text_edit.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                color: {C.TEXT};
                border: none;
                padding: 10px 12px;
                selection-background-color: rgba(255, 77, 166, 0.35);
            }}
            QScrollBar:vertical {{
                background: rgba(14, 4, 12, 120);
                width: 6px;
                border: none;
                margin: 0px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 77, 166, 0.45);
                border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {C.PRI};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        raw_text = self.source
        if os.path.exists(self.source) and os.path.isfile(self.source):
            try:
                with open(self.source, "r", encoding="utf-8", errors="replace") as f:
                    raw_text = f.read()
            except Exception:
                pass

        if hasattr(self._text_edit, "setMarkdown"):
            self._text_edit.setMarkdown(raw_text)
        else:
            self._text_edit.setPlainText(raw_text)
        lay.addWidget(self._text_edit)

    def _setup_image(self, lay: QVBoxLayout):
        self._img_data_sig.connect(self._on_image_bytes_loaded)
        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        self._img_lbl.setText("FETCHING VISUAL FEED // INTEL ACTIVE...")
        self._img_lbl.setFont(keena_font(8, bold=True))
        lay.addWidget(self._img_lbl)
        self._load_image()

    def _load_image(self):
        if not self.source:
            self._img_lbl.setText("NO IMAGE SOURCE SPECIFIED")
            return
        if os.path.exists(self.source):
            px = QPixmap(self.source)
            if not px.isNull():
                self._original_pixmap = px
                self._update_scaled_image()
                return

        # URL or remote source: download in background thread
        t = threading.Thread(target=self._download_image_worker, daemon=True)
        t.start()

    def _download_image_worker(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        }
        url = self.source
        # Fix Wikipedia thumb URL if present
        if "upload.wikimedia.org" in url and "/thumb/" in url:
            m = re.match(r"(https?://upload\.wikimedia\.org/wikipedia/[^/]+)/thumb/([^/]+/[^/]+/[^/]+)/.*", url)
            if m:
                url = f"{m.group(1)}/{m.group(2)}"

    def _download_image_bytes(self, url: str, headers: dict, timeout: int = 5) -> bytes | None:
        try:
            import urllib.request
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return resp.read()
        except Exception:
            pass
        return None

    def _search_image_fallback(self, query: str, headers: dict) -> bytes | None:
        try:
            from actions.web_search import _get_ddgs
            DDGS = _get_ddgs()
            with DDGS() as ddgs:
                for r in ddgs.images(query, max_results=3):
                    img_url = r.get("image")
                    if not img_url:
                        continue
                    data = self._download_image_bytes(img_url, headers, timeout=4)
                    if data:
                        return data
        except Exception as e:
            print(f"[JarvisDisplayWindow] Fallback search error: {e}")
        return None

    def _fetch_image_thread(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        }
        url = self.source
        # Fix Wikipedia thumb URL if present
        if "upload.wikimedia.org" in url and "/thumb/" in url:
            m = re.match(r"(https?://upload\.wikimedia\.org/wikipedia/[^/]+)/thumb/([^/]+/[^/]+/[^/]+)/.*", url)
            if m:
                url = f"{m.group(1)}/{m.group(2)}"

        data = None
        if url.startswith(("http://", "https://")):
            data = self._download_image_bytes(url, headers, timeout=6)

        # If data fetch failed, search DDG for image
        if not data:
            query = self.title_text or Path(self.source.split("?")[0]).stem.replace("_", " ")
            if query and not query.lower().startswith("display"):
                data = self._search_image_fallback(query, headers)

        self._img_data_sig.emit(data or b"")

    def _on_image_bytes_loaded(self, data: bytes):
        if data:
            px = QPixmap()
            px.loadFromData(data)
            if not px.isNull():
                self._original_pixmap = px
                self._update_scaled_image()
                return
        self._img_lbl.setText("IMAGE RETRIEVAL FAILED // RE-TRY WITH SEARCH")
        self._img_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; font-family: 'Courier New';")

    def _update_scaled_image(self):
        if hasattr(self, "_original_pixmap") and not self._original_pixmap.isNull():
            w = max(100, self._content_cont.width() - 10)
            h = max(100, self._content_cont.height() - 10)
            scaled = self._original_pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._img_lbl.setPixmap(scaled)

    def _setup_video(self, lay: QVBoxLayout):
        try:
            self._vid_player = QMediaPlayer(self)
            self._vid_audio = QAudioOutput(self)
            self._vid_player.setAudioOutput(self._vid_audio)
            self._vid_audio.setMuted(True)

            self._scene = QGraphicsScene(self)
            self._view = QGraphicsView(self._scene, self)
            self._view.setStyleSheet("background: #000000; border: none;")
            self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._vid_item = QGraphicsVideoItem()
            self._scene.addItem(self._vid_item)
            self._vid_player.setVideoOutput(self._vid_item)

            lay.addWidget(self._view, stretch=1)

            c_bar = QHBoxLayout()
            c_bar.setContentsMargins(4, 2, 4, 2)
            c_bar.setSpacing(6)

            self._play_btn = QPushButton("PAUSE")
            self._play_btn.setFont(keena_font(7, bold=True))
            self._play_btn.setFixedSize(48, 20)
            self._play_btn.setStyleSheet(f"background: rgba(30, 8, 26, 210); color: #fff; border: 1px solid {C.PRI}; border-radius: 3px;")
            self._play_btn.clicked.connect(self._toggle_playback)
            c_bar.addWidget(self._play_btn)

            self._mute_btn = QPushButton("SOUND OFF")
            self._mute_btn.setFont(keena_font(7, bold=True))
            self._mute_btn.setFixedSize(68, 20)
            self._mute_btn.setStyleSheet(f"background: rgba(30, 8, 26, 210); color: #fff; border: 1px solid {C.PRI}; border-radius: 3px;")
            self._mute_btn.clicked.connect(self._toggle_sound)
            c_bar.addWidget(self._mute_btn)
            c_bar.addStretch()

            lay.addLayout(c_bar)

            if os.path.exists(self.source):
                self._vid_player.setSource(QUrl.fromLocalFile(self.source))
            else:
                self._vid_player.setSource(QUrl(self.source))
            self._vid_player.play()
        except Exception as e:
            print(f"[JarvisDisplayWindow] Video setup error: {e}")
            self._setup_image(lay)

    def _toggle_playback(self):
        if hasattr(self, "_vid_player"):
            if self._vid_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._vid_player.pause()
                self._play_btn.setText("PLAY")
            else:
                self._vid_player.play()
                self._play_btn.setText("PAUSE")

    def _toggle_sound(self):
        if hasattr(self, "_vid_audio"):
            is_muted = not self._vid_audio.isMuted()
            self._vid_audio.setMuted(is_muted)
            self._mute_btn.setText("SOUND OFF" if is_muted else "SOUND ON")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "_original_pixmap"):
            self._update_scaled_image()
        if hasattr(self, "_view") and hasattr(self, "_vid_item"):
            w = self._view.width()
            h = self._view.height()
            self._scene.setSceneRect(0, 0, w, h)
            self._vid_item.setSize(QSizeF(w, h))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.globalPosition().toPoint()
            self._win_start = self.pos()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if getattr(self, "_drag_start", None) and e.buttons() == Qt.MouseButton.LeftButton:
            delta = e.globalPosition().toPoint() - self._drag_start
            self.move(self._win_start + delta)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_start = None
        super().mouseReleaseEvent(e)

    def _on_min_btn_clicked(self):
        self.hide()
        self.minimized_requested.emit(self.display_id)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self.setWindowState(Qt.WindowState.WindowNoState)
            self.hide()
            self.minimized_requested.emit(self.display_id)

    def closeEvent(self, e):
        if hasattr(self, "_vid_player"):
            try:
                self._vid_player.stop()
            except Exception:
                pass
        self.closed.emit(self.display_id)
        super().closeEvent(e)


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _codejob_sig    = pyqtSignal(object)     # dev_agent job state — live coding panel
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _video_open_sig  = pyqtSignal(str, str, bool, str)  # video, title, muted, audio
    _wake_btns_sig   = pyqtSignal()          # wake state resolved off-thread
    _video_close_sig = pyqtSignal()
    _video_mute_sig  = pyqtSignal(bool)
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _mute_sig       = pyqtSignal(bool)       # thread-safe mute state toggle
    _confirm_sig    = pyqtSignal(str, str)   # (title, detail) — irreversible-action gate
    _confirm_hide_sig = pyqtSignal()
    _wake_dl_sig    = pyqtSignal(bool, str)  # wake-word install finished (ok, message)
    _quiz_sig       = pyqtSignal(str, object, object)  # (topic, questions, grader)
    _quiz_hide_sig  = pyqtSignal()
    _review_sig     = pyqtSignal(str, str, object, object)  # document review payload
    _open_display_sig = pyqtSignal(str, str, str)   # (content_type, source, encoded_title)
    _focus_display_sig = pyqtSignal(int)            # (display_id)
    _close_display_sig = pyqtSignal(int)            # (display_id)
    _stream_user_sig = pyqtSignal(str)              # (streamed user transcript)
    _stream_reply_sig = pyqtSignal(str)             # (streamed assistant transcript)

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "JARVIS").strip()
        _display = self._assistant_name.upper()

        # Apply the saved UI colour BEFORE panels/stylesheets are built
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(_ui_color)

        self.setWindowTitle(f"{_display} — {APP_VERSION}")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(280, 380)
        self._is_closed: bool = False
        self._expanded: bool = False
        self._is_fullscreen: bool = False

        screen = QApplication.primaryScreen().availableGeometry()
        hud_w, hud_h = 420, 580
        init_x = max(screen.left() + 20, screen.right() - hud_w - 24)
        init_y = max(screen.top() + 20, screen.bottom() - hud_h - 24)
        self.setGeometry(init_x, init_y, hud_w, hud_h)

        self.on_text_command   = None
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop JARVIS mid-speech
        self.on_voice_change   = None   # callable: () -> None — rebuild session with new voice
        self.on_audio_device_change = None  # callable: () -> None — reopen audio streams
        self._confirm_overlay  = None   # live ConfirmBanner, if one is on screen
        self.get_plugins       = None   # callable: () -> list[dict], set by JarvisLive
        self.get_plugin_settings = None # callable: () -> list[dict] settings schemas, set by JarvisLive
        self.on_wake_toggle    = None   # callable: (enable: bool) -> str, set by JarvisLive
        self.on_wake_manual    = None   # callable: () -> None — manual sleep/wake
        self.on_push_to_talk   = None   # callable: (enable: bool) -> str scope
        self.ptt_hold          = None   # callable: (held: bool) -> None — windowed chord
        self.wake_get_state    = None   # callable: () -> dict {enabled, awake, ready}
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None

        self._left_panel = self._build_left_panel()
        self._right_panel = self._build_right_panel()

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud.reactor_clicked.connect(self.toggle_minimize_top)
        self._content_panel = self._build_content_panel()
        self._quiz_panel = self._build_quiz_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet("background: #000308;")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED")
        _cam_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("✕  CLOSE")
        _cam_x.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # ── Video surface ────────────────────────────────────────────────
        self._video_split = False        # is the sound a separate stream?
        self._video_auto_muted = False   # did JARVIS close the mic, or the user?
        self._video_on = False
        self._video_cont = QWidget()
        self._video_cont.setStyleSheet(f"background: {C.BG};")
        _vid_v = QVBoxLayout(self._video_cont)
        _vid_v.setContentsMargins(8, 6, 8, 8)
        _vid_v.setSpacing(4)

        _vid_hdr = QHBoxLayout()
        self._video_title = QLabel("▶  VIDEO")
        self._video_title.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._video_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _vid_hdr.addWidget(self._video_title)
        _vid_hdr.addStretch()

        def _vid_btn(text: str) -> QPushButton:
            b = QPushButton(text)
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    color: {C.TEXT_DIM}; background: transparent;
                    border: none; padding: 2px 6px;
                }}
                QPushButton:hover {{ color: {C.PRI}; }}
            """)
            return b

        self._video_mute_btn = _vid_btn("🔇  SOUND OFF")
        self._video_mute_btn.clicked.connect(self._toggle_video_mute)
        _vid_hdr.addWidget(self._video_mute_btn)

        _vid_x = _vid_btn("✕  CLOSE")
        _vid_x.clicked.connect(self.stop_video)
        _vid_hdr.addWidget(_vid_x)
        _vid_v.addLayout(_vid_hdr)

        if HAVE_VIDEO:
            self._video_scene = QGraphicsScene(self)
            self._video_item = QGraphicsVideoItem()
            self._video_scene.addItem(self._video_item)
            self._video_widget = QGraphicsView(self._video_scene)
            self._video_widget.setStyleSheet("background: #000; border: none;")
            self._video_widget.setFrameShape(QGraphicsView.Shape.NoFrame)
            self._video_widget.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._video_widget.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._video_widget.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            _vid_v.addWidget(self._video_widget, stretch=1)

            self._video_audio = QAudioOutput()
            self._video_audio.setMuted(True)
            self._video_player = QMediaPlayer()
            self._video_player.setVideoOutput(self._video_item)
            self._video_item.nativeSizeChanged.connect(self._fit_video)
            self._video_player.setAudioOutput(self._video_audio)
            self._video_player.errorOccurred.connect(self._on_video_error)

            self._video_sound = QMediaPlayer()
            self._video_sound_out = QAudioOutput()
            self._video_sound_out.setMuted(True)
            self._video_sound.setAudioOutput(self._video_sound_out)

            self._video_sync = QTimer(self)
            self._video_sync.setInterval(1000)
            self._video_sync.timeout.connect(self._sync_video_sound)
        else:
            self._video_scene = None
            self._video_item = None
            self._video_widget = None
            self._video_player = None
            self._video_audio = None
            self._video_sound = None
            self._video_sound_out = None
            self._video_sync = None
            _miss = QLabel("Video playback is not available in this Qt install.")
            _miss.setAlignment(Qt.AlignmentFlag.AlignCenter)
            _miss.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            _vid_v.addWidget(_miss, stretch=1)

        # Stack: 0 = animated HUD, 1 = live camera, 2 = video
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)
        self._hud_cam_stack.addWidget(self._video_cont)

        # ── Compact HUD container (only the compact HUD!)
        self.hud_box = QWidget()
        self.hud_box.setFixedWidth(420)
        self.hud_box.setStyleSheet("background: transparent;")
        hud_v = QVBoxLayout(self.hud_box)
        hud_v.setContentsMargins(0, 0, 0, 0)
        hud_v.setSpacing(0)
        hud_v.addWidget(self._hud_cam_stack, stretch=1)
        self.hud_ctrl_bar = self._build_hud_controls_bar()
        hud_v.addWidget(self.hud_ctrl_bar, stretch=0)

        # ── Expanded Cyber Drawer (attached directly to JARVIS window)
        self._drawer = self._build_cyber_drawer()
        self._drawer.setVisible(False)

        central = QWidget()
        central.setStyleSheet("background: transparent;")
        self.setCentralWidget(central)

        self._main_h = QHBoxLayout(central)
        self._main_h.setContentsMargins(0, 0, 0, 0)
        self._main_h.setSpacing(8)
        self._main_h.addWidget(self._drawer, stretch=1)
        self._main_h.addWidget(self.hud_box, stretch=0)

        # Edge-docked components:
        # 1. Chat log at bottom center of screen (long width, short height)
        self.bottom_chat_dock = BottomChatDock(self)

        # 2. Search display popping up from left edge
        self.left_search_dock = LeftSearchDock(self)

        # Arc Reactor minimize tracking
        self._is_minimized_top = False
        self._normal_geom = self.geometry()
        self._active_corner = "BR"

        # Dummy compatibility references
        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._quick_drawer = self._build_quick_drawer()
        self._ctrl_drawer = self._build_controls_drawer()
        self._warm_wake_state()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metric update timer
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._on_log_line)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._codejob_sig.connect(self._show_code_job)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._wake_btns_sig.connect(self._refresh_wake_btns)
        self._video_open_sig.connect(self._on_video_open)
        self._video_close_sig.connect(self._on_video_close)
        self._video_mute_sig.connect(self._on_video_mute)
        self._mute_sig.connect(self._set_muted)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._wake_dl_sig.connect(self._on_wake_install_done)
        self._quiz_sig.connect(self._show_quiz)
        self._quiz_hide_sig.connect(self._hide_quiz)
        self._review_sig.connect(self._show_review)
        self._stream_user_sig.connect(self._on_stream_user)
        self._stream_reply_sig.connect(self._on_stream_reply)
        self._displays: dict[int, JarvisDisplayWindow] = {}
        self._display_counter: int = 0
        self._minimized_display_ids: set[int] = set()
        self._dots_overlay = MinimizedDisplaysDock(self)
        self._dots_overlay.dot_clicked.connect(self._on_restore_display)
        self._open_display_sig.connect(self._on_open_display)
        self._focus_display_sig.connect(self._on_focus_display)
        self._close_display_sig.connect(self._on_close_display_win)
        self._cam_stop = threading.Event()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)

    def showEvent(self, event):
        super().showEvent(event)
        # Chat box is popped up ONLY if selected by the user. Do not auto-show on start.

    def closeEvent(self, event):
        self._is_closed = True
        if hasattr(self, "_dots_overlay"):
            self._dots_overlay.close()
        if hasattr(self, "_displays"):
            for w in list(self._displays.values()):
                try:
                    w.close()
                except Exception:
                    pass
            self._displays.clear()
            self._minimized_display_ids.clear()
        if hasattr(self, "bottom_chat_dock"):
            self.bottom_chat_dock.close()
        if hasattr(self, "left_search_dock"):
            self.left_search_dock.close()
        super().closeEvent(event)

    def toggle_minimize_top(self):
        """
        Minimizes ONLY the circular Arc Reactor element to the left-bottom of the screen
        into a small favicon-sized floating orb, or restores it back to normal position.
        Triggered by clicking the Arc Reactor or the ◀ MINI button.
        """
        screen = QApplication.primaryScreen().availableGeometry()
        MINI_SIZE = 58  # Favicon-sized circular orb

        if getattr(self, "_is_minimized_top", False):
            # Restore to normal position at bottom-right
            self.hud._mini_mode = False
            self.setMinimumSize(280, 380)
            if hasattr(self, "hud_box"):
                self.hud_box.setFixedWidth(420)
            if hasattr(self, "hud_ctrl_bar"):
                self.hud_ctrl_bar.setVisible(True)
            target = getattr(self, "_normal_geom", None)
            if not target:
                init_x = max(screen.left() + 20, screen.right() - 420 - 24)
                init_y = max(screen.top() + 20, screen.bottom() - 580 - 24)
                target = QRect(init_x, init_y, 420, 580)
            self._is_minimized_top = False
            if hasattr(self, "_hud_mini_btn"):
                self._hud_mini_btn.setText("MINI")
                self._hud_mini_btn.setToolTip("Minimize Arc Reactor circle to bottom-left (Favicon size)")
        else:
            if getattr(self, "_expanded", False):
                self._toggle_expanded(False)
            self._normal_geom = self.geometry()
            self.hud._mini_mode = True
            if hasattr(self, "hud_ctrl_bar"):
                self.hud_ctrl_bar.setVisible(False)
            if hasattr(self, "hud_box"):
                self.hud_box.setFixedWidth(MINI_SIZE)
            self.setMinimumSize(MINI_SIZE, MINI_SIZE)
            target_x = screen.left() + 24
            target_y = screen.bottom() - MINI_SIZE - 24
            target = QRect(target_x, target_y, MINI_SIZE, MINI_SIZE)
            self._is_minimized_top = True
            if hasattr(self, "_hud_mini_btn"):
                self._hud_mini_btn.setText("NORM")
                self._hud_mini_btn.setToolTip("Restore Arc Reactor to normal position")

        self._geom_anim = QPropertyAnimation(self, b"geometry")
        self._geom_anim.setDuration(300)
        self._geom_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._geom_anim.setStartValue(self.geometry())
        self._geom_anim.setEndValue(target)
        self._geom_anim.start()

    def snap_to_corner(self, corner: str):
        """
        Smoothly moves JARVIS to one of the 4 screen corners:
        'TL' (Top-Left), 'TR' (Top-Right), 'BL' (Bottom-Left), 'BR' (Bottom-Right).
        """
        screen = QApplication.primaryScreen().availableGeometry()
        pad = 24
        w = self.width()
        h = self.height()
        corner = corner.upper().strip()

        if corner == "TL":
            target_x = screen.left() + pad
            target_y = screen.top() + pad
        elif corner == "TR":
            target_x = screen.right() - w - pad
            target_y = screen.top() + pad
        elif corner == "BL":
            target_x = screen.left() + pad
            target_y = screen.bottom() - h - pad
        else:  # 'BR' default
            target_x = screen.right() - w - pad
            target_y = screen.bottom() - h - pad

        self._active_corner = corner
        target_pos = QPoint(target_x, target_y)

        self._pos_anim = QPropertyAnimation(self, b"pos")
        self._pos_anim.setDuration(280)
        self._pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pos_anim.setStartValue(self.pos())
        self._pos_anim.setEndValue(target_pos)
        self._pos_anim.start()

    def cycle_corners(self):
        """Cycles smoothly through the 4 corners: BR -> BL -> TL -> TR -> BR."""
        order = ["BR", "BL", "TL", "TR"]
        curr = getattr(self, "_active_corner", "BR")
        try:
            next_idx = (order.index(curr) + 1) % len(order)
        except ValueError:
            next_idx = 0
        self.snap_to_corner(order[next_idx])

    def maximize_display(self):
        """Maximizes JARVIS to fullscreen mode or restores it."""
        self._toggle_fullscreen()

    def expand_display(self):
        """Expands the attached Cyber Drawer."""
        self._toggle_expanded(True)

    def collapse_display(self):
        """Collapses the Cyber Drawer into the compact HUD."""
        self._toggle_expanded(False)

    def close_display(self):
        """Closes the JARVIS display cleanly."""
        self.close()

    def toggle_chat_dock(self):
        """Toggle bottom chat dock visibility."""
        if hasattr(self, "bottom_chat_dock"):
            if self.bottom_chat_dock.isVisible():
                self.bottom_chat_dock.hide()
            else:
                self.bottom_chat_dock.show()
                self.bottom_chat_dock.raise_()

    def _on_open_display(self, content_type: str, source: str, encoded_title: str):
        if ":" in encoded_title:
            id_str, title = encoded_title.split(":", 1)
            try:
                disp_id = int(id_str)
            except ValueError:
                self._display_counter += 1
                disp_id = self._display_counter
        else:
            self._display_counter += 1
            disp_id = self._display_counter
            title = encoded_title

        name = f"DISPLAY {disp_id}"
        win = JarvisDisplayWindow(disp_id, name, content_type, source, title)
        self._displays[disp_id] = win
        win.closed.connect(self._on_display_closed)
        win.minimized_requested.connect(self._on_display_minimized)

        screen = QApplication.primaryScreen().availableGeometry()
        offset = ((disp_id - 1) % 6) * 36
        x = max(screen.left() + 20, min(screen.right() - win.width() - 20, screen.center().x() - win.width() // 2 + offset))
        y = max(screen.top() + 20, min(screen.bottom() - win.height() - 20, screen.center().y() - win.height() // 2 + offset))
        win.move(x, y)
        win.show()
        win.raise_()
        win.activateWindow()
        self._on_log_line(f"SYS: Opened {name} ({content_type.upper()})")
        return disp_id

    def _on_display_minimized(self, d_id: int):
        self._minimized_display_ids.add(d_id)
        self._update_dots_overlay()

    def _on_restore_display(self, d_id: int):
        self._minimized_display_ids.discard(d_id)
        win = self._displays.get(d_id)
        if win:
            win.setWindowState(Qt.WindowState.WindowNoState)
            win.show()
            win.raise_()
            win.activateWindow()
        self._update_dots_overlay()

    def _on_display_closed(self, d_id: int):
        self._minimized_display_ids.discard(d_id)
        self._displays.pop(d_id, None)
        self._update_dots_overlay()

    def _update_dots_overlay(self):
        items = []
        for did in sorted(list(self._minimized_display_ids)):
            win = self._displays.get(did)
            title = win.title_text if win else f"Display {did}"
            c_type = win.content_type if win else "image"
            items.append((did, title, c_type))
        self._dots_overlay.update_displays(items)

    def _update_dots_overlay_position(self):
        if hasattr(self, "_dots_overlay") and self._dots_overlay.isVisible():
            self._dots_overlay.reposition()

    def _on_focus_display(self, display_id: int):
        if display_id in self._displays:
            self._minimized_display_ids.discard(display_id)
            self._update_dots_overlay()
            win = self._displays[display_id]
            screen = QApplication.primaryScreen().availableGeometry()
            cx = max(screen.left() + 20, screen.center().x() - win.width() // 2)
            cy = max(screen.top() + 20, screen.center().y() - win.height() // 2)
            win.move(cx, cy)
            win.setWindowState(Qt.WindowState.WindowNoState)
            win.show()
            win.raise_()
            win.activateWindow()
            self._on_log_line(f"SYS: Focused DISPLAY {display_id} in center of screen")
            return True
        self._on_log_line(f"SYS: DISPLAY {display_id} not found")
        return False

    def _on_close_display_win(self, display_id: int):
        if display_id == 0:
            for w in list(self._displays.values()):
                try:
                    w.close()
                except Exception:
                    pass
            self._displays.clear()
            self._minimized_display_ids.clear()
            self._update_dots_overlay()
            self._on_log_line("SYS: Closed all display windows")
            return True
        elif display_id in self._displays:
            self._minimized_display_ids.discard(display_id)
            try:
                self._displays[display_id].close()
            except Exception:
                pass
            self._update_dots_overlay()
            self._on_log_line(f"SYS: Closed DISPLAY {display_id}")
            return True
        return False

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            self._hud_cam_stack.setCurrentIndex(0)
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j
                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    def _build_hud_controls_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(6, 0, 6, 4)
        lay.setSpacing(4)

        def _btn(txt, tip):
            b = QPushButton(txt)
            b.setFont(keena_font(8, True))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(30, 8, 26, 210);
                    color: {C.TEXT};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 4px;
                    padding: 3px 8px;
                }}
                QPushButton:hover {{
                    background: rgba(255, 77, 166, 0.25);
                    border: 1px solid {C.PRI};
                    color: #ffffff;
                }}
                QPushButton:pressed {{
                    background: rgba(255, 77, 166, 0.45);
                }}
            """)
            return b

        self._hud_mute_btn = _btn("MIC", "Mute/Unmute microphone (F4)")
        self._hud_mute_btn.clicked.connect(self._toggle_mute)
        lay.addWidget(self._hud_mute_btn)

        self._hud_exp_btn = _btn("EXPAND", "Expand attached JARVIS Intelligence Matrix")
        self._hud_exp_btn.clicked.connect(lambda: self._toggle_expanded())
        lay.addWidget(self._hud_exp_btn)

        self._hud_chat_btn = _btn("CHAT", "Toggle Bottom Chat Log Dock")
        self._hud_chat_btn.clicked.connect(self.toggle_chat_dock)
        lay.addWidget(self._hud_chat_btn)

        self._hud_mini_btn = _btn("MINI", "Minimize Arc Reactor to bottom-left (Favicon size)")
        self._hud_mini_btn.clicked.connect(self.toggle_minimize_top)
        lay.addWidget(self._hud_mini_btn)

        self._hud_close_btn = _btn("CLOSE", "Close JARVIS Display")
        self._hud_close_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(35, 8, 20, 190);
                color: #ff6688;
                border: 1px solid rgba(255, 102, 136, 0.35);
                border-radius: 4px;
                padding: 3px 8px;
            }}
            QPushButton:hover {{
                background: rgba(255, 51, 102, 0.45);
                color: #ffffff;
                border-color: #ff3366;
            }}
        """)
        self._hud_close_btn.clicked.connect(self.close_display)
        lay.addWidget(self._hud_close_btn)

        return bar

    def _tab_btn_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    background: rgba(255, 77, 166, 0.25);
                    color: #ffffff;
                    border: 1px solid {C.PRI};
                    border-radius: 3px;
                    padding: 5px 8px;
                }}
            """
        return f"""
            QPushButton {{
                background: rgba(20, 5, 18, 180);
                color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 5px 8px;
            }}
            QPushButton:hover {{
                background: rgba(255, 77, 166, 0.12);
                color: {C.TEXT};
                border-color: {C.BORDER_B};
            }}
        """

    def _build_cyber_drawer(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(610)
        w.setStyleSheet(f"""
            QWidget#CyberDrawer {{
                background: rgba(14, 4, 12, 238);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        w.setObjectName("CyberDrawer")

        v = QVBoxLayout(w)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        # Header
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        title = QLabel("JARVIS INTELLIGENCE MATRIX")
        title.setFont(keena_font(9, True, 2))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()

        btn_close = QPushButton("X")
        btn_close.setFont(keena_font(10, True))
        btn_close.setFixedSize(24, 24)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setToolTip("Collapse drawer into compact HUD")
        btn_close.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI}; }}
        """)
        btn_close.clicked.connect(lambda: self._toggle_expanded(False))
        hdr.addWidget(btn_close)
        v.addLayout(hdr)

        # Tabs row
        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(4)
        self._drawer_tabs = QStackedWidget()

        tab_names = [("CHAT & LOG", 0), ("TELEMETRY", 1), ("AUDIO CORE", 2), ("SETTINGS", 3)]
        self._tab_btns = []

        def _make_tab_btn(name, idx):
            btn = QPushButton(name)
            btn.setFont(keena_font(8, True, 1))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setCheckable(True)
            if idx == 0:
                btn.setChecked(True)
            def _on_click():
                for b in self._tab_btns:
                    b.setChecked(False)
                    b.setStyleSheet(self._tab_btn_style(False))
                btn.setChecked(True)
                btn.setStyleSheet(self._tab_btn_style(True))
                self._drawer_tabs.setCurrentIndex(idx)
            btn.clicked.connect(_on_click)
            btn.setStyleSheet(self._tab_btn_style(idx == 0))
            return btn

        for name, idx in tab_names:
            b = _make_tab_btn(name, idx)
            self._tab_btns.append(b)
            tabs_row.addWidget(b)
        v.addLayout(tabs_row)

        # Tab 0: Chat & Log
        p0 = QWidget()
        p0.setStyleSheet("background: transparent;")
        p0_v = QVBoxLayout(p0)
        p0_v.setContentsMargins(0, 4, 0, 0)
        p0_v.setSpacing(6)

        # Full conversation and activity chat log
        self._log.setStyleSheet(f"""
            QTextEdit {{
                background: rgba(12, 3, 11, 220);
                color: {C.TEXT};
                border: 1px solid {C.BORDER_B};
                border-radius: 4px;
                padding: 6px 8px;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 3px;
            }}
        """)
        p0_v.addWidget(self._log, stretch=1)
        p0_v.addLayout(self._build_input_row())
        p0_v.addWidget(self._drop_zone)
        p0_v.addWidget(self._file_hint)
        self._drawer_tabs.addWidget(p0)

        # Tab 1: Telemetry (Armoury Crate hardware matrix)
        p1 = QWidget()
        p1.setStyleSheet("background: transparent;")
        p1_v = QVBoxLayout(p1)
        p1_v.setContentsMargins(0, 2, 0, 2)
        p1_v.setSpacing(4)
        self._armoury_telemetry = ArmouryCrateTelemetryWidget(self)
        p1_v.addWidget(self._armoury_telemetry, stretch=1)
        self._drawer_tabs.addWidget(p1)

        # Tab 2: Audio Core
        p2 = self._build_audio_tab()
        self._drawer_tabs.addWidget(p2)

        # Tab 3: Settings
        p3 = self._build_settings_tab()
        self._drawer_tabs.addWidget(p3)

        v.addWidget(self._drawer_tabs, stretch=1)
        return w

    def _build_audio_tab(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 6, 4, 6)
        lay.setSpacing(10)

        def _hdr(txt):
            l = QLabel(txt)
            l.setFont(keena_font(8, True))
            l.setStyleSheet(f"color: {C.PRI}; border-bottom: 1px solid {C.BORDER}; padding-bottom: 3px;")
            return l

        lay.addWidget(_hdr("AUDIO INPUT (MICROPHONE)"))
        self._combo_mic = QComboBox()
        self._combo_mic.setStyleSheet(f"""
            QComboBox {{
                background: {C.PANEL2}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 5px;
            }}
            QComboBox::drop-down {{ border: none; }}
        """)
        try:
            from core.audio_devices import get_input_devices
            for dev in get_input_devices():
                self._combo_mic.addItem(dev)
        except Exception:
            self._combo_mic.addItem("Default System Microphone")
        lay.addWidget(self._combo_mic)

        lay.addWidget(_hdr("AUDIO OUTPUT (SPEAKERS)"))
        self._combo_spk = QComboBox()
        self._combo_spk.setStyleSheet(self._combo_mic.styleSheet())
        try:
            from core.audio_devices import get_output_devices
            for dev in get_output_devices():
                self._combo_spk.addItem(dev)
        except Exception:
            self._combo_spk.addItem("Default System Speaker")
        lay.addWidget(self._combo_spk)

        lay.addWidget(_hdr("VOICE PERSONA"))
        self._combo_voice = QComboBox()
        self._combo_voice.setStyleSheet(self._combo_mic.styleSheet())
        self._combo_voice.addItems(["Puck", "Charon", "Kore", "Fenrir", "Aoede"])
        self._combo_voice.currentTextChanged.connect(self._on_voice_combo_changed)
        lay.addWidget(self._combo_voice)

        lay.addWidget(_hdr("WAKE ENGINE & SHORTCUTS"))
        self._wake_toggle_btn = QPushButton("WAKE WORD: DISABLED (CLICK TO TOGGLE)")
        self._wake_toggle_btn.setFont(keena_font(8, True))
        self._wake_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wake_toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2}; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 6px;
            }}
            QPushButton:hover {{ border-color: {C.PRI}; color: {C.TEXT}; }}
        """)
        self._wake_toggle_btn.clicked.connect(self._ui_toggle_wake)
        lay.addWidget(self._wake_toggle_btn)

        ptt_info = QLabel("PUSH-TO-TALK: Hold Space or Alt to speak")
        ptt_info.setFont(keena_font(8))
        ptt_info.setStyleSheet(f"color: {C.TEXT_DIM};")
        lay.addWidget(ptt_info)

        lay.addStretch()
        return w

    def _on_voice_combo_changed(self, voice_name: str):
        try:
            from memory import config_manager
            config_manager.set_voice_name(voice_name)
        except Exception:
            pass
        if self.on_voice_change:
            self.on_voice_change()

    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 6, 4, 6)
        lay.setSpacing(8)

        def _hdr(txt):
            l = QLabel(txt)
            l.setFont(keena_font(8, True))
            l.setStyleSheet(f"color: {C.PRI}; border-bottom: 1px solid {C.BORDER}; padding-bottom: 3px;")
            return l

        def _action_btn(txt, cb):
            b = QPushButton(txt)
            b.setFont(keena_font(9, True))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PANEL2}; color: {C.TEXT};
                    border: 1px solid {C.BORDER_B}; border-radius: 4px; padding: 8px 12px;
                }}
                QPushButton:hover {{
                    background: rgba(255, 77, 166, 0.25);
                    border: 1px solid {C.PRI}; color: #ffffff;
                }}
            """)
            b.clicked.connect(cb)
            return b

        lay.addWidget(_hdr("NEURAL INTELLIGENCE CONFIGURATION"))
        lay.addWidget(_action_btn("CONFIGURE GEMINI API KEY", self._show_setup))
        lay.addWidget(_action_btn("OPEN PLUGIN MANAGER", self._open_plugin_manager))
        lay.addWidget(_action_btn("CUSTOMIZE APPEARANCE & IDENTITY", self._open_customize))
        lay.addWidget(_action_btn("PAIR PHONE WEB DASHBOARD", self._open_remote))
        lay.addWidget(_action_btn("ENTER BIG SCREEN MODE (F11)", self._toggle_fullscreen))

        lay.addStretch()
        return w

    def _ui_toggle_wake(self):
        if self.on_wake_toggle:
            st = self.wake_get_state() if self.wake_get_state else {}
            curr = st.get("enabled", False)
            self.on_wake_toggle(not curr)
            self._wake_toggle_btn.setText(f"WAKE WORD: {'ENABLED' if not curr else 'DISABLED'}")

    def _toggle_expanded(self, state: bool | None = None):
        if state is None:
            self._expanded = not self._expanded
        else:
            self._expanded = bool(state)

        if hasattr(self, "_drawer"):
            self._drawer.setVisible(self._expanded)

        screen = QApplication.primaryScreen().availableGeometry()
        curr = self.geometry()

        if self._expanded:
            new_w = min(1040, screen.width() - 40)
            new_h = max(580, curr.height())
            right_anchor = curr.x() + curr.width()
            new_x = max(screen.left() + 20, right_anchor - new_w)
            self.setGeometry(new_x, curr.y(), new_w, new_h)
            if hasattr(self, "_hud_exp_btn"):
                self._hud_exp_btn.setText("COLLAPSE")
                self._hud_exp_btn.setToolTip("Collapse drawer into compact HUD")
        else:
            new_w = 420
            new_h = 580
            right_anchor = curr.x() + curr.width()
            new_x = max(screen.left() + 20, right_anchor - new_w)
            self.setGeometry(new_x, curr.y(), new_w, new_h)
            self.resize(new_w, new_h)
            if hasattr(self, "_hud_exp_btn"):
                self._hud_exp_btn.setText("EXPAND")
                self._hud_exp_btn.setToolTip("Expand attached JARVIS Intelligence Matrix")

    def _toggle_fullscreen(self):
        self._is_fullscreen = not getattr(self, "_is_fullscreen", False)
        if self._is_fullscreen:
            self._prev_geom = self.geometry()
            self._was_expanded = self._expanded
            if hasattr(self, "hud_ctrl_bar"):
                self.hud_ctrl_bar.setVisible(False)
            screen = QApplication.primaryScreen().geometry()
            self.setGeometry(screen)
            self.hud.set_big_mode(True)
        else:
            if hasattr(self, "hud_ctrl_bar"):
                self.hud_ctrl_bar.setVisible(True)
            self.hud.set_big_mode(False)
            target = getattr(self, "_normal_geom", None) or getattr(self, "_prev_geom", None)
            if not target:
                screen = QApplication.primaryScreen().availableGeometry()
                target = QRect(screen.right() - 440, screen.bottom() - 610, 420, 580)
            self.setGeometry(target)

    def _on_log_line(self, text: str):
        self._log.append_log(text)
        if hasattr(self, "bottom_chat_dock") and hasattr(self.bottom_chat_dock, "_dock_log"):
            self.bottom_chat_dock._dock_log.append_log(text)
        asst = getattr(self, "_assistant_name", "JARVIS").upper()
        if text.startswith("You: "):
            self.hud.set_user_text(text[5:].strip())
        elif text.startswith(f"{asst}: ") or text.startswith("JARVIS: "):
            idx = text.find(": ")
            self.hud.set_reply_text(text[idx + 2:].strip())
        elif any(text.startswith(p) for p in ("SYS: ", "ACT: ", "TOOL: ", "ERR: ")):
            parts = text.split(":", 1)
            if len(parts) > 1:
                clean = parts[1].strip().lstrip("▶ 🎤 🔊 🛑 🔇 🎙 ").strip()
                if clean and len(clean) < 50:
                    self.hud.add_card(clean[:32])

    def _on_stream_user(self, text: str):
        if hasattr(self, "hud") and hasattr(self.hud, "stream_user_text"):
            self.hud.stream_user_text(text)

    def _on_stream_reply(self, text: str):
        if hasattr(self, "hud") and hasattr(self.hud, "stream_reply_text"):
            self.hud.stream_reply_text(text)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.globalPosition().toPoint()
            self._win_start = self.pos()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if hasattr(self, "_drag_start") and self._drag_start and e.buttons() == Qt.MouseButton.LeftButton:
            delta = e.globalPosition().toPoint() - self._drag_start
            self.move(self._win_start + delta)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_start = None
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            if getattr(self, "_is_fullscreen", False):
                self._toggle_fullscreen()
                return
            elif self._expanded:
                self._toggle_expanded(False)
                return
            elif self.on_interrupt:
                self.on_interrupt()
                return
        super().keyPressEvent(e)

    # --- Video in the HUD area ---------------------------------------------
    #
    # Everything below runs on the Qt thread. Plugins and the assistant reach it
    # through the signals above, the same way every other panel is driven, so a
    # background thread never touches a widget.
    def _on_video_open(self, source: str, title: str, muted: bool,
                       audio_source: str = "") -> None:
        if not HAVE_VIDEO or not self._video_player:
            self.write_log("SYS: Video playback is not available in this Qt "
                           "install.")
            return
        # A video and the live camera cannot share the centre of the HUD.
        self._cam_stop.set()

        self._video_title.setText(f"▶  {(title or 'VIDEO')[:44].upper()}")
        self._video_split = bool(audio_source)
        self._set_video_muted(bool(muted))

        url = (QUrl.fromLocalFile(source) if Path(source).exists()
               else QUrl(source))
        self._video_player.setSource(url)
        if self._video_split:
            self._video_sound.setSource(QUrl(audio_source))
        self._hud_cam_stack.setCurrentIndex(2)
        self._video_on = True
        # Re-run now that the video counts as playing: _set_video_muted ran
        # before this line and saw no video, so its mic check was a no-op.
        self._sync_mic_for_video()
        self._video_player.play()
        if self._video_split:
            self._video_sound.play()
            self._video_sync.start()

    def _fit_video(self, *_a) -> None:
        """Size the picture to the panel, keeping its shape."""
        if not (self._video_item and self._video_widget):
            return
        try:
            native = self._video_item.nativeSize()
            if native.isEmpty():
                return
            view = self._video_widget.viewport().size()
            scale = min(view.width() / native.width(),
                        view.height() / native.height())
            w, h = native.width() * scale, native.height() * scale
            self._video_item.setSize(QSizeF(w, h))
            self._video_scene.setSceneRect(0, 0, w, h)
            self._video_widget.centerOn(self._video_item)
        except Exception:
            pass

    def _on_video_close(self) -> None:
        if self._video_sync:
            self._video_sync.stop()
        for p in (self._video_player, self._video_sound):
            if p:
                p.stop()
                p.setSource(QUrl())
        self._video_split = False
        self._video_on = False
        self._hud_cam_stack.setCurrentIndex(0)
        self._sync_mic_for_video()      # gives the microphone back

    def _set_video_muted(self, muted: bool) -> None:
        """Silence whichever output is carrying the sound for this video."""
        muted = bool(muted)
        if self._video_audio:
            # When the sound is a separate stream this player has none, but
            # muting it too costs nothing and keeps the two paths identical.
            self._video_audio.setMuted(muted)
        if self._video_sound_out:
            self._video_sound_out.setMuted(muted)
        self._sync_video_mute_btn()
        self._sync_mic_for_video()

    def _on_video_mute(self, muted: bool) -> None:
        self._set_video_muted(muted)

    def _sync_mic_for_video(self) -> None:
        """Close the microphone while the video is making sound.

        JARVIS subtracts its OWN output from the microphone — that is what
        core/echo.py does — but a video plays through a different output
        entirely, so the guard has never heard of it and the assistant answers
        the film. There is no arrangement in which an open microphone and a
        loudspeaker in the same room do not do that.

        So the microphone closes for exactly as long as the sound is on, and
        opens again by itself the moment it goes off or the video is closed.
        Only if JARVIS closed it: a microphone the user muted themselves stays
        muted, and pressing the mute key during a video hands the decision back
        to them for good.
        """
        sound_on = bool(self._video_on and self._video_sound_out
                        and not self._video_sound_out.isMuted())
        if sound_on and not self._muted:
            self._video_auto_muted = True
            self._set_muted(True, "The video's sound is on — silence it, close "
                                  "it, or press F4 to talk. Typing still works.")
        elif not sound_on and self._video_auto_muted:
            self._video_auto_muted = False
            self._set_muted(False, "The video is quiet again.")

    def _sync_video_sound(self) -> None:
        """Keep the separate soundtrack in step with the picture.

        Two players started at the same moment do not stay together: they buffer
        independently, and measured on a real stream they were about half a
        second apart after nine seconds. So the sound is nudged back to the
        picture whenever it drifts far enough to hear — and only then, because
        correcting a smaller gap is itself audible.
        """
        if not (self._video_split and self._video_sound and self._video_player):
            return
        try:
            if self._video_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                return
            drift = self._video_sound.position() - self._video_player.position()
            if abs(drift) > 300:
                self._video_sound.setPosition(self._video_player.position())
        except Exception:
            pass

    def _sync_video_mute_btn(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        muted = bool(out and out.isMuted())
        self._video_mute_btn.setText("🔇  SOUND OFF" if muted else "🔊  SOUND ON")

    def _toggle_video_mute(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        if out:
            self._set_video_muted(not out.isMuted())

    def _on_video_error(self, *_a) -> None:
        err = ""
        try:
            err = self._video_player.errorString()
        except Exception:
            pass
        self.write_log(f"SYS: The video could not be played{(' — ' + err) if err else ''}.")
        self._on_video_close()

    def stop_video(self) -> None:
        self._video_close_sig.emit()

    def video_is_playing(self) -> bool:
        return bool(self._video_on)

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_jarvis_icon(out_path: Path) -> bool:
        """
        Render a JARVIS arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "J.A.R.V.I.S AI Assistant"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{lnk}")',
            f'sc.TargetPath = "{target}"',
            f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
            f'sc.WorkingDirectory = "{work_dir}"',
            'sc.Description = "J.A.R.V.I.S AI Assistant"',
            f'sc.IconLocation = "{icon_loc}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "jarvis.ico"
        if not ico_path.exists():
            self._build_jarvis_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "J.A.R.V.I.S.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "J.A.R.V.I.S.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "JARVIS"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>JARVIS</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.jarvis.assistant</string>\n'
                    '  <key>CFBundleName</key><string>J.A.R.V.I.S</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "J.A.R.V.I.S.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=J.A.R.V.I.S\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if getattr(self, "_overlay", None) and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if getattr(self, "_remote_overlay", None) and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if getattr(self, "_customize_overlay", None) and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Quick drawer — reposition if open
        if hasattr(self, '_quick_drawer') and self._quick_drawer.isVisible():
            self._position_quick_drawer()
        if hasattr(self, '_ctrl_drawer') and self._ctrl_drawer.isVisible():
            self._position_ctrl_drawer()
        self._fit_video()

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")

        try:
            self.hud.set_metrics(cpu, mem, "LIVE" if getattr(self, "_ready", True) else "OFFLINE")
        except Exception:
            pass

        if hasattr(self, "_armoury_telemetry") and self._armoury_telemetry:
            try:
                self._armoury_telemetry.update_metrics(snap)
            except Exception:
                pass


    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(54)
        w.setStyleSheet(f"background: {C.DARK}; border-bottom: 1px solid {C.BORDER_B};")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 16, 0)

        def _badge(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(QFont("Courier New", 8))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_badge(APP_VERSION, C.PRI_DIM))
        lay.addSpacing(8)
        self._drawer_btn = QPushButton("⚙")
        self._drawer_btn.setFixedSize(26, 26)
        self._drawer_btn.setFont(QFont("Courier New", 11))
        self._drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_btn.setToolTip("Settings & Controls")
        self._drawer_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 4px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
            QPushButton:checked {{ color: {C.PRI}; border-color: {C.PRI}; background: {C.PRI_GHO}; }}
        """)
        self._drawer_btn.setCheckable(True)
        self._drawer_btn.setToolTip("Setup — things you set once")
        self._drawer_btn.clicked.connect(self._toggle_drawer)
        lay.addWidget(self._drawer_btn)

        # A SECOND drawer, and the split is by how often a thing is touched.
        #
        # One panel held twelve buttons: the ones you press once when you set
        # the assistant up sitting next to the ones you flick on and off every
        # day. Separating them by that — setup behind ⚙, everyday behind 🎛 —
        # is what makes each list short enough to read at a glance.
        self._ctrl_btn = QPushButton("🎛")
        self._ctrl_btn.setFixedSize(26, 26)
        self._ctrl_btn.setFont(QFont("Courier New", 11))
        self._ctrl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ctrl_btn.setToolTip("Controls — the everyday switches")
        self._ctrl_btn.setStyleSheet(self._drawer_btn.styleSheet())
        self._ctrl_btn.setCheckable(True)
        self._ctrl_btn.clicked.connect(self._toggle_controls)
        lay.addSpacing(4)
        lay.addWidget(self._ctrl_btn)
        lay.addStretch()

        mid = QVBoxLayout(); mid.setSpacing(1)
        _disp = self._assistant_name.upper()
        self._title_lbl = QLabel(_disp)
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setFont(QFont("Courier New", 17, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        mid.addWidget(self._title_lbl)
        _sub_text = ("A Friendly Assistant"
                     if _disp in ("JARVIS", "J.A.R.V.I.S")
                     else "Personal AI Assistant")
        self._sub_lbl = QLabel(_sub_text)
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setFont(QFont("Courier New", 7))
        self._sub_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        mid.addWidget(self._sub_lbl)
        lay.addLayout(mid)
        lay.addStretch()

        right_col = QVBoxLayout(); right_col.setSpacing(2)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Courier New", 14, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont("Courier New", 7))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        return w

    def _tick_clock(self):
        if hasattr(self, "_clock_lbl"):
            self._clock_lbl.setText(time.strftime("%I:%M:%S %p"))
        if hasattr(self, "_date_lbl"):
            self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-right: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(6)

        hdr = QLabel("◈ SYS MONITOR")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)
        lay.addSpacing(2)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#ff6688")

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                    self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        info_panel = QWidget()
        info_panel.setStyleSheet(
            f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 4px;"
        )
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(6, 5, 6, 5)
        ip_lay.setSpacing(3)

        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont("Courier New", 8))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(QFont("Courier New", 8))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addSpacing(4)

        lay.addStretch()

        for txt, col in [
            ("AI CORE\nACTIVE",  C.GREEN),
            ("SEC\nCLEARED",     C.PRI),
            ("PROTOCOL\n" + APP_PROTOCOL,   C.TEXT_DIM),
        ]:
            lbl = QLabel(txt)
            lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {col}; background: {C.PANEL2};"
                f"border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 4px;"
            )
            lay.addWidget(lbl)

        return w
    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(f"background: {C.DARK}; border-left: 1px solid {C.BORDER};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        def _sec(txt):
            l = QLabel(f"▸ {txt}")
            l.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            return l

        lay.addWidget(_sec("ACTIVITY LOG"))
        self._log = LogWidget()
        lay.addWidget(self._log, stretch=1)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_sec("FILE UPLOAD"))
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file loaded — drop or click above to upload")
        self._file_hint.setFont(QFont("Courier New", 7))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        lay.addWidget(self._file_hint)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_sec("COMMAND INPUT"))
        lay.addLayout(self._build_input_row())

        self._interrupt_btn = QPushButton("✋  INTERRUPT  [ESC]")
        self._interrupt_btn.setFixedHeight(34)
        self._interrupt_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt_btn.setStyleSheet(f"""
            QPushButton {{
                background: #140008; color: {C.MUTED_C};
                border: 1px solid {C.MUTED_C}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: #200010; border: 1px solid #ff6688;
            }}
            QPushButton:pressed {{
                background: #300018;
            }}
        """)
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        lay.addWidget(self._interrupt_btn)

        self._mute_btn = QPushButton("🎙  MICROPHONE ACTIVE")
        self._mute_btn.setFixedHeight(30)
        self._mute_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        return w

    def _build_quick_drawer(self) -> QWidget:
        """Floating overlay panel shown when the ⚙ header button is toggled."""
        _BTN_STYLE_PRI = f"""
            QPushButton {{
                background: #00091a; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """
        _BTN_STYLE_DIM = f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """

        self._BTN_PRI, self._BTN_DIM = _BTN_STYLE_PRI, _BTN_STYLE_DIM
        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(f"""
            QWidget#QuickDrawer {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-top: none;
                border-radius: 0 0 6px 6px;
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ SETUP")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        remote_btn = QPushButton("◉  REMOTE CONTROL")
        remote_btn.setFixedHeight(30)
        remote_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        sc_btn = QPushButton("⊞  CREATE DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(26)
        sc_btn.setFont(QFont("Courier New", 7))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = QPushButton("◉  AUTO-START: OFF")
        self._autostart_btn.setFixedHeight(26)
        self._autostart_btn.setFont(QFont("Courier New", 7))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = QPushButton("⚙  CUSTOMISE ASSISTANT")
        cust_btn.setFixedHeight(26)
        cust_btn.setFont(QFont("Courier New", 7))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        audio_btn = QPushButton("🎧  AUDIO DEVICES")
        audio_btn.setFixedHeight(26)
        audio_btn.setFont(QFont("Courier New", 7))
        audio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        audio_btn.setStyleSheet(_BTN_STYLE_DIM)
        audio_btn.clicked.connect(self._open_audio_devices)
        lay.addWidget(audio_btn)

        mem_btn = QPushButton("🧠  MEMORY")
        mem_btn.setFixedHeight(26)
        mem_btn.setFont(QFont("Courier New", 7))
        mem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mem_btn.setStyleSheet(_BTN_STYLE_DIM)
        mem_btn.clicked.connect(self._open_memory_panel)
        lay.addWidget(mem_btn)

        plugin_btn = QPushButton("🧩  PLUGINS")
        plugin_btn.setFixedHeight(26)
        plugin_btn.setFont(QFont("Courier New", 7))
        plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plugin_btn.setStyleSheet(_BTN_STYLE_DIM)
        plugin_btn.clicked.connect(self._open_plugin_manager)
        lay.addWidget(plugin_btn)

        settings_btn = QPushButton("⚙  PLUGIN SETTINGS")
        settings_btn.setFixedHeight(26)
        settings_btn.setFont(QFont("Courier New", 7))
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(_BTN_STYLE_DIM)
        settings_btn.clicked.connect(self._open_plugin_settings)
        lay.addWidget(settings_btn)

        # The button is built here with the rest of the drawer; the panel behind
        # it — and the whole providers package — is imported on the click. The
        # drawer used to stutter for a measured 2.104 seconds because a state
        # check imported openwakeword while it was being built, and adding a
        # second thing that does that would undo the fix rather than repeat it.
        prov_btn = QPushButton("🔌  ENGINE && PROVIDERS")
        prov_btn.setFixedHeight(26)
        prov_btn.setFont(QFont("Courier New", 7))
        prov_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        prov_btn.setStyleSheet(_BTN_STYLE_DIM)
        prov_btn.clicked.connect(self._open_providers)
        lay.addWidget(prov_btn)

        w.adjustSize()
        return w

    def _build_controls_drawer(self) -> QWidget:
        """The switches that get flicked every day, in their own panel."""
        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(self._quick_drawer.styleSheet())
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ CONTROLS")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        def _row(button, style=None, height=26):
            button.setFixedHeight(height)
            button.setFont(QFont("Courier New", 7))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if style:
                button.setStyleSheet(style)
            lay.addWidget(button)
            return button

        fs_btn = _row(QPushButton("⛶  FULLSCREEN  [F11]"), self._BTN_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)

        self._brief_btn = _row(QPushButton())
        self._brief_btn.clicked.connect(self._toggle_brief)

        self._wake_btn = _row(QPushButton())
        self._wake_btn.clicked.connect(self._toggle_wake_word)
        self._wake_sleep_btn = _row(QPushButton())
        self._wake_sleep_btn.clicked.connect(self._tap_wake_manual)
        # Neutral until the real state lands from the background warm-up.
        self._wake_btn.setText("🎙  WAKE WORD")
        self._wake_btn.setStyleSheet(self._BTN_DIM)
        self._wake_sleep_btn.hide()

        self._ptt_btn = _row(QPushButton())
        self._ptt_btn.clicked.connect(self._toggle_ptt)
        self._refresh_talk_btns()

        self._hud_btn = _row(QPushButton())
        self._hud_btn.clicked.connect(self._toggle_hud_style)
        self._refresh_hud_btn()

        w.adjustSize()
        return w

    def _warm_wake_state(self) -> None:
        """Work out the wake-word state off the UI thread, once.

        It used to be resolved when the drawer opened, and that made opening the
        drawer take two seconds the first time — measured at 2.10s, all of it
        `import openwakeword` dragging in onnxruntime behind it. The button
        count had nothing to do with it; the Qt thread was simply waiting on an
        import. Afterwards the same check costs about a millisecond, so it only
        ever needed to happen somewhere other than in front of the user.
        """
        def work():
            try:
                self._wake_state()
            except Exception:
                pass
            self._wake_btns_sig.emit()
        threading.Thread(target=work, daemon=True, name="wake-state-warm").start()

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._close_controls()
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _toggle_controls(self, checked: bool):
        if checked:
            self._close_setup()
            self._refresh_wake_btns()   # cheap now: the state was warmed at boot
            self._position_ctrl_drawer()
            self._ctrl_drawer.show()
            self._ctrl_drawer.raise_()
        else:
            self._ctrl_drawer.hide()

    def _close_setup(self):
        if hasattr(self, "_quick_drawer"):
            self._quick_drawer.hide()
        if hasattr(self, "_drawer_btn"):
            self._drawer_btn.setChecked(False)

    def _close_controls(self):
        if hasattr(self, "_ctrl_drawer"):
            self._ctrl_drawer.hide()
        if hasattr(self, "_ctrl_btn"):
            self._ctrl_btn.setChecked(False)

    def _place_drawer(self, drawer, left: int):
        _W = 220
        drawer.setFixedWidth(_W)
        drawer.adjustSize()
        drawer.setGeometry(left, 54, _W, drawer.sizeHint().height())

    def _position_quick_drawer(self):
        if hasattr(self, "_quick_drawer"):
            self._place_drawer(self._quick_drawer, 12)

    def _position_ctrl_drawer(self):
        # Under its own header button rather than under the other one's.
        if hasattr(self, "_ctrl_drawer"):
            left = 12
            try:
                left = max(12, self._ctrl_btn.mapTo(self.centralWidget(),
                                                    QPoint(0, 0)).x() - 8)
            except Exception:
                pass
            self._place_drawer(self._ctrl_drawer, left)

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(5)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or question…")
        self._input.setFont(QFont("Courier New", 9))
        self._input.setFixedHeight(30)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d14; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 7px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = QPushButton("▸")
        send.setFixedSize(30, 30)
        send.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BRIEFING")
        self._content_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(QFont("Courier New", 7))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("DISMISS  ✕")
        dismiss.setFont(QFont("Courier New", 7))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(QFont("Courier New", 8))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time
        self.hud.glance(0.0, -0.85, hold=1.3)
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%I:%M:%S %p"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        self._content_panel.show()
        if hasattr(self, "left_search_dock"):
            self.left_search_dock.popup(title, _time.strftime("%I:%M:%S %p"))

    # ── the coding agent, live ───────────────────────────────────────────────
    # The panel under the head shows a folder job while it runs: what was asked,
    # the plan, every file with its state, and the steps as they happen. Drawn
    # from a snapshot the job publishes on every step, so this slot never reads
    # anything a worker thread is still changing.
    _JOB_STATES = {
        "waiting": ("TEXT_DIM", "○"), "writing": ("PRI", "✎"),
        "done": ("GREEN", "✓"), "fixing": ("ACC2", "↻"),
        "skipped": ("TEXT_DIM", "–"), "failed": ("RED", "✕"),
        "broken": ("RED", "!"),
    }
    _JOB_PHASES = ("reading", "planning", "writing", "checking")

    def _show_code_job(self, job: dict):
        """Slot — Qt main thread. Draws the coding agent's progress."""
        import time as _time
        if not job:
            return
        e = self._esc
        col = lambda name: getattr(C, name, C.TEXT)
        status = job.get("status", "")
        finished = job.get("finished")
        took = (finished or _time.time()) - job.get("started", _time.time())

        # Phase strip: where in the job it is, readable at a glance.
        strip = []
        phases = list(job.get("phases") or self._JOB_PHASES)
        here = phases.index(status) if status in phases else (
            len(phases) if finished else -1)
        for i, ph in enumerate(phases):
            c = C.GREEN if i < here else (C.PRI if i == here else C.TEXT_DIM)
            mark = "●" if i <= here else "○"
            strip.append(f'<span style="color:{c};">{mark} {ph.upper()}</span>')
        if finished:
            fin = C.GREEN if status == "done" else (C.RED if status == "failed" else C.ACC2)
            strip.append(f'<span style="color:{fin};"><b>● {e(status.upper())}</b></span>')

        parts = [f'<div style="color:{C.TEXT}; font-family:Courier New;">',
                 f'<div style="color:{C.TEXT_DIM};">{"  ".join(strip)}'
                 f'&nbsp;&nbsp;·&nbsp;&nbsp;{took:.0f}s</div>',
                 f'<div style="margin-top:6px;"><span style="color:{C.TEXT_DIM};">'
                 f'REQUEST&nbsp;</span>{e(job.get("request", ""))}</div>']
        if job.get("summary"):
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin:8px 0;">{e(job["summary"])}</div>')
        files = job.get("files") or {}
        if files:
            parts.append(f'<div style="color:{C.TEXT_DIM}; margin-top:4px;">FILES</div>')
            for path, f in files.items():
                cname, mark = self._JOB_STATES.get(f.get("state", ""), ("TEXT_DIM", "·"))
                verb = "new " if f.get("action") == "create" else ""
                parts.append(
                    f'<div><span style="color:{col(cname)};">{mark}</span>&nbsp;'
                    f'<span style="color:{C.WHITE};">{e(path)}</span>&nbsp;'
                    f'<span style="color:{C.TEXT_DIM};">{verb}{e(f.get("what", ""))[:110]}'
                    f'</span></div>')
        # Newest first, so the latest step sits under the plan and the whole
        # state of the job is visible without scrolling in a 220-pixel panel.
        steps = job.get("steps") or []
        if steps:
            parts.append(f'<div style="color:{C.TEXT_DIM}; margin-top:8px;">'
                         f'STEPS — newest first</div>')
            for t, msg in reversed(steps[-14:]):
                parts.append(f'<div><span style="color:{C.TEXT_DIM};">{float(t):6.1f}s'
                             f'</span>&nbsp;{e(msg)}</div>')
        parts.append("</div>")

        title = f"CODE AGENT — {job.get('folder', '')} · {job.get('model', '')}"
        first = self._content_title_lbl.text() != title.upper()[:48]
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%I:%M:%S %p"))
        bar = self._content_display.verticalScrollBar()
        keep = 0 if first else bar.value()
        self._content_display.setHtml("".join(parts))
        # setHtml jumps to the top; put the reader back where they were.
        bar.setValue(keep)
        if not self._content_panel.isVisible():
            self.hud.glance(0.0, -0.85, hold=1.3)
            self._content_panel.show()
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 260, 120), 260])

    # ── document review ──────────────────────────────────────────────────────
    # Rendered as rich text into the content panel that already exists, rather
    # than into a panel of its own. A review is read, not clicked, so QTextEdit
    # gives scrolling, selection and copy for nothing, and the HUD gains no
    # widget it has to lay out. Severity decides colour and order here because
    # that is presentation; the plugin supplies no styling and knows no palette,
    # which is also what lets a re-theme repaint a review correctly.

    # Severity is marked by a symbol and a colour, not by a word. The findings
    # themselves are in the user's language, and "[SERIOUS]" sitting inside a
    # Turkish sentence is the kind of seam this project tries not to have —
    # while translating the tag would mean a table per language, which is worse.
    # A shape carries it in every language, and shape plus colour still reads
    # for someone who cannot separate red from amber. What the marks mean
    # arrives the way everything else does: JARVIS says it out loud.
    _REVIEW_MARKS = {"serious": ("RED", "▲"), "caution": ("ACC2", "●"), "note": ("PRI_DIM", "·")}

    @staticmethod
    def _esc(s) -> str:
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))

    def _show_review(self, title: str, summary: str, findings, unclear):
        """Slot — Qt main thread. Lays a document review into the content panel."""
        if hasattr(self, "left_search_dock"):
            self.left_search_dock.popup("REVIEW: " + title, time.strftime("%I:%M:%S %p"))
        e = self._esc
        parts = [f'<div style="color:{C.TEXT}; font-family:Courier New;">']

        if summary:
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin-bottom:10px;">{e(summary)}</div>')

        for f in (findings or []):
            key, mark = self._REVIEW_MARKS.get(f.get("severity"), ("PRI_DIM", "·"))
            colour = getattr(C, key)
            parts.append(f'<div style="margin-bottom:11px;">')
            parts.append(
                f'<span style="color:{colour}; font-weight:bold;">{mark}</span> '
                f'<span style="color:{C.WHITE}; font-weight:bold;">'
                f'{e(f.get("heading"))}</span>')
            if f.get("detail"):
                parts.append(f'<div style="margin-left:12px;">{e(f["detail"])}</div>')
            if f.get("quote"):
                # The document's own wording, visually separated from the
                # explanation so the two are never mistaken for each other.
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_DIM};'
                    f' border-left:1px solid {C.BORDER}; padding-left:7px;">'
                    f'&ldquo;{e(f["quote"])}&rdquo;</div>')
            if f.get("suggestion"):
                parts.append(
                    f'<div style="margin-left:12px; color:{C.PRI};">'
                    f'&rarr; {e(f["suggestion"])}</div>')
            parts.append('</div>')

        if unclear:
            parts.append(
                f'<div style="margin-top:6px; border-top:1px solid {C.BORDER};'
                f' padding-top:7px; color:{C.TEXT_MED};">'
                'The document does not settle:</div>')
            for u in unclear:
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_MED};">'
                    f'&middot; {e(u)}</div>')
        parts.append('</div>')

        import time as _time
        self.hud.glance(0.0, -0.85, hold=1.3)
        # Left as written, not upper-cased. The other content-panel titles are
        # the app's own English labels, but this one is the document's name in
        # the user's language, and str.upper() applies English casing rules to
        # it: Turkish "Sözleşmesi" comes back "SÖZLEŞMESI", having lost the
        # dotted capital İ. Python has no locale-aware upper to reach for, and
        # imposing one language's rules on all of them is the bug, not the fix.
        self._content_title_lbl.setText((title or "Document")[:48])
        self._content_ts_lbl.setText(_time.strftime("%I:%M:%S %p"))
        self._content_display.setHtml("".join(parts))
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start)
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 260, 120), 260, 0])

    # ── quiz panel ───────────────────────────────────────────────────────────
    # An interactive twin of the content panel. The plugin only ever hands over
    # questions; everything about asking, marking and reporting happens here,
    # and the finished result is pushed back into the conversation the same way
    # a dropped file is — as a message JARVIS reads and responds to. That keeps
    # the tool call short (it returns the moment the board is up) and leaves the
    # talking to the assistant, in the user's own language.

    def _quiz_btn(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFont(QFont("Courier New", 8))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(24)
        edge = C.BORDER_B if primary else C.BORDER
        col = C.PRI if primary else C.TEXT_MED
        b.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2}; color: {col};
                border: 1px solid {edge}; border-radius: 2px;
                padding: 3px 9px; text-align: left;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI_DIM}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        """)
        return b

    def _build_quiz_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("QuizPanel")
        w.setStyleSheet(f"""
            QWidget#QuizPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout(); hdr.setSpacing(6)
        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._quiz_title_lbl = QLabel("QUIZ")
        self._quiz_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._quiz_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        hdr.addWidget(self._quiz_title_lbl)
        hdr.addStretch()

        self._quiz_count_lbl = QLabel("")
        self._quiz_count_lbl.setFont(QFont("Courier New", 7))
        self._quiz_count_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._quiz_count_lbl)

        quit_btn = QPushButton("DISMISS  ✕")
        quit_btn.setFont(QFont("Courier New", 7))
        quit_btn.setFixedHeight(18)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        quit_btn.clicked.connect(self._hide_quiz)
        hdr.addWidget(quit_btn)
        lay.addLayout(hdr)

        rule = QFrame(); rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {C.BORDER};")
        lay.addWidget(rule)

        self._quiz_q_lbl = QLabel("")
        self._quiz_q_lbl.setWordWrap(True)
        self._quiz_q_lbl.setFont(QFont("Courier New", 9))
        self._quiz_q_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._quiz_q_lbl)

        self._quiz_answers = QWidget()
        self._quiz_answers.setStyleSheet("background: transparent;")
        self._quiz_answers_lay = QVBoxLayout(self._quiz_answers)
        self._quiz_answers_lay.setContentsMargins(0, 2, 0, 0)
        self._quiz_answers_lay.setSpacing(4)
        lay.addWidget(self._quiz_answers)

        self._quiz_note_lbl = QLabel("")
        self._quiz_note_lbl.setWordWrap(True)
        self._quiz_note_lbl.setFont(QFont("Courier New", 8))
        self._quiz_note_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._quiz_note_lbl.hide()
        lay.addWidget(self._quiz_note_lbl)

        foot = QHBoxLayout()
        foot.addStretch()
        self._quiz_next_btn = self._quiz_btn("NEXT  →", primary=True)
        self._quiz_next_btn.setFixedWidth(110)
        self._quiz_next_btn.clicked.connect(self._quiz_next)
        self._quiz_next_btn.hide()
        foot.addWidget(self._quiz_next_btn)
        lay.addLayout(foot)

        self._quiz = None
        return w

    def _show_quiz(self, topic: str, questions, grader=None):
        """Slot — Qt main thread. Puts a fresh quiz on the board."""
        if not questions:
            return
        if hasattr(self, "left_search_dock"):
            self.left_search_dock.popup("QUIZ: " + (topic or "QUIZ"), time.strftime("%I:%M:%S %p"))
        self._quiz = {
            "topic": topic or "",
            "questions": list(questions),
            "grader": grader,
            "i": 0,
            "results": [],
            "answered": False,
        }
        self._quiz_title_lbl.setText((topic or "quiz").upper()[:48])
        self.hud.glance(0.0, -0.85, hold=1.3)
        first_show = not self._quiz_panel.isVisible()
        self._quiz_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 250, 120), 0, 250])
        self._quiz_render()

    def _hide_quiz(self):
        self._quiz = None
        self._quiz_panel.hide()

    def _quiz_clear_answers(self):
        while self._quiz_answers_lay.count():
            item = self._quiz_answers_lay.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()

    def _quiz_render(self):
        q = self._quiz["questions"][self._quiz["i"]]
        n, total = self._quiz["i"] + 1, len(self._quiz["questions"])
        self._quiz_count_lbl.setText(f"{n} / {total}")
        self._quiz_q_lbl.setText(q.get("question", ""))
        self._quiz_note_lbl.hide()
        self._quiz_next_btn.hide()
        self._quiz["answered"] = False
        self._quiz_clear_answers()

        opts = q.get("options") or []
        if opts:
            for text in opts:
                b = self._quiz_btn("   " + text)
                b.clicked.connect(lambda _=False, t=text: self._quiz_submit(t))
                self._quiz_answers_lay.addWidget(b)
        else:
            row = QWidget(); row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            field = QLineEdit()
            field.setFont(QFont("Courier New", 9))
            field.setPlaceholderText("your answer")
            field.setStyleSheet(f"""
                QLineEdit {{
                    background: {C.PANEL2}; color: {C.WHITE};
                    border: 1px solid {C.BORDER}; border-radius: 2px; padding: 4px 7px;
                }}
                QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
            """)
            send = self._quiz_btn("ANSWER", primary=True)
            send.setFixedWidth(90)
            field.returnPressed.connect(lambda: self._quiz_submit(field.text()))
            send.clicked.connect(lambda: self._quiz_submit(field.text()))
            h.addWidget(field, stretch=1)
            h.addWidget(send)
            self._quiz_answers_lay.addWidget(row)
            field.setFocus()

    def _quiz_submit(self, given: str):
        if self._quiz is None or self._quiz["answered"]:
            return
        self._quiz["answered"] = True
        q = self._quiz["questions"][self._quiz["i"]]
        grader = self._quiz.get("grader")
        verdict = None
        if callable(grader):
            try:
                verdict = grader(q, given)
            except Exception:
                verdict = None
        self._quiz["results"].append({
            "question": q.get("question", ""),
            "type": q.get("type", ""),
            "given": str(given or "").strip(),
            "answer": q.get("answer", ""),
            "correct": verdict,
        })

        for i in range(self._quiz_answers_lay.count()):
            wdg = self._quiz_answers_lay.itemAt(i).widget()
            if wdg is not None:
                wdg.setEnabled(False)

        if verdict is True:
            mark, colour = "✓  correct", C.GREEN
        elif verdict is False:
            mark, colour = "✕  " + str(q.get("answer", "")), C.RED
        else:
            # Open answers and near-miss gap-fills are JARVIS's to judge. Saying
            # so is honest; marking it wrong here would be a guess.
            mark, colour = "…  noted — I'll go over this one with you", C.ACC2
        note = q.get("note") or ""
        self._quiz_note_lbl.setText(mark + (("\n" + note) if note else ""))
        self._quiz_note_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        self._quiz_note_lbl.show()

        last = self._quiz["i"] >= len(self._quiz["questions"]) - 1
        self._quiz_next_btn.setText("FINISH  →" if last else "NEXT  →")
        self._quiz_next_btn.show()
        self._quiz_next_btn.setFocus()

    def _quiz_next(self):
        if self._quiz is None:
            return
        if self._quiz["i"] >= len(self._quiz["questions"]) - 1:
            self._quiz_finish()
        else:
            self._quiz["i"] += 1
            self._quiz_render()

    def _quiz_finish(self):
        if self._quiz is None:
            return
        topic = self._quiz["topic"]
        results = self._quiz["results"]
        right = sum(1 for r in results if r["correct"] is True)
        unsure = sum(1 for r in results if r["correct"] is None)
        total = len(results)
        self._quiz_panel.hide()
        self._quiz = None

        self._log.append_log(f"QUIZ: {topic or 'quiz'} — {right}/{total} correct")

        # Hand it back to JARVIS as a message, not as a tool return: the tool
        # call ended minutes ago. This is the same channel a dropped file uses.
        lines = [f"[QUIZ_DONE] topic={topic or 'general'} | "
                 f"auto-marked {right}/{total} correct"
                 + (f", {unsure} still need your marking" if unsure else "")]
        for i, r in enumerate(results, 1):
            state = ("correct" if r["correct"] is True
                     else "wrong" if r["correct"] is False else "NEEDS MARKING")
            lines.append(
                f"{i}. [{r['type']}] {r['question']} | they answered: "
                f"{r['given'] or '(blank)'} | expected: {r['answer']} | {state}")
        lines.append(
            "Mark every question flagged NEEDS MARKING yourself — accept an answer "
            "that means the same thing. Then tell them how they did in their own "
            "language: the score, what they got wrong and why, in a couple of "
            "sentences. Offer another round only if it fits. "
            "Remember something only if it would still matter next week — that they "
            "are working through a subject, or keep missing the same thing. A score "
            "from one session is not worth a memory, and a memory per quiz would "
            "bury the things that are.")
        msg = "\n".join(lines)
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(22)
        w.setStyleSheet(f"background: {C.DARK}; border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont("Courier New", 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mute  ·  [F11] Fullscreen"))
        lay.addStretch()
        lay.addWidget(_fl("By FatihMakes", C.PRI_DIM))
        return w

    def _browse_file(self):
        if hasattr(self, "_drop_zone") and self._drop_zone:
            self._drop_zone._browse()

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        if cat == "folder":
            n = self._drop_zone._folder_files
            self._file_hint.setText(
                f"{icon}  {p.name}  ·  {n} files  ·  Tell {self._assistant_name} "
                f"what to change in it")
            self._log.append_log(f"FOLDER: {p.name} ({n} files) loaded")
            if self.on_text_command:
                msg = (
                    f"[FOLDER_UPLOADED] path={path} | name={p.name} | files={n} | "
                    f"Briefly tell the user the project folder '{p.name}' is loaded "
                    f"and that you can improve, fix or extend it with the coding "
                    f"model — ask what they want changed. When they say, call "
                    f"dev_agent with mode='improve' and their request as the "
                    f"description."
                )
                threading.Thread(target=self.on_text_command, args=(msg,),
                                 daemon=True).start()
            return
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell {self._assistant_name} what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        berr   = result[4] if len(result) >= 5 else ""
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw,
                               bind_error=berr)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "JARVIS_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.jarvis.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "jarvis.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    winreg.DeleteValue(reg, "JARVIS_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "JARVIS_AI", 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.jarvis.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.jarvis.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "jarvis.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  AUTO-START: ON")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._autostart_btn.setText("◉  AUTO-START: OFF")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    # ── Wake word settings ───────────────────────────────────────────────────

    def _wake_state(self) -> dict:
        """Combined state for the two wake-word buttons. Readiness is a cheap,
        deterministic on-disk check now (see core.wake_word.is_ready), so there
        is nothing to cache — the button never flickers to a stale value."""
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {"ready": bool(s.get("ready")),
                        "enabled": bool(s.get("enabled")),
                        "awake": bool(s.get("awake"))}
            except Exception:
                pass
        # Before JarvisLive has wired its callback (drawer built at startup).
        ready, enabled = False, False
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled
            ready, enabled = is_ready(), get_wake_word_enabled()
        except Exception:
            pass
        return {"ready": ready, "enabled": enabled, "awake": True}

    def _refresh_wake_btns(self):
        if not hasattr(self, '_wake_btn'):
            return
        st = self._wake_state()
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""
        self._wake_btn.setEnabled(True)
        if not st["ready"]:
            self._wake_btn.setText("⬇  WAKE WORD: DOWNLOAD")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()
        elif st["enabled"]:
            self._wake_btn.setText("🎙  WAKE WORD: ON")
            self._wake_btn.setStyleSheet(_on)
            self._wake_sleep_btn.show()
            self._wake_sleep_btn.setText("😴  SLEEP NOW" if st["awake"] else "👂  WAKE NOW")
            self._wake_sleep_btn.setStyleSheet(_off)
        else:
            self._wake_btn.setText("🎙  WAKE WORD: OFF")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()

    def _refresh_talk_btns(self):
        """Repaint the push-to-talk row from the saved setting."""
        if not hasattr(self, "_ptt_btn"):
            return
        from core.hotkey import chord_label
        from memory.config_manager import get_push_to_talk_enabled
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""

        ptt = get_push_to_talk_enabled()
        self._ptt_btn.setText(f"🎚  PUSH-TO-TALK: {chord_label()}" if ptt
                              else "🎚  PUSH-TO-TALK: OFF")
        self._ptt_btn.setStyleSheet(_on if ptt else _off)
        self._ptt_btn.setToolTip(
            "Microphone stays closed until you hold the key — nothing is sent "
            "while you are not holding it." if ptt
            else "Hold a key to talk instead of streaming the mic continuously.")


    def _refresh_hud_btn(self):
        from memory.config_manager import get_hud_style
        face = get_hud_style() == "face"
        # Neither state is "off", so both read as active — this is a choice
        # between two things, not a switch with a disabled side.
        style = f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.BORDER_A}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}"""
        self._hud_btn.setText("🧑  HUD: ANIMATED FACE" if face
                              else "◉  HUD: REACTOR CORE")
        self._hud_btn.setStyleSheet(style)
        self._hud_btn.setToolTip(
            "An animated head that speaks your words and shows what JARVIS is "
            "doing. Tap to switch to the reactor core."
            if face else
            "A reactor core that turns with the state and moves with your voice. "
            "Tap to switch to the animated head.")

    def _toggle_hud_style(self):
        """Swap the centrepiece. Both objects stay in memory, so the change is
        instant and switching back costs nothing."""
        from memory.config_manager import get_hud_style, save_hud_style
        want = "core" if get_hud_style() == "face" else "face"
        save_hud_style(want)
        try:
            self.hud.hud_style = want
            self.hud.update()
        except Exception:
            pass
        self._refresh_hud_btn()
        self._log.append_log(
            "SYS: HUD switched to the animated face." if want == "face"
            else "SYS: HUD switched to the reactor core.")

    def _toggle_ptt(self):
        from memory.config_manager import (get_push_to_talk_enabled,
                                           save_push_to_talk_enabled)
        want = not get_push_to_talk_enabled()
        save_push_to_talk_enabled(want)
        scope = None
        if self.on_push_to_talk:
            try:
                scope = self.on_push_to_talk(want)
            except Exception as e:
                self._log.append_log(f"ERR: Push-to-talk failed — {e}")
                save_push_to_talk_enabled(False)
                want = False
        self._apply_ptt_shortcut(want and scope != "global")
        self._refresh_talk_btns()

    def _apply_ptt_shortcut(self, needed: bool):
        """Bind the chord inside the window when no global hook is available.

        On macOS and Linux there is no dependency-free way to read global key
        state, so the chord is at least live whenever this window has focus.
        Qt gives no key-release for a QShortcut, so a press latches the mic open
        and a short timer closes it; held down, auto-repeat keeps pushing that
        timer out, which behaves like holding a key.
        """
        from PyQt6.QtGui import QKeySequence, QShortcut
        from core.hotkey import qt_sequence

        if not needed:
            sc = getattr(self, "_ptt_sc", None)
            if sc is not None:
                sc.setEnabled(False)
                self._ptt_sc = None
            self._ptt_hold(False)
            return
        if getattr(self, "_ptt_sc", None) is not None:
            return

        self._ptt_release = QTimer(self)
        self._ptt_release.setSingleShot(True)
        self._ptt_release.setInterval(420)
        self._ptt_release.timeout.connect(lambda: self._ptt_hold(False))

        def _press():
            self._ptt_hold(True)
            self._ptt_release.start()

        self._ptt_sc = QShortcut(QKeySequence(qt_sequence()), self)
        self._ptt_sc.setAutoRepeat(True)
        self._ptt_sc.activated.connect(_press)

    def _ptt_hold(self, held: bool):
        """Report a windowed press/release to whoever owns the microphone."""
        cb = getattr(self, "ptt_hold", None)
        if cb:
            try:
                cb(bool(held))
            except Exception:
                pass

    def _toggle_wake_word(self):
        st = self._wake_state()
        if not st["ready"]:
            # First time: download openwakeword + model in a worker thread.
            self._wake_btn.setText("⬇  DOWNLOADING… (one-time)")
            self._wake_btn.setEnabled(False)
            def _work():
                try:
                    from core.wake_word import install_and_download
                    ok, msg = install_and_download(
                        logger=lambda m: self._log_sig.emit(f"SYS: {m}"))
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)   # auto-enable after a successful download
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)
            threading.Thread(target=_work, daemon=True).start()
            return
        # Already downloaded → just flip enabled/disabled through JarvisLive.
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(not st["enabled"])
            except Exception:
                pass
        self._refresh_wake_btns()

    def _on_wake_install_done(self, ok: bool, msg: str):
        self._log_sig.emit(f"SYS: {'Wake word ready.' if ok else 'Wake word setup failed: ' + msg}")
        self._refresh_wake_btns()

    def _tap_wake_manual(self):
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception:
                pass
        self._refresh_wake_btns()

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  MORNING BRIEF: ON")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._brief_btn.setText("☀  MORNING BRIEF: OFF")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "JARVIS") or "JARVIS",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            cfg.get("voice_name", ""),
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.saved.connect(self._apply_name_update)
        ov.show()
        self._customize_overlay = ov

    def _preview_ui_color(self, hex_color: str):
        """Live preview — paints the whole interface the new colour (does NOT write to config)."""
        old = current_palette()
        if apply_ui_accent(hex_color):
            retheme_all_widgets(old, current_palette())

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = "",
                           voice: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or "JARVIS"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — {APP_VERSION}")
        self._title_lbl.setText(display)
        if display in ("JARVIS", "J.A.R.V.I.S"):
            self._sub_lbl.setText("Just A Rather Very Intelligent System")
        else:
            self._sub_lbl.setText("Personal AI Assistant")
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_accent(ui_color):
                # Live-paint the whole interface (panels, buttons, borders, HUD)
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI

        # Voice change → persist and, if it actually changed, rebuild the Live
        # session so the new voice takes effect (it's fixed at connect time).
        voice_changed = False
        if voice:
            from memory.config_manager import get_voice, save_voice
            if voice != get_voice():
                save_voice(voice)
                voice_changed = True

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
            if voice_changed:
                self._log.append_log(f"SYS: Voice set — {voice}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

        if voice_changed and self.on_voice_change:
            self.on_voice_change()

    def _centre_overlay(self, ov) -> None:
        """Place a floating overlay in the middle of the HUD and show it."""
        cw = self.centralWidget()
        ov.adjustSize()
        ov.setGeometry(
            max(0, (cw.width()  - ov.width())  // 2),
            max(0, (cw.height() - ov.height()) // 2),
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()

    # ── Audio devices ────────────────────────────────────────────────────────

    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._centre_overlay(ov)
        self._audio_overlay = ov            # keep a reference so it isn't GC'd

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Engine & providers ───────────────────────────────────────────────────

    def _open_providers(self):
        """Display engine info: Google Gemini API is the sole reasoning engine."""
        self._log.append_log(
            "SYS: Google Gemini API is the sole AI reasoning engine across JARVIS. "
            "Local and third-party LLM pipelines are disabled by architecture."
        )

    def _on_providers_applied(self):
        """Rebuild the session so the new engine takes over.

        Reuses the audio-device callback rather than adding a second one: both
        mean 'the conversation has to be rebuilt', main.py already keeps the
        transcript across such a rebuild through session resumption, and one
        path that is known to work is better than two that look alike."""
        self._log.append_log("SYS: Engine settings saved — reconnecting.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Memory panel ─────────────────────────────────────────────────────────

    def _open_memory_panel(self):
        ov = MemoryOverlay(parent=self.centralWidget())
        self._centre_overlay(ov)
        self._memory_overlay = ov

    # ── Irreversible-action confirmation ─────────────────────────────────────

    def _show_confirm_banner(self, title: str, detail: str):
        self._hide_confirm_banner()
        ov = ConfirmBanner(title, detail, parent=self.centralWidget())
        ov.answered.connect(self._on_confirm_answered)
        self._centre_overlay(ov)
        self._confirm_overlay = ov

    def _hide_confirm_banner(self):
        ov = getattr(self, "_confirm_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._confirm_overlay = None

    def _on_confirm_answered(self, accepted: bool):
        # Tear the banner down first: core.confirm.resolve() may be about to
        # shut the machine down, and a live widget mid-callback is not where you
        # want to be when that happens.
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve
            resolve(bool(accepted))
        except Exception as e:
            self._log.append_log(f"ERR: Confirmation failed — {e}")

    def _open_plugin_manager(self):
        plugins = self.get_plugins() if self.get_plugins else []
        cw = self.centralWidget()
        ov = PluginManagerOverlay(plugins, parent=cw)
        ov.adjustSize()
        ov.setGeometry(
            (cw.width()  - ov.width())  // 2,
            (cw.height() - ov.height()) // 2,
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()
        self._plugin_manager_overlay = ov   # keep a reference so it isn't GC'd

    def _open_plugin_settings(self):
        sections = self.get_plugin_settings() if self.get_plugin_settings else []
        cw = self.centralWidget()
        ov = PluginSettingsOverlay(sections, parent=cw)
        ow = PluginSettingsOverlay._OW
        oh = min(560, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.show()
        ov.raise_()
        self._plugin_settings_overlay = ov   # keep a reference so it isn't GC'd

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        # A deliberate press settles the question: whatever the video did, or is
        # about to do, the user has now said what they want.
        self._video_auto_muted = False
        self._set_muted(not self._muted)

    def _set_muted(self, muted: bool, note: str = ""):
        muted = bool(muted)
        if muted == self._muted:
            return
        self._muted = muted
        self.hud.muted = muted
        self._style_mute_btn()
        if muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted." + (f" {note}" if note else ""))
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active." + (f" {note}" if note else ""))

    def _style_mute_btn(self):
        if hasattr(self, "_hud_mute_btn"):
            if self._muted:
                self._hud_mute_btn.setText("MUTED")
                self._hud_mute_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: rgba(80, 10, 20, 220);
                        color: {C.MUTED_C};
                        border: 1px solid {C.MUTED_C};
                        border-radius: 4px; padding: 4px 8px;
                    }}
                """)
            else:
                self._hud_mute_btn.setText("MIC")
                self._hud_mute_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: rgba(30, 8, 26, 210);
                        color: {C.TEXT};
                        border: 1px solid {C.BORDER_B};
                        border-radius: 4px; padding: 4px 8px;
                    }}
                    QPushButton:hover {{
                        background: rgba(255, 77, 166, 0.25);
                        border: 1px solid {C.PRI};
                        color: #ffffff;
                    }}
                """)
        if hasattr(self, "_mute_btn"):
            if self._muted:
                self._mute_btn.setText("MICROPHONE MUTED")
                self._mute_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #140006; color: {C.MUTED_C};
                        border: 1px solid {C.MUTED_C}; border-radius: 3px;
                    }}
                """)
            else:
                self._mute_btn.setText("MICROPHONE ACTIVE")
                self._mute_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.PANEL}; color: {C.PRI};
                        border: 1px solid {C.BORDER_B}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
                """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        self.hud.set_user_text(txt)
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (str(state or "").upper() == "SPEAKING")

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._assistant_name = _read_full_config().get("assistant_name", "JARVIS") or "JARVIS"
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.")


class _RootShim:
    def __init__(self, app: QApplication, ui=None):
        self._app = app
        self._ui = ui

    def mainloop(self):
        try:
            self._app.exec()
        finally:
            if self._ui is not None:
                self._ui._is_closed = True

    def protocol(self, *_):
        pass


def _auto_ui_scale() -> float:
    """A scale for the interface from the primary screen's usable height.

    Every size in this file — 7 and 8 point text, fixed button heights, panel
    widths — was laid out on a 1080-pixel-tall screen. Windows only enlarges it
    when the user has raised display scaling, so on a 1440p monitor left at 100%
    the whole HUD came out at three-quarters size and the settings text was hard
    to read. Measured against 900 logical pixels rather than 1080 because the
    designed sizes were on the small side even there — reported as "generally
    small" on a 1440p screen already at 125%.

    Windows only, through calls that are not DPI-virtualised — so nothing about
    the process's DPI awareness is touched before Qt sets it. Elsewhere the
    system's own scaling is trusted and this returns 1."""
    if sys.platform != "win32":
        return 1.0
    try:
        import ctypes
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        hdc = user32.GetDC(0)
        try:
            physical_h = gdi32.GetDeviceCaps(hdc, 117)      # DESKTOPVERTRES
        finally:
            user32.ReleaseDC(0, hdc)
        percent = ctypes.windll.shcore.GetScaleFactorForDevice(0) or 100
        logical_h = physical_h * 100.0 / percent
    except Exception:
        return 1.0
    return max(1.0, min(2.0, round(logical_h / 900.0 * 20) / 20))


def _apply_ui_scale() -> None:
    """Set Qt's global scale factor. Must run before QApplication exists."""
    if QApplication.instance() is not None or os.environ.get("QT_SCALE_FACTOR"):
        return              # too late, or the user set it themselves
    try:
        from memory.config_manager import get_ui_scale
        scale = get_ui_scale() or _auto_ui_scale()
    except Exception:
        scale = _auto_ui_scale()
    if abs(scale - 1.0) > 0.01:
        os.environ["QT_SCALE_FACTOR"] = f"{scale:.2f}"


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        _apply_ui_scale()
        self._is_closed = False
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        self.root = _RootShim(self._app, self)
        self._win.show()

    def _is_alive(self) -> bool:
        if getattr(self, "_is_closed", False):
            return False
        try:
            return self._win is not None and not getattr(self._win, "_is_closed", False)
        except (RuntimeError, AttributeError):
            return False

    @property
    def muted(self) -> bool:
        try:
            if not self._is_alive():
                return True
            return self._win._muted
        except (RuntimeError, AttributeError):
            return True

    @muted.setter
    def muted(self, v: bool):
        try:
            if not self._is_alive():
                return
            self._win._mute_sig.emit(bool(v))
        except (RuntimeError, AttributeError):
            pass

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_audio_device_change(self):
        return self._win.on_audio_device_change

    @on_audio_device_change.setter
    def on_audio_device_change(self, cb):
        self._win.on_audio_device_change = cb

    def show_confirm(self, title: str, detail: str) -> None:
        """Thread-safe: raise the irreversible-action gate. Called from action
        handlers running in executor threads, so it goes through a signal."""
        self._win._confirm_sig.emit(str(title)[:120], str(detail)[:300])

    def hide_confirm(self) -> None:
        """Thread-safe: take the gate down."""
        self._win._confirm_hide_sig.emit()

    @property
    def get_plugins(self):
        return self._win.get_plugins

    @get_plugins.setter
    def get_plugins(self, cb):
        self._win.get_plugins = cb

    @property
    def get_plugin_settings(self):
        return self._win.get_plugin_settings

    @get_plugin_settings.setter
    def get_plugin_settings(self, cb):
        self._win.get_plugin_settings = cb

    @property
    def on_wake_toggle(self):
        return self._win.on_wake_toggle

    @on_wake_toggle.setter
    def on_wake_toggle(self, cb):
        self._win.on_wake_toggle = cb

    @property
    def on_wake_manual(self):
        return self._win.on_wake_manual

    @on_wake_manual.setter
    def on_wake_manual(self, cb):
        self._win.on_wake_manual = cb

    @property
    def wake_get_state(self):
        return self._win.wake_get_state

    @wake_get_state.setter
    def wake_get_state(self, cb):
        self._win.wake_get_state = cb

    def set_audio_level(self, level: float) -> None:
        """Thread-safe: feed a 0.0–1.0 live audio level to the HUD waveform.
        Called from the audio threads; a plain float store is atomic under the
        GIL, so no signal/lock is needed for this cosmetic value."""
        try:
            self._win.hud.set_audio_level(level)
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    @property
    def ptt_hold(self):
        return self._win.ptt_hold

    @ptt_hold.setter
    def ptt_hold(self, cb):
        self._win.ptt_hold = cb

    @property
    def on_push_to_talk(self):
        return self._win.on_push_to_talk

    @on_push_to_talk.setter
    def on_push_to_talk(self, cb):
        self._win.on_push_to_talk = cb

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: post a schedule of (level, openness, width) mouth frames
        for JARVIS's own speech. `at` is the wall-clock time the batch begins to
        sound, not the time of the call. See HudCanvas.push_visemes()."""
        try:
            self._win.hud.push_visemes(frames, hop, at)
        except Exception:
            pass

    def notify_phone_connected(self) -> None:
        try:
            if not self._is_alive():
                return
            self._win.notify_phone_connected()
        except (RuntimeError, AttributeError):
            pass

    def set_state(self, state: str):
        try:
            if not self._is_alive():
                return
            self._win._state_sig.emit(state)
        except (RuntimeError, AttributeError):
            pass

    def write_log(self, text: str):
        try:
            if not self._is_alive():
                return
            self._win._log_sig.emit(text)
        except (RuntimeError, AttributeError):
            pass

    def stream_user_text(self, text: str):
        try:
            if not self._is_alive():
                return
            self._win._stream_user_sig.emit(str(text or ""))
        except (RuntimeError, AttributeError):
            pass

    def stream_reply_text(self, text: str):
        try:
            if not self._is_alive():
                return
            self._win._stream_reply_sig.emit(str(text or ""))
        except (RuntimeError, AttributeError):
            pass

    def wait_for_api_key(self):
        while not self._is_alive() or not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        try:
            if not self._is_alive():
                return
            self._win._content_sig.emit(title[:48], text[:4000])
        except (RuntimeError, AttributeError):
            pass

    def show_code_job(self, job: dict) -> None:
        """Thread-safe: draw the coding agent's live progress in the panel."""
        try:
            if not self._is_alive():
                return
            self._win._codejob_sig.emit(dict(job))
        except (RuntimeError, AttributeError):
            pass

    def show_quiz(self, topic: str, questions, grade=None) -> None:
        """Thread-safe: put an interactive quiz on the board."""
        try:
            if not self._is_alive():
                return
            self._win._quiz_sig.emit(str(topic or ""), list(questions or []), grade)
        except (RuntimeError, AttributeError):
            pass

    def hide_quiz(self) -> None:
        """Thread-safe: clear any quiz currently on the board."""
        try:
            if not self._is_alive():
                return
            self._win._quiz_hide_sig.emit()
        except (RuntimeError, AttributeError):
            pass

    def show_review(self, title: str, summary: str, findings, unclear=None) -> None:
        """Thread-safe: lay a document review into the panel below the HUD."""
        try:
            if not self._is_alive():
                return
            self._win._review_sig.emit(str(title or ""), str(summary or ""),
                                       list(findings or []), list(unclear or []))
        except (RuntimeError, AttributeError):
            pass

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        try:
            if not self._is_alive():
                return
            self._win._ready = False
            self._win._reconfig_sig.emit()
        except (RuntimeError, AttributeError):
            pass

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        try:
            if not self._is_alive():
                return
            self._win._camera_sig.emit(img_bytes)
        except (RuntimeError, AttributeError):
            pass

    def show_video(self, source: str, title: str = "", muted: bool = True,
                   audio_source: str = "") -> None:
        """Thread-safe: play a video where the avatar normally is."""
        try:
            if not self._is_alive():
                return
            self._win._video_open_sig.emit(str(source or ""), str(title or ""),
                                           bool(muted), str(audio_source or ""))
        except (RuntimeError, AttributeError):
            pass

    def stop_video(self) -> None:
        """Thread-safe: close the video and give the HUD back to the avatar."""
        try:
            if not self._is_alive():
                return
            self._win._video_close_sig.emit()
        except (RuntimeError, AttributeError):
            pass

    def set_video_muted(self, muted: bool) -> None:
        """Thread-safe: silence or unsilence whatever is playing."""
        try:
            if not self._is_alive():
                return
            self._win._video_mute_sig.emit(bool(muted))
        except (RuntimeError, AttributeError):
            pass

    def video_is_playing(self) -> bool:
        try:
            if not self._is_alive():
                return False
            return bool(self._win.video_is_playing())
        except (RuntimeError, AttributeError):
            return False

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        try:
            if not self._is_alive():
                return
            self._win.start_camera_stream()
        except (RuntimeError, AttributeError):
            pass

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        try:
            if not self._is_alive():
                return
            self._win.stop_camera_stream()
        except (RuntimeError, AttributeError):
            pass

    @property
    def assistant_name(self) -> str:
        try:
            if not self._is_alive():
                return "JARVIS"
            return self._win._assistant_name
        except (RuntimeError, AttributeError):
            return "JARVIS"

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")

    def open_display(self, content_type: str, source: str, title: str = "") -> int:
        """Thread-safe: Open an image or video in a new named floating display window."""
        try:
            if not self._is_alive():
                return 0
            self._win._display_counter += 1
            disp_id = self._win._display_counter
            self._win._open_display_sig.emit(str(content_type), str(source), f"{disp_id}:{title}")
            return disp_id
        except (RuntimeError, AttributeError):
            return 0

    def focus_display(self, display_id: int) -> bool:
        """Thread-safe: Focus display N and bring to front in center of screen."""
        try:
            if not self._is_alive():
                return False
            self._win._focus_display_sig.emit(int(display_id))
            return True
        except (RuntimeError, AttributeError):
            return False

    def close_display_window(self, display_id: int = 0) -> bool:
        """Thread-safe: Close display N (or 0 for all)."""
        try:
            if not self._is_alive():
                return False
            self._win._close_display_sig.emit(int(display_id))
            return True
        except (RuntimeError, AttributeError):
            return False

    def get_open_displays(self) -> list[dict]:
        """Thread-safe query for currently open displays."""
        try:
            if not self._is_alive():
                return []
            res = []
            for d_id, w in list(self._win._displays.items()):
                res.append({
                    "display_id": d_id,
                    "name": getattr(w, "display_name", f"DISPLAY {d_id}"),
                    "type": getattr(w, "content_type", "image"),
                    "title": getattr(w, "title_text", ""),
                })
            return res
        except (RuntimeError, AttributeError):
            return []
