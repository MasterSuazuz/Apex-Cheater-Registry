"""Захват экрана и запись «повтора» как в ShadowPlay.

Кадры идут в ffmpeg, который пишет 2-секундные сегменты .ts во временную папку.
Старые сегменты удаляются, поэтому на диске всегда лежат последние N секунд.
При отметке момента сегменты вокруг неё склеиваются в .mp4 без перекодирования,
и к ним подклеивается звук (игра + микрофон) из кольцевого буфера audio.AudioRecorder.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import imageio_ffmpeg
import numpy as np

from .audio import AudioRecorder
from .config import Settings
from .display import CaptureTarget
from .i18n import t

SEGMENT_SECONDS = 2
_NO_WINDOW = subprocess.CREATE_NO_WINDOW

_encoder_cache: dict[str, bool] = {}


def ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def nvenc_available() -> bool:
    """Проверяем пробным кодированием: в списке nvenc есть даже без видеокарты NVIDIA."""
    if "nvenc" not in _encoder_cache:
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
               "-i", "color=c=black:s=256x256:r=30", "-frames:v", "5",
               "-c:v", "h264_nvenc", "-f", "null", "-"]
        try:
            ok = subprocess.run(cmd, capture_output=True, timeout=15, creationflags=_NO_WINDOW).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        _encoder_cache["nvenc"] = ok
    return _encoder_cache["nvenc"]


def pick_encoder(choice: str) -> str:
    if choice == "x264":
        return "x264"
    if choice == "nvenc" or nvenc_available():
        return "nvenc" if nvenc_available() else "x264"
    return "x264"


def output_size(target: CaptureTarget, resolution: str) -> tuple[int, int]:
    w, h = target.width, target.height
    if resolution == "native" or not resolution.isdigit() or int(resolution) >= h:
        return w & ~1, h & ~1
    out_h = int(resolution)
    out_w = round(w * out_h / h)
    return out_w & ~1, out_h & ~1


def trim_clip(src: Path, dst: Path, start: float, end: float, quality: int = 23) -> bool:
    """Вырезать кусок [start, end] сек. Перекодируем — тогда обрез точный до кадра, а не до ключевого."""
    enc = (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(quality), "-b:v", "0"]
           if nvenc_available() else ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(quality)])
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src),
           "-t", f"{max(0.1, end - start):.3f}", *enc, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", str(dst)]
    res = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
    return res.returncode == 0 and dst.exists() and dst.stat().st_size > 0


@dataclass
class MarkRequest:
    id: str
    stream_time: float          # секунда записи, на которой нажали отметку
    wall_time: float
    frame: np.ndarray | None    # кадр в момент отметки (для превью)
    payload: dict


@dataclass
class MarkResult:
    request: MarkRequest
    clip_path: Path | None
    frame_path: Path | None
    mark_offset: float          # где внутри клипа находится момент, сек
    error: str = ""


class _FrameSource:
    def open(self, target: CaptureTarget, fps: int) -> None: ...
    def read(self) -> np.ndarray | None: ...
    def close(self) -> None: ...


class _DxcamSource(_FrameSource):
    name = "DXGI (dxcam)"

    def open(self, target, fps):
        import dxcam
        factory = getattr(dxcam, "__factory")
        device_idx = output_idx = None
        for d_i, outputs in enumerate(factory.outputs):
            for o_i, out in enumerate(outputs):
                if int(out.hmonitor or 0) == target.monitor.handle:
                    device_idx, output_idx = d_i, o_i
        if device_idx is None:
            raise RuntimeError(t("dxcam не нашёл этот монитор"))
        self._cam = dxcam.create(device_idx=device_idx, output_idx=output_idx, output_color="BGR")
        self._cam.start(region=target.region_on_monitor, target_fps=fps, video_mode=True)

    def read(self):
        return self._cam.get_latest_frame()

    def close(self):
        cam = getattr(self, "_cam", None)
        if cam is not None:
            cam.stop()
            cam.release()
            self._cam = None


class _MssSource(_FrameSource):
    name = "GDI (mss)"

    def open(self, target, fps):
        import mss
        self._sct = mss.mss()
        self._box = {"left": target.left, "top": target.top, "width": target.width, "height": target.height}
        self._period = 1.0 / fps
        self._next = time.perf_counter()

    def read(self):
        # Сами держим постоянную частоту кадров, иначе ffmpeg получит «рваное» время.
        delay = self._next - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        self._next = max(self._next + self._period, time.perf_counter() - self._period)
        shot = np.asarray(self._sct.grab(self._box))
        return np.ascontiguousarray(shot[:, :, :3])

    def close(self):
        sct = getattr(self, "_sct", None)
        if sct is not None:
            sct.close()
            self._sct = None


class CaptureEngine:
    def __init__(self, settings: Settings,
                 on_status: Callable[[str], None],
                 on_mark_done: Callable[[MarkResult], None]):
        self.settings = settings
        self._on_status = on_status
        self._on_mark_done = on_mark_done
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pending: list[MarkRequest] = []
        self._finishing: list[MarkRequest] = []   # клип собирается — его сегменты не стираем
        self._proc: subprocess.Popen | None = None
        self.session_dir: Path | None = None
        self.frames_written = 0
        self.actual_fps = 0.0
        self.encoder = ""
        self.source_name = ""
        self.target: CaptureTarget | None = None
        self.out_size = (0, 0)
        self.latest_frame: np.ndarray | None = None
        self.last_error = ""
        self.fps = settings.capture.fps     # фактическая частота записи (GDI ограничен 30 к/с)
        # Получает каждый записанный кадр: (кадр, секунда записи, к/с). Должен возвращаться мгновенно.
        self.frame_listener: Callable[[np.ndarray, float, float], None] | None = None
        self.audio: AudioRecorder | None = None
        # Секунда записи -> время часов perf_counter (по ним подписан звук).
        self._clock: deque[tuple[float, float]] = deque(maxlen=4000)

    # --- управление -------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stream_time(self) -> float:
        return self.frames_written / max(1, self.fps)

    def start(self, target: CaptureTarget) -> None:
        if self.running:
            self.stop()
        self.target = target
        self._stop.clear()
        self.last_error = ""
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._thread = None

    def mark(self, payload: dict, at_time: float | None = None) -> MarkRequest | None:
        """at_time — секунда записи, если момент был чуть раньше (автоанализ отстаёт на доли секунды)."""
        if not self.running:
            return None
        frame = self.latest_frame
        now = self.stream_time
        t = now if at_time is None else min(now, max(0.0, at_time))
        req = MarkRequest(uuid.uuid4().hex[:8], t, time.time() - (now - t),
                          None if frame is None else frame.copy(), payload)
        with self._lock:
            self._pending.append(req)
        return req

    # --- рабочий поток ----------------------------------------------------

    def _make_source(self) -> _FrameSource:
        backend = self.settings.capture.backend
        self.fps = self.settings.capture.fps
        if backend in ("auto", "dxcam"):
            src = _DxcamSource()
            try:
                src.open(self.target, self.fps)
                return src
            except Exception as exc:  # dxcam падает по-разному на разных драйверах
                src.close()
                if backend == "dxcam":
                    raise
                self._on_status(t("DXGI недоступен, переключаюсь на GDI") + f" ({exc})")
        # GDI не успевает выдавать 60 к/с на 1080p, а нехватку кадров видео превратило бы в ускорение.
        self.fps = min(self.fps, 30)
        src = _MssSource()
        src.open(self.target, self.fps)
        return src

    def _ffmpeg_cmd(self, width_in, height_in) -> list[str]:
        rec = self.settings.recording
        fps = self.fps
        out_w, out_h = self.out_size
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width_in}x{height_in}",
               "-r", str(fps), "-i", "-"]
        if (out_w, out_h) != (width_in, height_in):
            cmd += ["-vf", f"scale={out_w}:{out_h}:flags=bicubic"]
        if self.encoder == "nvenc":
            cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(rec.quality),
                    "-b:v", "0", "-forced-idr", "1"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(rec.quality)]
        # Ключевой кадр ровно на границе каждого сегмента — тогда склейка без перекодирования точна.
        cmd += ["-pix_fmt", "yuv420p", "-g", str(fps * SEGMENT_SECONDS),
                "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_SECONDS})",
                "-f", "segment", "-segment_time", str(SEGMENT_SECONDS), "-segment_format", "mpegts",
                "-reset_timestamps", "1", str(self.session_dir / "seg_%06d.ts")]
        return cmd

    def _run(self) -> None:
        source: _FrameSource | None = None
        try:
            self.session_dir = Path(self.settings.paths.temp_dir) / time.strftime("session_%Y%m%d_%H%M%S")
            self.session_dir.mkdir(parents=True, exist_ok=True)
            self.encoder = pick_encoder(self.settings.recording.encoder)
            source = self._make_source()
            self.source_name = source.name
            first = source.read()
            if first is None:
                raise RuntimeError(t("Не удалось получить кадр с экрана"))
            in_h, in_w = first.shape[:2]
            self.out_size = output_size(self.target, self.settings.recording.resolution)
            self._proc = subprocess.Popen(self._ffmpeg_cmd(in_w, in_h), stdin=subprocess.PIPE,
                                          stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
            self.frames_written = 0
            self._clock.clear()
            self._start_audio()
            self._on_status(t("Запись идёт") + f": {in_w}×{in_h} → {self.out_size[0]}×{self.out_size[1]}, "
                            f"{self.fps} {t('к/с')}, {self.encoder.upper()}, {source.name}")
            frame = first
            fps_t0, fps_n = time.perf_counter(), 0
            while not self._stop.is_set():
                if frame is not None:
                    if frame.shape[0] != in_h or frame.shape[1] != in_w:
                        frame = cv2.resize(frame, (in_w, in_h))
                    self._proc.stdin.write(np.ascontiguousarray(frame).tobytes())
                    self.frames_written += 1
                    if self.frames_written % max(1, self.fps // 4) == 1:
                        self._clock.append((self.stream_time, time.perf_counter()))
                    self.latest_frame = frame
                    if self.frame_listener is not None:
                        self.frame_listener(frame, self.stream_time, self.fps)
                    fps_n += 1
                now = time.perf_counter()
                if now - fps_t0 >= 1.0:
                    self.actual_fps = fps_n / (now - fps_t0)
                    fps_t0, fps_n = now, 0
                    self._housekeeping(final=False)
                frame = source.read()
        except Exception as exc:
            self.last_error = str(exc)
            self._on_status(t("Ошибка захвата") + f": {exc}")
        finally:
            if source is not None:
                source.close()
            if self.audio is not None:
                self.audio.stop()      # буфер звука остаётся в памяти для последних отметок
            self._close_ffmpeg()
            self._housekeeping(final=True)
            self.actual_fps = 0.0
            self.latest_frame = None
            if not self.last_error:
                self._on_status(t("Запись остановлена"))

    def _start_audio(self) -> None:
        a = self.settings.audio
        self.audio = None
        if not (a.system or a.microphone):
            return
        rec = self.settings.recording
        self.audio = AudioRecorder(a.system, a.microphone, a.speaker_device, a.mic_device, a.mic_volume,
                                   keep_seconds=rec.buffer_seconds + rec.seconds_after + 30)
        self.audio.start()

    def stream_to_clock(self, t: float, clock=None) -> float | None:
        pts = list(clock if clock is not None else self._clock)
        if len(pts) < 2:
            return None
        xs, ys = zip(*pts)
        if t >= xs[-1]:
            return ys[-1] + (t - xs[-1])
        if t <= xs[0]:
            return ys[0] - (xs[0] - t)
        return float(np.interp(t, xs, ys))

    def _close_ffmpeg(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
            proc.wait(timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
        err = proc.stderr.read().decode("utf-8", "replace").strip() if proc.stderr else ""
        if proc.returncode not in (0, None) and err and not self.last_error:
            self.last_error = err.splitlines()[-1]
            self._on_status(f"ffmpeg: {self.last_error}")

    # --- отметки и чистка буфера -----------------------------------------

    def _housekeeping(self, final: bool) -> None:
        if self.session_dir is None:
            return
        rec = self.settings.recording
        now_t = self.stream_time
        with self._lock:
            ready = [r for r in self._pending
                     if final or now_t >= r.stream_time + rec.seconds_after + SEGMENT_SECONDS + 0.5]
            self._pending = [r for r in self._pending if r not in ready]
            self._finishing += ready
            keep_from = min([r.stream_time - rec.seconds_before for r in self._pending + self._finishing],
                            default=now_t)
        for req in ready:
            threading.Thread(target=self._finish_mark, args=(req, self.audio, list(self._clock)),
                             daemon=True).start()

        keep_from = min(keep_from, now_t - rec.buffer_seconds)
        first_kept = int(max(0.0, keep_from) // SEGMENT_SECONDS) - 1
        if final:
            # Сессию удаляем целиком, когда все отметки уже сохранены (см. _finish_mark).
            threading.Thread(target=self._cleanup_session, args=(self.session_dir,), daemon=True).start()
            return
        for seg in self.session_dir.glob("seg_*.ts"):
            try:
                if int(seg.stem[4:]) < first_kept:
                    seg.unlink()
            except (ValueError, OSError):
                pass

    def _finish_mark(self, req: MarkRequest, audio: AudioRecorder | None = None, clock=None) -> None:
        try:
            self._build_clip(req, audio, clock)
        finally:
            with self._lock:
                if req in self._finishing:
                    self._finishing.remove(req)

    def _build_clip(self, req: MarkRequest, audio: AudioRecorder | None, clock) -> None:
        rec = self.settings.recording
        session = self.session_dir
        start_t = max(0.0, req.stream_time - rec.seconds_before)
        first_seg = int(start_t // SEGMENT_SECONDS)
        last_seg = int((req.stream_time + rec.seconds_after) // SEGMENT_SECONDS)
        segs = [session / f"seg_{i:06d}.ts" for i in range(first_seg, last_seg + 1)]
        segs = [s for s in segs if s.exists() and s.stat().st_size > 0]
        out_dir = session / "marks"
        out_dir.mkdir(exist_ok=True)
        frame_path = None
        if req.frame is not None:
            frame_path = out_dir / f"{req.id}.jpg"
            cv2.imwrite(str(frame_path), req.frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not segs:
            self._on_mark_done(MarkResult(req, None, frame_path, 0.0, t("В буфере нет видео для этого момента")))
            return
        list_file = out_dir / f"{req.id}.txt"
        list_file.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs), encoding="utf-8")
        clip = out_dir / f"{req.id}.mp4"
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
               "-i", str(list_file), "-c", "copy", "-movflags", "+faststart", str(clip)]
        res = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
        first_existing = int(segs[0].stem[4:])
        offset = req.stream_time - first_existing * SEGMENT_SECONDS
        if res.returncode != 0 or not clip.exists():
            err = res.stderr.decode("utf-8", "replace").strip().splitlines()
            self._on_mark_done(MarkResult(req, None, frame_path, offset, err[-1] if err else "ffmpeg error"))
            return
        # Клип не должен захватывать предыдущего игрока: начинаем с момента, когда на экране этот.
        since = req.payload.get("since")
        cut = (since - first_existing * SEGMENT_SECONDS) if since is not None else 0.0
        applied = 0.0
        if cut > 0.3 and cut < offset:
            trimmed = out_dir / f"{req.id}_cut.mp4"
            cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{cut:.3f}", "-i", str(clip),
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", str(rec.quality), "-an",
                   "-movflags", "+faststart", str(trimmed)]
            if subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW).returncode == 0 and trimmed.exists():
                clip, offset, applied = trimmed, offset - cut, cut
        if audio is not None:
            start = first_existing * SEGMENT_SECONDS + applied
            clip = self._add_audio(req, clip, audio, clock, start, len(segs) * SEGMENT_SECONDS - applied + 1)
        self._on_mark_done(MarkResult(req, clip, frame_path, offset))

    def _add_audio(self, req: MarkRequest, clip: Path, audio: AudioRecorder, clock, start: float,
                   duration: float) -> Path:
        t0 = self.stream_to_clock(start, clock)
        if t0 is None:
            return clip
        wav = clip.with_name(f"{req.id}.wav")
        if not audio.write_wav(t0, t0 + duration, wav):
            return clip
        out = clip.with_name(f"{req.id}_av.mp4")
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(clip), "-i", str(wav),
               "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
               "-movflags", "+faststart", str(out)]
        ok = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW).returncode == 0 and out.exists()
        wav.unlink(missing_ok=True)
        return out if ok else clip

    def _cleanup_session(self, session: Path) -> None:
        # Даём время на сохранение последних клипов в дело, потом чистим.
        time.sleep(60)
        if session != self.session_dir or not self.running:
            shutil.rmtree(session, ignore_errors=True)
