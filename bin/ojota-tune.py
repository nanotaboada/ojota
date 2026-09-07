#!/usr/bin/env python3
"""ojota tune — muestra el % de cambio entre frames en vivo.

Sirve para calibrar PIXEL_DELTA / MOTION_AREA_PCT / LIGHT_CHANGE_PCT con
pruebas reales: corré esto, pasá por delante de la cámara y mirá los
números. No graba nada.

    ./ojota-tune.py            # usa el substream de la config
    ./ojota-tune.py --main     # usa el stream principal
"""

import os
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_PATH = os.environ.get("OJOTA_CONF", os.path.join(ROOT, "config", "ojota.conf"))


def load_conf(path):
    d = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def main():
    c = load_conf(CONF_PATH)
    url = c["RTSP_URL"] if "--main" in sys.argv else (
        c.get("RTSP_SUBSTREAM_URL") or c["RTSP_URL"])
    fps = int(c.get("DETECT_FPS", 3))
    width = int(c.get("DETECT_WIDTH", 320))
    delta = int(c.get("PIXEL_DELTA", 18))
    area = float(c.get("MOTION_AREA_PCT", 1.5))
    light = float(c.get("LIGHT_CHANGE_PCT", 70))

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0",
         "-rtsp_transport", "tcp", url],
        capture_output=True, text=True).stdout.strip()
    sw, sh = (int(x) for x in probe.split(",")[:2])
    h = int(round(width * sh / sw))
    h += h % 2
    frame_bytes = width * h
    print(f"stream {sw}x{sh} -> analizo {width}x{h} @ {fps}fps | "
          f"delta={delta} area={area}% luz={light}%\n"
          f"{'hora':8}  {'cambio%':>8}  estado")

    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error",
           "-rtsp_transport", "tcp", "-timeout", "15000000", "-i", url,
           "-an", "-vf", f"fps={fps},scale={width}:-2,format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    limit = None
    for a in sys.argv:
        if a.startswith("--seconds="):
            limit = float(a.split("=", 1)[1])
    warmup = float(c.get("DETECT_WARMUP_SECONDS", 10))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    prev = None
    streak = 0
    t0 = time.time()
    try:
        while True:
            if limit and time.time() - t0 > limit:
                break
            buf = p.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            el = time.time() - t0
            if el < warmup:
                prev = buf
                print(f"[{el:5.1f}s] warm-up…", flush=True)
                continue
            if prev is not None:
                a = np.frombuffer(prev, np.uint8).astype(np.int16)
                b = np.frombuffer(buf, np.uint8).astype(np.int16)
                pct = np.count_nonzero(np.abs(a - b) > delta) / a.size * 100
                if pct >= light:
                    state, streak = "CAMBIO DE LUZ (ignora)", 0
                elif pct >= area:
                    streak += 1
                    state = f"movimiento x{streak}" + (
                        "  <<< DISPARARÍA" if streak >= 3 else "")
                else:
                    state, streak = "quieto", 0
                el = time.time() - t0
                print(f"[{el:5.1f}s] {time.strftime('%H:%M:%S')}  "
                      f"{pct:7.2f}%  {state}", flush=True)
            prev = buf
    except KeyboardInterrupt:
        pass
    finally:
        p.terminate()


if __name__ == "__main__":
    main()
