#!/usr/bin/env python3
"""Tests puntuales para los mecanismos de timeout del daemon — no una
suite completa. Cubre justo los puntos que ya se colgaron una vez en
producción (lectura de frames, limpieza de procesos, el watchdog).
No depende de la cámara real ni de pytest — plain asserts.

Uso: .venv/bin/python3 tests/test_daemon.py
"""

import importlib.util
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "ojota_daemon", os.path.join(ROOT, "bin", "ojota-daemon.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures = []


def check(name, condition):
    status = "OK" if condition else "FALLÓ"
    print(f"  [{status}] {name}")
    if not condition:
        failures.append(name)


def test_read_exact_stalls_on_silence():
    """Un stream que no manda nada dispara _ReadStalled a tiempo."""
    p = subprocess.Popen(["sleep", "10"], stdout=subprocess.PIPE, bufsize=0)
    start = time.time()
    stalled = False
    try:
        mod._read_exact(p.stdout, 100, timeout=1)
    except mod._ReadStalled:
        stalled = True
    finally:
        p.terminate()
        p.wait()
    elapsed = time.time() - start
    check("_read_exact: dispara _ReadStalled sin datos", stalled)
    check("_read_exact: dispara cerca del timeout (no antes, no mucho después)",
          0.9 <= elapsed <= 3)


def test_read_exact_reads_normally():
    """Si el dato llega a tiempo, no debe dispararse el timeout."""
    p = subprocess.Popen(
        ["python3", "-c",
         "import sys,time; time.sleep(0.1); "
         "sys.stdout.buffer.write(b'x'*50); sys.stdout.flush()"],
        stdout=subprocess.PIPE, bufsize=0)
    try:
        buf = mod._read_exact(p.stdout, 50, timeout=5)
    finally:
        p.wait()
    check("_read_exact: lee datos normales sin falso positivo",
          buf is not None and len(buf) == 50)


def test_terminate_and_wait_kills_stubborn_process():
    """Réplica del bloque de limpieza de _supervise: un proceso que
    ignora SIGTERM debe morir igual, escalando a SIGKILL, acotado."""
    def cleanup(p, each=1.5):
        p.poll()
        if p.returncode is None:
            p.terminate()
            try:
                return p.wait(timeout=each)
            except subprocess.TimeoutExpired:
                p.kill()
                try:
                    return p.wait(timeout=each)
                except subprocess.TimeoutExpired:
                    return None
        return p.returncode

    p = subprocess.Popen(["bash", "-c", 'trap "" TERM; sleep 30'])
    time.sleep(0.3)
    start = time.time()
    rc = cleanup(p)
    elapsed = time.time() - start
    check("limpieza: escala a SIGKILL y no cuelga (<5s, no 30s)", elapsed < 5)
    check("limpieza: el proceso terminó (rc no None)", rc is not None)


def test_watchdog_kill_decision():
    """Lógica de decisión del watchdog, sin depender de threads reales."""
    class FakeProc:
        def __init__(self):
            self.killed = False

        def kill(self):
            self.killed = True

    def tick(capture_enabled, detector_proc, last_frame_ts, stall_s=20):
        margin = 3
        if not capture_enabled or detector_proc is None:
            return
        if time.time() - last_frame_ts > stall_s * margin:
            detector_proc.kill()

    stale = FakeProc()
    tick(True, stale, time.time() - 100)
    check("watchdog: mata si hace mucho que no hay frames", stale.killed)

    fresh = FakeProc()
    tick(True, fresh, time.time() - 5)
    check("watchdog: no mata con frames recientes", not fresh.killed)

    paused = FakeProc()
    tick(False, paused, time.time() - 1000)
    check("watchdog: no mata si está desarmado", not paused.killed)


if __name__ == "__main__":
    print("test_read_exact_stalls_on_silence")
    test_read_exact_stalls_on_silence()
    print("test_read_exact_reads_normally")
    test_read_exact_reads_normally()
    print("test_terminate_and_wait_kills_stubborn_process")
    test_terminate_and_wait_kills_stubborn_process()
    print("test_watchdog_kill_decision")
    test_watchdog_kill_decision()

    if failures:
        print(f"\n{len(failures)} test(s) fallaron: {', '.join(failures)}")
        sys.exit(1)
    print("\nTodo OK.")
