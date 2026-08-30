"""Floating record button — frontend GUI di atas core yang sama (session lock,
antrian, worker). Logika state di ButtonState (diuji unit); tkinter hanya shell."""
from __future__ import annotations

import threading
import time

try:  # tkinter opsional saat headless (WSL test) — GUI hanya butuh display
    import tkinter as tk
    from tkinter import messagebox
except ImportError:  # pragma: no cover
    tk = None
    messagebox = None

from . import config, indexmd, paths, queuer, recorder, recover, session

SIZE = 56
BG = "#1e1e1e"
IDLE_COLOR = "#888888"
REC_COLOR = "#e53935"
OK_COLOR = "#43a047"
TICK_MS = 120


class ButtonState:
    """State machine murni: idle → recording → (stop) → idle."""

    def __init__(self) -> None:
        self.phase = "idle"
        self.t0 = 0.0
        self.source: str | None = None
        self.job_id: str | None = None

    def start(self, source: str, job_id: str) -> None:
        self.phase = "recording"
        self.source = source
        self.job_id = job_id
        self.t0 = time.monotonic()

    @property
    def elapsed_sec(self) -> float:
        return time.monotonic() - self.t0 if self.phase == "recording" else 0.0

    def stop(self) -> float:
        el = self.elapsed_sec
        self.phase = "idle"
        return el


