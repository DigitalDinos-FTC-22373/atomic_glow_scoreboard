#!/usr/bin/env python3
"""
Raspberry Pi GPIO 26 Monitor

Monitors GPIO pin 26 at 20 Hz, printing transitions to the console.
If a display is attached, shows a GUI with:
  - A counter for HIGH→LOW (falling edge) transitions
  - A rolling 5-second graph of the pin state

Usage:
    python3 bbtest.py              # Normal mode
    python3 bbtest.py --simulate   # Simulate without GPIO hardware
    python3 bbtest.py --headless   # Force console-only mode

Dependencies:
    - gpiod (python3-gpiod) or RPi.GPIO
    - For GUI: python3-tk, python3-matplotlib
"""

import argparse
import os
import sys
import time, datetime
from collections import deque

SAMPLE_RATE = 20
SAMPLE_PERIOD = 1.0 / SAMPLE_RATE
HISTORY_SECS = 5
HISTORY_LEN = SAMPLE_RATE * HISTORY_SECS  # 100 samples
GPIO_PIN = 26


# ── GPIO backends ───────────────────────────────────────────────────────


def _open_gpiod_v2():
    """Try gpiod v2 API (Raspberry Pi OS Bookworm+)."""
    import gpiod
    from gpiod.line import Direction

    for chip_path in ("/dev/gpiochip4", "/dev/gpiochip0"):
        try:
            request = gpiod.request_lines(
                chip_path,
                consumer="gpio-monitor",
                config={GPIO_PIN: gpiod.LineSettings(direction=Direction.INPUT)},
            )

            def read():
                return int(request.get_value(GPIO_PIN))

            read()  # test it
            return read
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return None


def _open_gpiod_v1():
    """Try gpiod v1 API."""
    import gpiod

    for chip_name in ("gpiochip4", "gpiochip0"):
        try:
            chip = gpiod.Chip(chip_name)
            line = chip.get_line(GPIO_PIN)
            line.request(consumer="gpio-monitor", type=gpiod.LINE_REQ_DIR_IN)

            def read(l=line):
                return l.get_value()

            read()  # test it
            return read
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return None


