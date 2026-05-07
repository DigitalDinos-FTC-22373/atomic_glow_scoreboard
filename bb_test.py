#!/usr/bin/env python3
"""Monitor GPIO26 and print its current state (0/1) to stdout at 10Hz."""

import signal
import sys
import time

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("This script must be run on a Raspberry Pi with RPi.GPIO installed.", file=sys.stderr)
    sys.exit(1)

PIN = 26  # BCM numbering
DELAY_SEC = 0.01  # 100 Hz
N_SAMP = 100 # Num of samples to store


def main():
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    running = True
    last_state = None
    last_change_time = time.time()
    last_dot_time = last_change_time

    hist = [1] * N_SAMP

    def _handle_shutdown(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    try:
        while running:
            state = GPIO.input(PIN)
            now = time.time()

            hist.append(state)
            hist = hist[-N_SAMP:]
            
            ## for testing, print the history if there are some breaks (0 value) and the last 10 are unbroken (1)
            if (sum(hist) < 100 and sum(hist[:10]) == 10):
                print(hist)

            ######### this prints only changes in state, or a dot if there's been no change for a second
            # if last_state is None or state != last_state:
            #     print(state, flush=True)
            #     last_state = state
            #     last_change_time = now
            #     last_dot_time = now
            # elif now - last_dot_time >= 1.0:
            #     print('.', flush=True)
            #     last_dot_time = now
            
            time.sleep(DELAY_SEC)
    finally:
        GPIO.cleanup()


if __name__ == '__main__':
    main()