def _fmt(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


class FloatingButton:
    def __init__(self) -> None:
        if tk is None:
            raise RuntimeError("tkinter tidak tersedia — install python3-tk / Python dengan Tcl/Tk")
        self.state = ButtonState()
        self.rec_thread: threading.Thread | None = None
        self.rec_meta: dict | None = None

        self.root = tk.Tk()
        self.root.title("meeting")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry(f"{SIZE}x{SIZE}")

        cfg = config.load()
        x, y = cfg.get("button_x"), cfg.get("button_y")
        if isinstance(x, int) and isinstance(y, int):
            self.root.geometry(f"+{x}+{y}")

        self.root.configure(bg=BG)
        self.canvas = tk.Canvas(self.root, width=SIZE, height=SIZE, bg=BG,
                                highlightthickness=0)
        self.canvas.pack()
        # NB: <Button-1> dan <ButtonPress-1> adalah sinonim Tk — jangan bind dua-duanya.
        # Klik dideteksi di <ButtonRelease-1> dengan threshold drag: tekan-tahan
        # tanpa gerak >= threshold = klik; gerak = drag.
        self._press_pos = (0, 0)
        self._drag_offset = (0, 0)
        self._dragged = False
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)  # praktis tak terpanggil utk overrideredirect; posisi disimpan saat drag

        self._pulse_t = 0.0
        self._tick()

    # ---------- interaksi ----------

    def _on_press(self, ev):
        self._press_pos = (self.root.winfo_pointerx(), self.root.winfo_pointery())
        self._drag_offset = (ev.x, ev.y)  # offset dari pojok jendela — drag tanpa lompat
        self._dragged = False

    def _on_drag(self, ev):
        if not self._dragged:
            px, py = self._press_pos
            if abs(self.root.winfo_pointerx() - px) + abs(self.root.winfo_pointery() - py) < 5:
                return  # belum lewat threshold — belum dianggap drag
            self._dragged = True
        dx, dy = self._drag_offset
        x = self.root.winfo_pointerx() - dx
        y = self.root.winfo_pointery() - dy
        self.root.geometry(f"+{x}+{y}")

    def _on_release(self, ev):
        if self._dragged:
            # simpan posisi setiap selesai drag — jendela overrideredirect tidak
            # menerima WM_DELETE_WINDOW, jadi tidak ada momen "close" yang bisa dipakai
            config.update(button_x=self.root.winfo_x(), button_y=self.root.winfo_y())
            return
        # tekan-tanpa-drag = klik
        if self.state.phase == "idle":
            self._start_recording()
        elif self.state.phase == "recording":
            self._stop_recording()

    def _on_close(self):
        config.update(button_x=self.root.winfo_x(), button_y=self.root.winfo_y())
        if self.state.phase == "recording":
            session.request_stop()  # recorder proses lain menulis WAV tetap
        self.root.destroy()

    def _start_recording(self):
        recover.offer_recovery(
            ui_confirm=lambda msg: messagebox.askyesno("Rekaman yatim", msg)
        )
        cfg = config.load()
        source = cfg.get("default_source", "mic")
        job_id = queuer.new_job_id()
        try:
            session.start({
                "source": source, "id": job_id, "wav_paths": [],
                "device": cfg.get("mic_device") or "default",
            })
        except session.SessionActiveError as e:
            messagebox.showwarning("Sesi aktif", str(e))
            return
        self.state.start(source, job_id)
        self.rec_meta = None
        self.rec_thread = threading.Thread(
            target=self._record_worker, args=(source, job_id, cfg), daemon=True
        )
        self.rec_thread.start()

    def _record_worker(self, source, job_id, cfg):
        try:
            self.rec_meta = recorder.record(source, job_id, mic_device=cfg.get("mic_device"))
        except Exception:
            # device gagal total (RecorderError, import, disk) — jangan biarkan
            # tombol wedged merah selamanya; route ke jalur buang yang sama
            self.rec_meta = {"duration_sec": 0, "wav_paths": [], "device": "error", "device_lost": True}
        try:
            self.root.after(0, self._after_record_stopped)
        except Exception:
            pass  # root sudah di-destroy — sesi yatim ditangani recovery saat klik berikutnya

    def _stop_recording(self):
        session.request_stop()

    def _after_record_stopped(self):
        meta = self.rec_meta or {"duration_sec": 0, "wav_paths": [], "device": "?"}
        if not meta.get("wav_paths"):
            session.finish()
            self.state.stop()
            return
        wav_rel = [str(w.relative_to(paths.home() / "data")) for w in meta["wav_paths"]]
        session_meta = session.finish()
        job = {
            "id": self.state.job_id,
            "created_at": session_meta.get("started_at"),
            "source": self.state.source,
            "audio": f"recordings/{self.state.job_id}.flac",
            "wav": wav_rel[0] if len(wav_rel) == 1 else wav_rel,
            "original_path": None,
            "needs_finalization": True,
            "duration_sec": meta["duration_sec"],
            "device": meta["device"],
            "model": config.load()["model"],
            "status": "pending",
        }
        queuer.enqueue(job)
        indexmd.upsert({
            "id": job["id"],
            "tanggal": (job["created_at"] or "").replace("T", " "),
            "durasi": indexmd.fmt_duration(job["duration_sec"]),
            "status": "pending",
            "path": f"data/output/{job['id']}/transcript.md",
        })
        queuer.spawn_worker()
        self.state.stop()
        self._flash_saved()

    # ---------- render ----------

    def _flash_saved(self):
        self._saved_until = time.monotonic() + 1.5

    def _tick(self):
        self._pulse_t += 0.12
        c = self.canvas
        c.delete("all")
        w, h = SIZE, SIZE
        if self.state.phase == "recording":
            r = 14 + 4 * (0.5 + 0.5 * ((self._pulse_t * 2) % 2 - 1))
            c.create_oval(w/2 - r, h/2 - r, w/2 + r, h/2 + r, fill=REC_COLOR, outline="")
            c.create_text(w/2, h - 10, text=_fmt(self.state.elapsed_sec),
                          fill="white", font=("Segoe UI", 8))
        elif getattr(self, "_saved_until", 0) > time.monotonic():
            r = 16
            c.create_oval(w/2 - r, h/2 - r, w/2 + r, h/2 + r, fill=OK_COLOR, outline="")
            c.create_text(w/2, h/2, text="✓", fill="white", font=("Segoe UI", 12, "bold"))
        else:
            r = 12
            c.create_oval(w/2 - r, h/2 - r, w/2 + r, h/2 + r, fill=IDLE_COLOR, outline="")
        self.root.after(TICK_MS, self._tick)

    def run(self):
        self.root.mainloop()


def run() -> None:
    FloatingButton().run()