def _open_rpigpio():
    """Try RPi.GPIO (Pi 4 and earlier)."""
    import RPi.GPIO as GPIO

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(GPIO_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    return lambda: GPIO.input(GPIO_PIN)


def open_gpio():
    """Try available GPIO libraries in order; return a read function."""
    for name, opener in [
        #("gpiod v2", _open_gpiod_v2),
        #("gpiod v1", _open_gpiod_v1),
        ("RPi.GPIO", _open_rpigpio),
    ]:
        try:
            reader = opener()
            if reader is not None:
                print(f"GPIO backend: {name}")
                return reader
        except (ImportError, AttributeError):
            pass

    print("Error: no GPIO library found. Install python3-gpiod or RPi.GPIO.")
    sys.exit(1)


# ── Simulated GPIO ─────────────────────────────────────────────────────


def open_simulated():
    """Simulated signal with random transitions (~2 Hz avg)."""
    import random

    state = [0]

    def read():
        if random.random() < 2.0 / SAMPLE_RATE:
            state[0] ^= 1
        return state[0]

    print("GPIO backend: simulated")
    return read


# ── Display detection ──────────────────────────────────────────────────


def has_display():
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except Exception:
        return False


# ── Headless mode ──────────────────────────────────────────────────────


def run_headless(read_pin):
    prev = read_pin()
    fall_count = 0

    print(f"Monitoring GPIO {GPIO_PIN} at {SAMPLE_RATE} Hz  [headless]")
    print(f"Initial state: {'HIGH' if prev else 'LOW'}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            t0 = time.monotonic()
            state = read_pin()
            if state != prev:
                edge = "FALLING" if state == 0 else "RISING"
                if prev == 1 and state == 0:
                    fall_count += 1
                    print(
						f"[{datetime.datetime.now()}] "
						f"{'HIGH' if prev else 'LOW'} -> {'HIGH' if state else 'LOW'}  "
						f"[{edge}]  falls={fall_count}")
                prev = state
            elapsed = time.monotonic() - t0
            time.sleep(max(0, SAMPLE_PERIOD - elapsed))
    except KeyboardInterrupt:
        print(f"\nStopped. Total falling edges: {fall_count}")


# ── GUI mode ───────────────────────────────────────────────────────────


def run_gui(read_pin):
    import tkinter as tk

    import numpy as np
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure

    history = deque([0] * HISTORY_LEN, maxlen=HISTORY_LEN)
    prev_state = [read_pin()]
    fall_count = [0]

    # ── window ──────────────────────────────────────────────────────────
    root = tk.Tk()
    root.title(f"GPIO {GPIO_PIN} Monitor")
    root.configure(bg="#1a1a2e")
    root.geometry("720x480")

    # ── top bar ─────────────────────────────────────────────────────────
    top = tk.Frame(root, bg="#1a1a2e", pady=8)
    top.pack(fill=tk.X)

    count_lbl = tk.Label(
        top,
        text="Falling edges: 0",
        font=("monospace", 22, "bold"),
        fg="#00ffa5",
        bg="#1a1a2e",
    )
    count_lbl.pack(side=tk.LEFT, padx=20)

    state_lbl = tk.Label(
        top, text="--", font=("monospace", 18), fg="#888", bg="#1a1a2e"
    )
    state_lbl.pack(side=tk.RIGHT, padx=20)

    # ── matplotlib graph ────────────────────────────────────────────────
    fig = Figure(figsize=(7, 3), dpi=100, facecolor="#1a1a2e")
    ax = fig.add_subplot(111)
    ax.set_facecolor("#16213e")

    x_data = np.linspace(-HISTORY_SECS, 0, HISTORY_LEN)
    (line_plot,) = ax.step(
        x_data, list(history), where="post", color="#00aaff", linewidth=2
    )

    ax.set_xlim(-HISTORY_SECS, 0)
    ax.set_ylim(-0.15, 1.15)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["LOW", "HIGH"], fontsize=10, color="white")
    ax.set_xlabel("Time (s)", color="white", fontsize=10)
    ax.tick_params(axis="x", colors="white")
    for spine in ax.spines.values():
        spine.set_color("#333")
    ax.grid(axis="x", alpha=0.25, color="#555")
    fig.tight_layout(pad=1.5)

    canvas = FigureCanvasTkAgg(fig, master=root)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

    # ── 20 Hz update loop ──────────────────────────────────────────────
    def update():
        state = read_pin()
        history.append(state)

        # Detect transitions and print to console
        if state != prev_state[0]:
            if prev_state[0] == 1 and state == 0:
                fall_count[0] += 1
            edge = "FALLING" if state == 0 else "RISING"
            print(
                f"[{datetime.datetime.utcnow().strftime('%H:%M:%S.%f')}] "
                f"{'HIGH' if prev_state[0] else 'LOW'} -> "
                f"{'HIGH' if state else 'LOW'}  "
                f"[{edge}]  falls={fall_count[0]}"
            )
            prev_state[0] = state

        # Update labels
        count_lbl.config(text=f"Falling edges: {fall_count[0]}")
        state_lbl.config(
            text=f"\u25cf {'HIGH' if state else 'LOW'}",
            fg="#00ffa5" if state else "#ff4455",
        )

        # Update graph
        line_plot.set_ydata(list(history))
        canvas.draw_idle()

        root.after(int(SAMPLE_PERIOD * 1000), update)

    print(f"Monitoring GPIO {GPIO_PIN} at {SAMPLE_RATE} Hz  [GUI]")
    print("Close the window or press Ctrl+C to stop.\n")
    update()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\nStopped. Total falling edges: {fall_count[0]}")


# ── Main ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description=f"Monitor GPIO {GPIO_PIN} at {SAMPLE_RATE} Hz"
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="use simulated signal (no GPIO hardware needed)",
    )
    parser.add_argument(
        "--headless", action="store_true", help="force console-only mode"
    )
    args = parser.parse_args()

    read_pin = open_simulated() if args.simulate else open_gpio()

    if args.headless or not has_display():
        run_headless(read_pin)
    else:
        run_gui(read_pin)


if __name__ == "__main__":
    main()
