#!/usr/bin/env python3
"""ojota — daemon de captura y detección de movimiento.

Una sola conexión RTSP al substream para detectar (decodifica en baja
resolución), otra al stream principal que se copia sin re-encodear a un
ring buffer de segmentos. Cuando hay movimiento se arma un clip mp4 con
pre-captura y se llama al hook.
"""

import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
import logging

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_PATH = os.environ.get(
    "OJOTA_CONF", os.path.join(ROOT, "config", "ojota.conf")
)

SEG_RE = re.compile(r"seg-(\d{8}-\d{6})\.ts$")
CRED_RE = re.compile(r"(://)[^/@\s]+@")


def _redact(text):
    """Oculta user:pass en URLs (rtsp://user:pass@host -> rtsp://***@host)."""
    return CRED_RE.sub(r"\1***@", text)


def _parse_roi(s):
    """'left,top,right,bottom' en fracciones 0-1 -> tupla validada."""
    try:
        l, t, r, b = (float(x) for x in s.split(","))
        l, r = sorted((max(0.0, l), min(1.0, r)))
        t, b = sorted((max(0.0, t), min(1.0, b)))
        if r - l < 0.05 or b - t < 0.05:
            return (0.0, 0.0, 1.0, 1.0)
        return (l, t, r, b)
    except Exception:  # noqa: BLE001
        return (0.0, 0.0, 1.0, 1.0)


def load_conf(path):
    conf = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            conf[key.strip()] = val.strip().strip('"').strip("'")
    return conf


class Conf:
    def __init__(self, d):
        self.rtsp_url = d["RTSP_URL"]
        self.rtsp_sub = d.get("RTSP_SUBSTREAM_URL") or d["RTSP_URL"]
        self.transport = d.get("RTSP_TRANSPORT", "tcp")
        self.detect_fps = int(d.get("DETECT_FPS", 3))
        self.detect_width = int(d.get("DETECT_WIDTH", 320))
        self.pixel_delta = int(d.get("PIXEL_DELTA", 18))
        self.area_pct = float(d.get("MOTION_AREA_PCT", 1.5))
        self.min_frames = int(d.get("MOTION_MIN_FRAMES", 3))
        self.light_pct = float(d.get("LIGHT_CHANGE_PCT", 70))
        self.warmup_s = float(d.get("DETECT_WARMUP_SECONDS", 10))
        self.roi = _parse_roi(d.get("DETECT_ROI", "0,0,1,1"))
        self.default_profile = d.get("DEFAULT_PROFILE", "armado")
        self.home = d.get("OJOTA_HOME") or ROOT
        self.profile_file = os.path.join(self.home, "config", "profile")
        self.segment_s = int(d.get("SEGMENT_SECONDS", 4))
        self.precapture_s = int(d.get("PRECAPTURE_SECONDS", 8))
        self.postcapture_s = int(d.get("POSTCAPTURE_SECONDS", 10))
        self.buffer_s = int(d.get("BUFFER_RETENTION_SECONDS", 60))
        self.cam_down_min = float(d.get("CAMERA_DOWN_ALERT_MINUTES", 5))
        self.notify_silence_min = float(d.get("NOTIFY_SILENCE_MINUTES", 5))
        self.instant_notify_delay_s = float(
            d.get("INSTANT_NOTIFY_DELAY_SECONDS", 45))
        self.live_upload = d.get("LIVE_UPLOAD", "1") == "1"
        self.pending_retry_min = float(d.get("PENDING_RETRY_MINUTES", 3))
        self.retention_check_h = float(d.get("RETENTION_CHECK_HOURS", 24))
        self.rclone_dest = "%s:%s" % (d.get("RCLONE_REMOTE", "gdrive"),
                                      d.get("RCLONE_PATH", "ojota"))
        self.config_backup = d.get("CONFIG_BACKUP", "1") == "1"
        self.heartbeat_url = d.get("HEARTBEAT_URL", "").strip()
        self.heartbeat_min = float(d.get("HEARTBEAT_MINUTES", 15))
        self.burst_count = int(d.get("EVENT_BURST_COUNT", 10))
        self.burst_min = float(d.get("EVENT_BURST_MINUTES", 15))
        self.phone_ip = d.get("PHONE_IP", "").strip()
        self.presence_poll_s = float(d.get("PRESENCE_POLL_SECONDS", 10))
        self.presence_away_min = float(d.get("PRESENCE_AWAY_MINUTES", 2))
        self.return_grace_s = float(d.get("RETURN_GRACE_SECONDS", 120))
        self.manual_hold = os.path.join(self.home, "config", "manual-hold")
        self.return_window = os.path.join(self.home, "clips", ".return-window")
        self.buffer_dir = os.path.join(self.home, "clips", "buffer")
        self.pending_dir = os.path.join(self.home, "clips", "pending")
        self.log_dir = os.path.join(self.home, "logs")
        self.hook = os.path.join(self.home, "bin", "ojota-clip-hook.sh")
        self.notify_script = os.path.join(self.home, "bin", "ojota-notify.sh")
        self.cli = os.path.join(self.home, "bin", "ojota")


def setup_logging(log_dir):
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("ojota")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(os.path.join(log_dir, "ojota.log"),
                             maxBytes=2_000_000, backupCount=5)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


class Daemon:
    def __init__(self, conf, log):
        self.c = conf
        self.log = log
        self.stop = threading.Event()
        self.lock = threading.Lock()
        # estado de evento
        self.event_active = False
        self.event_start = 0.0
        self.last_motion = 0.0
        # salud de la cámara
        self.last_frame_ts = time.time()
        self.cam_down_notified = False
        self.procs = []
        # anti-burst / avisos
        self.event_times = []
        self.last_burst_alert = 0.0
        self.last_instant_notify = 0.0
        self.instant_timer = None
        # estado armado / desarmado
        self.profile = None
        self.active_roi = conf.roi
        self.notify_enabled = True
        self.capture_enabled = True   # armado=True, desarmado=False
        self._apply_profile(self._read_profile(), initial=True)

    # ── estado armado / desarmado ──────────────────────────────────
    def _read_profile(self):
        try:
            val = open(self.c.profile_file).read().strip().lower()
            if val in ("armado", "desarmado"):
                return val
            if val in ("casa", "afuera"):   # compat con archivos viejos
                return "armado" if val == "afuera" else "desarmado"
        except FileNotFoundError:
            pass
        return self.c.default_profile

    def _apply_profile(self, name, initial=False):
        if name == self.profile:
            return
        self.profile = name
        want_capture = name == "armado"
        self.notify_enabled = want_capture
        if want_capture != self.capture_enabled:
            self.capture_enabled = want_capture
            if want_capture:
                self.last_frame_ts = time.time()   # gracia para el health
                if not initial:
                    self.log.info("ARMADO: reanudo la captura")
            else:
                if not initial:
                    self.log.info("DESARMADO: captura en pausa")
                with self.lock:
                    self.event_active = False
                for p in list(self.procs):
                    try:
                        p.terminate()
                    except Exception:  # noqa: BLE001
                        pass
        elif not initial:
            self.log.info("estado -> %s", name)

    def _profile_watch(self):
        # el archivo lo escribe `bin/ojota salir|volver` (usuario) o el
        # hilo de presencia. El daemon solo lo lee.
        while not self.stop.is_set():
            self._apply_profile(self._read_profile())
            self.stop.wait(2)

    def _write_profile(self, name):
        try:
            with open(self.c.profile_file, "w") as fh:
                fh.write(name + "\n")
        except OSError as exc:
            self.log.error("no pude escribir el perfil: %s", exc)

    # ── presencia del celu en la WiFi ─────────────────────────────
    def _presence_watch(self):
        if not self.c.phone_ip:
            return
        self.log.info("presencia: %s cada %gs (armo tras %g min sin verlo)",
                      self.c.phone_ip, self.c.presence_poll_s,
                      self.c.presence_away_min)
        last_seen = time.time()
        was_home = True
        while not self.stop.is_set():
            if self._ping(self.c.phone_ip):
                last_seen = time.time()
                if not was_home:
                    was_home = True
                    if os.path.exists(self.c.manual_hold):
                        self.log.info("presencia: volviste (hold manual, "
                                      "no desarmo)")
                    else:
                        self.log.info("presencia: volviste → DESARMADO")
                        self._write_profile("desarmado")
                        self._on_return()
            elif was_home:
                gap = time.time() - last_seen
                if gap > self.c.presence_away_min * 60:
                    was_home = False
                    self.log.info("presencia: sin verte hace %.0f min → "
                                  "ARMADO", gap / 60)
                    self._clear_manual_hold()
                    self._write_profile("armado")
            self.stop.wait(self.c.presence_poll_s)

    @staticmethod
    def _ping(ip):
        try:
            r = subprocess.run(["ping", "-c", "2", "-W", "1500", "-q", ip],
                               capture_output=True, timeout=8)
            return r.returncode == 0
        except Exception:  # noqa: BLE001
            return False

    def _clear_manual_hold(self):
        try:
            os.unlink(self.c.manual_hold)
        except FileNotFoundError:
            pass

    def _on_return(self):
        """Volviste: los clips de esta ventana casi seguro sos vos entrando.
        Marca una ventana (el hook los archiva sin notificar) y archiva los
        que ya se subieron a Drive. Si no hubo evento reciente, silencio."""
        grace = self.c.return_grace_s
        recent = time.time() - self.event_start < grace
        try:
            with open(self.c.return_window, "w") as fh:
                fh.write("%d\n" % (time.time() + grace))
        except OSError:
            pass
        if not recent:
            return
        dest = self.c.rclone_dest
        try:
            r = subprocess.run(
                ["rclone", "move", dest, dest + "/probablemente-vos",
                 "--filter", "- probablemente-vos/**",
                 "--filter", "+ *.mp4", "--filter", "- *",
                 "--max-age", "%ds" % (grace + 60),
                 "--no-traverse", "-q"],
                env=dict(os.environ), capture_output=True, text=True,
                timeout=120)
            err = "" if r.returncode == 0 else r.stderr.strip()[:120]
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
        if err:
            self.log.warning("volviste: no pude archivar en Drive: %s", err)
        else:
            self.log.info("volviste: clips recientes → probablemente-vos/")
        self._notify(1, "house", "🩴 En casa",
                     "Llegaste, ojota se desarmó solo. Guardó el video "
                     "de tu entrada por las dudas.")

    # ── ffmpeg: segmentador (copia, sin re-encodear) ──────────────────
    def run_segmenter(self):
        os.makedirs(self.c.buffer_dir, exist_ok=True)
        pattern = os.path.join(self.c.buffer_dir, "seg-%Y%m%d-%H%M%S.ts")
        # -c copy: video Y audio sin re-encodear (la música/ambiente queda
        # en la grabación; el audio nunca entra en la detección).
        cmd = [
            "ffmpeg", "-nostdin", "-loglevel", "error",
            "-rtsp_transport", self.c.transport,
            "-timeout", "15000000",
            "-i", self.c.rtsp_url,
            "-map", "0", "-c", "copy",
            "-f", "segment", "-segment_time", str(self.c.segment_s),
            "-segment_format", "mpegts", "-reset_timestamps", "1",
            "-strftime", "1", pattern,
        ]
        self._supervise("segmentador", cmd, self._drain_stderr)

    # ── ffmpeg: detector (decodifica en baja resolución) ──────────────
    def run_detector(self):
        w = self.c.detect_width
        vf = f"fps={self.c.detect_fps},scale={w}:-2,format=gray"
        cmd = [
            "ffmpeg", "-nostdin", "-loglevel", "error",
            "-rtsp_transport", self.c.transport,
            "-timeout", "15000000",
            "-i", self.c.rtsp_sub,
            "-an", "-vf", vf,
            "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ]
        self._supervise("detector", cmd, self._detect_loop)

    def _supervise(self, name, cmd, handler):
        backoff = 2
        paused = False
        while not self.stop.is_set():
            if not self.capture_enabled:
                if not paused:
                    self.log.info("%s: en pausa (desarmado)", name)
                    paused = True
                self.stop.wait(2)
                continue
            paused = False
            self.log.info("iniciando %s: %s", name, _redact(shlex.join(cmd)))
            try:
                p = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=0,
                )
            except Exception as exc:  # noqa: BLE001
                self.log.error("no se pudo lanzar %s: %s", name, exc)
                self.stop.wait(backoff)
                backoff = min(backoff * 2, 60)
                continue
            self.procs.append(p)
            try:
                handler(p)
            except Exception as exc:  # noqa: BLE001
                self.log.error("%s: handler cortó: %s", name, exc)
            p.poll()
            if p.returncode is None:
                p.terminate()
            rc = p.wait()
            self.procs.remove(p)
            if self.stop.is_set():
                return
            if not self.capture_enabled:
                continue  # lo frenó el desarme, no es un fallo
            err = (p.stderr.read() or b"").decode(errors="replace").strip()
            self.log.warning("%s terminó (rc=%s) %s", name, rc,
                             _redact(err.splitlines()[-1]) if err else "")
            self.stop.wait(backoff)
            backoff = min(backoff * 2, 60)
        # reset de backoff tras una corrida larga se maneja en _detect_loop

    def _drain_stderr(self, p):
        for raw in iter(p.stderr.readline, b""):
            if self.stop.is_set():
                return
            msg = raw.decode(errors="replace").strip()
            if msg:
                self.log.warning("segmentador ffmpeg: %s", _redact(msg))

    # ── bucle de detección: diferencia de frames ─────────────────────
    def _detect_loop(self, p):
        # calcular alto real: scale=W:-2 mantiene aspecto, múltiplo de 2
        w = self.c.detect_width
        h = self._probe_detect_height() or int(round(w * 9 / 16))
        frame_bytes = w * h
        self.log.info("detector: %dx%d, %d fps, delta=%d area=%.2f%% "
                      "luz=%.0f%%", w, h, self.c.detect_fps,
                      self.c.pixel_delta, self.c.area_pct, self.c.light_pct)
        prev = None
        run_start = time.time()
        warmup_until = run_start + self.c.warmup_s
        motion_streak = 0
        while not self.stop.is_set() and self.capture_enabled:
            buf = _read_exact(p.stdout, frame_bytes)
            if buf is None:
                break
            now = time.time()
            self.last_frame_ts = now
            if self.cam_down_notified:
                self.log.info("cámara: stream recuperado")
                self.cam_down_notified = False
                self._notify(3, "white_check_mark,camera",
                             "🩴 Cámara reconectada",
                             "Volvió a andar. Todo normal.")
            frame = np.frombuffer(buf, np.uint8).reshape(h, w)
            l, t, r, b = self.active_roi
            cur = frame[int(t * h):int(b * h),
                        int(l * w):int(r * w)].astype(np.int16)
            if now < warmup_until:
                prev = cur
                continue
            if prev is not None and prev.shape == cur.shape:
                changed = int(np.count_nonzero(
                    np.abs(cur - prev) > self.c.pixel_delta))
                pct = changed / cur.size * 100.0
                if pct >= self.c.light_pct:
                    if motion_streak:
                        self.log.info("cambio de luz (%.1f%%), ignoro", pct)
                    motion_streak = 0
                elif pct >= self.c.area_pct:
                    motion_streak += 1
                    if motion_streak >= self.c.min_frames:
                        self._on_motion(now, pct)
                else:
                    motion_streak = 0
            prev = cur

    def _probe_detect_height(self):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-rtsp_transport", self.c.transport,
                 "-timeout", "10000000", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", self.c.rtsp_sub],
                capture_output=True, text=True, timeout=20,
            ).stdout.strip()
            sw, sh = (int(x) for x in out.split(",")[:2])
            th = int(round(self.c.detect_width * sh / sw))
            return th + (th % 2)
        except Exception:  # noqa: BLE001
            return None

    # ── manejo de eventos ───────────────────────────────────────────
    def _on_motion(self, ts, pct):
        new_event = False
        with self.lock:
            self.last_motion = ts
            if not self.event_active:
                self.event_active = True
                self.event_start = ts
                new_event = True
        if new_event:
            self.log.info("MOVIMIENTO detectado (%.1f%% de cambio)", pct)
            self._instant_notify(ts)
            self._check_burst(ts)

    def _in_return_window(self):
        try:
            return time.time() < float(open(self.c.return_window).read())
        except (OSError, ValueError):
            return False

    def _instant_notify(self, ts):
        # aviso apenas se detecta, sin esperar a que se arme el clip.
        # respeta la misma ventana de silencio que el aviso por clip.
        if not self.notify_enabled or self._in_return_window():
            return
        if ts - self.last_instant_notify < self.c.notify_silence_min * 60:
            return
        self.last_instant_notify = ts
        # diferido: si en los próximos segundos la presencia te reconoce
        # (volviste → desarmado / ventana de vuelta), se cancela: casi
        # seguro eras vos entrando y el celu tardó en reconectar al WiFi.
        delay = self.c.instant_notify_delay_s
        if delay <= 0:
            self._fire_instant_notify(ts)
            return
        self.instant_timer = threading.Timer(
            delay, self._fire_instant_notify, args=(ts,))
        self.instant_timer.daemon = True
        self.instant_timer.start()

    def _fire_instant_notify(self, ts):
        if self.stop.is_set() or not self.notify_enabled:
            return
        if self._in_return_window():
            self.log.info("aviso instantáneo cancelado (volviste)")
            return
        hora = datetime.fromtimestamp(ts).strftime("%H:%M")
        self._notify(4, "eyes,rotating_light", "🩴 Movimiento en casa",
                     "Algo se movió a las %s. Guardando el video…" % hora)

    def _check_burst(self, ts):
        if self._in_return_window():
            return
        cutoff = ts - self.c.burst_min * 60
        self.event_times = [t for t in self.event_times if t >= cutoff]
        self.event_times.append(ts)
        n = len(self.event_times)
        if (n >= self.c.burst_count
                and ts - self.last_burst_alert > self.c.burst_min * 60):
            self.last_burst_alert = ts
            self.log.warning("BURST: %d eventos en %g min", n, self.c.burst_min)
            self._notify(5, "warning,eyes", "🩴 Actividad inusual",
                         "%d alertas de movimiento en %g minutos. "
                         "Revisá los videos." % (n, self.c.burst_min))

    def _event_finalizer(self):
        while not self.stop.is_set():
            self.stop.wait(1)
            with self.lock:
                active = self.event_active
                last = self.last_motion
                start = self.event_start
            if active and time.time() - last > self.c.postcapture_s:
                self._finalize_event(start, last)
                with self.lock:
                    self.event_active = False

    def _finalize_event(self, start, last):
        win_start = start - self.c.precapture_s
        win_end = last + 2
        segs = self._segments_in_window(win_start, win_end)
        if not segs:
            self.log.warning("evento sin segmentos en ventana, descarto")
            return
        ts_name = datetime.fromtimestamp(start).strftime("%Y%m%d-%H%M%S")
        out = os.path.join(self.c.pending_dir, f"ojota-{ts_name}.mp4")
        os.makedirs(self.c.pending_dir, exist_ok=True)
        listfile = out + ".txt"
        with open(listfile, "w") as fh:
            for s in segs:
                fh.write(f"file {shlex.quote(s)}\n")
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
               "-f", "concat", "-safe", "0", "-i", listfile,
               "-map", "0", "-c", "copy", "-movflags", "+faststart",
               "-fflags", "+genpts", out]
        rc = subprocess.run(cmd, capture_output=True, text=True)
        os.unlink(listfile)
        if rc.returncode != 0:
            self.log.error("concat falló: %s", rc.stderr.strip())
            return
        dur = time.time() - start + self.c.precapture_s
        self.log.info("clip armado: %s (%d segmentos, ~%ds)",
                      os.path.basename(out), len(segs), int(win_end - win_start))
        self._call_hook(out, start)

    def _segments_in_window(self, win_start, win_end):
        out = []
        try:
            names = sorted(os.listdir(self.c.buffer_dir))
        except FileNotFoundError:
            return out
        newest = names[-1] if names else None
        for name in names:
            m = SEG_RE.search(name)
            if not m:
                continue
            st = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").timestamp()
            end = st + self.c.segment_s * 2  # margen por GOP
            if end >= win_start and st <= win_end and name != newest:
                out.append(os.path.join(self.c.buffer_dir, name))
        return out

    def _call_hook(self, clip_path, event_ts):
        if not os.path.exists(self.c.hook):
            self.log.info("hook no existe todavía (%s), salteo", self.c.hook)
            return
        env = dict(os.environ,
                   OJOTA_CONF=CONF_PATH,
                   OJOTA_NOTIFY="1" if self.notify_enabled else "0")
        try:
            subprocess.Popen(
                [self.c.hook, clip_path, str(int(event_ts))], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001
            self.log.error("no se pudo llamar al hook: %s", exc)

    def _notify(self, priority, tags, title, message):
        """Notificación directa (cámara caída / recuperada). Siempre se
        manda, sin importar el perfil."""
        if not os.path.exists(self.c.notify_script):
            return
        try:
            subprocess.Popen(
                [self.c.notify_script, str(priority), tags, title, message],
                env=dict(os.environ, OJOTA_CONF=CONF_PATH),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001
            self.log.error("no se pudo notificar: %s", exc)

    # ── subida en vivo del ring buffer (mientras está armado) ──────
    def _live_upload(self):
        if not self.c.live_upload:
            return
        dest = self.c.rclone_dest + "/live/"
        env = dict(os.environ)
        logged = False
        while not self.stop.is_set():
            self.stop.wait(10)
            if self.stop.is_set() or not self.capture_enabled:
                logged = False
                continue
            if not logged:
                self.log.info("live: subiendo el ring buffer a %s", dest)
                logged = True
            try:
                subprocess.run(
                    ["rclone", "copy", self.c.buffer_dir, dest,
                     "--include", "*.ts", "--no-traverse",
                     "--retries", "1", "-q"],
                    env=env, capture_output=True, timeout=60)
                subprocess.run(
                    ["rclone", "delete", dest, "--min-age", "3m",
                     "--include", "*.ts", "--drive-use-trash=false", "-q"],
                    env=env, capture_output=True, timeout=60)
            except Exception as exc:  # noqa: BLE001
                self.log.warning("live: %s", exc)

    # ── reintento de subidas que quedaron pendientes ────────────────
    def _pending_flush(self):
        while not self.stop.is_set():
            self.stop.wait(self.c.pending_retry_min * 60)
            if self.stop.is_set():
                return
            try:
                names = os.listdir(self.c.pending_dir)
            except FileNotFoundError:
                continue
            for name in names:
                if not name.endswith(".mp4"):
                    continue
                path = os.path.join(self.c.pending_dir, name)
                try:
                    age = time.time() - os.path.getmtime(path)
                except FileNotFoundError:
                    continue
                if age < 60:
                    continue  # recién creado, el hook original sigue vivo
                self.log.info("reintento de subida pendiente: %s", name)
                self._call_hook(path, os.path.getmtime(path))

    # ── mantenimiento: retención + backup de config ────────────────
    def _maintenance(self):
        env = dict(os.environ, OJOTA_CONF=CONF_PATH)
        last_backup = 0.0
        first = True
        while not self.stop.is_set():
            if not first:
                self.stop.wait(self.c.retention_check_h * 3600)
            first = False
            if self.stop.is_set():
                return
            self._run_cli("prune", env)
            if self.c.config_backup and time.time() - last_backup > 7 * 86400:
                self._run_cli("backup-config", env)
                last_backup = time.time()

    def _run_cli(self, sub, env):
        if not os.path.exists(self.c.cli):
            return
        try:
            r = subprocess.run([self.c.cli, sub], env=env, timeout=300,
                               capture_output=True, text=True)
            for line in (r.stdout or "").splitlines():
                if line.strip():
                    self.log.info("%s: %s", sub, line.strip())
            if r.returncode != 0 and r.stderr:
                self.log.warning("%s: %s", sub, r.stderr.strip()[:200])
        except Exception as exc:  # noqa: BLE001
            self.log.warning("no se pudo correr '%s': %s", sub, exc)

    # ── heartbeat externo ─────────────────────────────────────────
    def _heartbeat(self):
        if not self.c.heartbeat_url:
            return
        self.log.info("heartbeat: cada %.0f min", self.c.heartbeat_min)
        ok_prev = None
        while not self.stop.is_set():
            try:
                r = subprocess.run(["curl", "-fsS", "-m", "10",
                                    self.c.heartbeat_url],
                                   capture_output=True, timeout=15)
                ok = r.returncode == 0
            except Exception:  # noqa: BLE001
                ok = False
            if ok != ok_prev:
                self.log.info("heartbeat: %s", "OK" if ok else "FALLÓ el ping")
                ok_prev = ok
            self.stop.wait(self.c.heartbeat_min * 60)

    # ── limpieza del ring buffer ────────────────────────────────────
    def _buffer_cleaner(self):
        while not self.stop.is_set():
            self.stop.wait(5)
            now = time.time()
            with self.lock:
                keep_from = (self.event_start - self.c.precapture_s
                             if self.event_active else now - self.c.buffer_s)
            try:
                names = sorted(os.listdir(self.c.buffer_dir))
            except FileNotFoundError:
                continue
            for name in names[:-1]:  # nunca el más nuevo
                m = SEG_RE.search(name)
                if not m:
                    continue
                st = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").timestamp()
                if st + self.c.segment_s * 2 < keep_from:
                    try:
                        os.unlink(os.path.join(self.c.buffer_dir, name))
                    except FileNotFoundError:
                        pass

    # ── salud de la cámara ─────────────────────────────────────────
    def _health_watch(self):
        limit = self.c.cam_down_min * 60
        while not self.stop.is_set():
            self.stop.wait(10)
            if not self.capture_enabled:
                continue  # desarmado: no hay frames a propósito
            gap = time.time() - self.last_frame_ts
            if gap > limit and not self.cam_down_notified:
                self.cam_down_notified = True
                self.log.error(
                    "ALERTA: sin frames de la cámara hace %.0f min", gap / 60)
                self._notify(5, "rotating_light,camera",
                             "🩴 Cámara sin señal",
                             "Dejó de responder hace %.0f min. Puede ser "
                             "un corte de luz o de internet en casa."
                             % (gap / 60))

    # ── arranque ───────────────────────────────────────────────────
    def start(self):
        threads = [
            threading.Thread(target=self.run_segmenter, name="segmenter"),
            threading.Thread(target=self.run_detector, name="detector"),
            threading.Thread(target=self._event_finalizer, name="finalizer"),
            threading.Thread(target=self._buffer_cleaner, name="cleaner"),
            threading.Thread(target=self._health_watch, name="health"),
            threading.Thread(target=self._profile_watch, name="profile"),
            threading.Thread(target=self._presence_watch, name="presence"),
            threading.Thread(target=self._live_upload, name="live"),
            threading.Thread(target=self._pending_flush, name="flush"),
            threading.Thread(target=self._maintenance, name="maint"),
            threading.Thread(target=self._heartbeat, name="heartbeat"),
        ]
        for t in threads:
            t.daemon = True
            t.start()
        self.log.info("ojota daemon arriba (pid %d) — perfil '%s'",
                      os.getpid(), self.profile)
        while not self.stop.is_set():
            self.stop.wait(1)
        self.log.info("apagando…")
        for p in list(self.procs):
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
        for t in threads:
            t.join(timeout=3)

    def shutdown(self, *_):
        self.stop.set()


def _read_exact(stream, n):
    chunks = []
    got = 0
    while got < n:
        b = stream.read(n - got)
        if not b:
            return None
        chunks.append(b)
        got += len(b)
    return b"".join(chunks)


def main():
    # corre como root bajo launchd: que los archivos queden legibles
    # por el usuario (el plist ya pone Umask, esto es por las dudas)
    os.umask(0o022)
    raw = load_conf(CONF_PATH)
    conf = Conf(raw)
    log = setup_logging(conf.log_dir)
    d = Daemon(conf, log)
    signal.signal(signal.SIGTERM, d.shutdown)
    signal.signal(signal.SIGINT, d.shutdown)
    try:
        d.start()
    except KeyboardInterrupt:
        d.shutdown()


if __name__ == "__main__":
    main()
