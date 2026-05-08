#!/usr/bin/env python3
"""
Scoreboard Application
======================
A networked scorekeeping app with a neon-themed GUI.

Usage:
  Server:  python scoreboard.py server [-t]
  Client:  python scoreboard.py client <server_ip> <team>  [-t]
             team = red | blue

Server key commands (in terminal):
  s  - Start scoring
  p  - Stop (pause) scoring
  r  - Reset scores (requires confirmation)
  q  - Quit

Test mode (-t):
  Server: simulates 4 clients (2 red, 2 blue), each scoring 1 pt/sec
  Client: simulates 1 point per second
"""

import argparse
import json
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

# ─────────────────────────── Network config ──────────────────────────────────
PORT = 55321
UPDATE_RATE = 0.1   # 10 Hz

# ─────────────────────────── Shared protocol ─────────────────────────────────
# Client → Server  {"team": "red"|"blue", "score": <int>}
# Server → Client  {"cmd": "start"|"stop"|"reset"}


# ══════════════════════════════════════════════════════════════════════════════
#  GUI  (used by both server and client in their own windows)
# ══════════════════════════════════════════════════════════════════════════════

class ScoreboardGUI:
    """Big neon scoreboard window."""

    # Neon palette
    BG          = "#0a0a0f"
    RED_BRIGHT  = "#ff2244"
    RED_DIM     = "#6b0018"
    RED_GLOW    = "#ff6688"
    BLUE_BRIGHT = "#00aaff"
    BLUE_DIM    = "#003366"
    BLUE_GLOW   = "#66ccff"
    DIVIDER     = "#333355"
    LABEL_FG    = "#aaaacc"
    STATUS_FG   = "#888899"

    def __init__(self, title="Scoreboard"):
        self.root = tk.Tk()
        self.root.title(title)
        self.root.configure(bg=self.BG)
        self.root.geometry("900x520")
        self.root.resizable(True, True)
        self.root.minsize(600, 360)

        self._red_score  = 0
        self._blue_score = 0
        self._status     = "Waiting..."

        self._build_ui()

    def _build_ui(self):
        r = self.root

        # ── top status bar ──────────────────────────────────────────────────
        self.status_var = tk.StringVar(value=self._status)
        status_lbl = tk.Label(r, textvariable=self.status_var,
                              bg=self.BG, fg=self.STATUS_FG,
                              font=("Courier New", 13))
        status_lbl.pack(pady=(14, 0))

        # ── main score frame ────────────────────────────────────────────────
        frame = tk.Frame(r, bg=self.BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=20)

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=0)
        frame.columnconfigure(2, weight=1)
        frame.rowconfigure(0, weight=0)
        frame.rowconfigure(1, weight=1)

        # Team labels
        red_lbl = tk.Label(frame, text="RED", bg=self.BG, fg=self.RED_BRIGHT,
                           font=("Courier New", 50, "bold"))
        red_lbl.grid(row=0, column=0, pady=(0, 8))

        blue_lbl = tk.Label(frame, text="BLUE", bg=self.BG, fg=self.BLUE_BRIGHT,
                            font=("Courier New", 50, "bold"))
        blue_lbl.grid(row=0, column=2, pady=(0, 8))

        # Score digits
        score_font = tkfont.Font(family="Courier New", size=240, weight="bold")

        self.red_var  = tk.StringVar(value="0")
        self.blue_var = tk.StringVar(value="0")

        self.red_score_lbl = tk.Label(frame, textvariable=self.red_var,
                                      bg=self.BG, fg=self.RED_BRIGHT,
                                      font=score_font)
        self.red_score_lbl.grid(row=1, column=0, sticky="nsew")

        # Vertical divider
        div = tk.Frame(frame, bg=self.DIVIDER, width=4)
        div.grid(row=0, column=1, rowspan=2, sticky="ns", padx=20)

        self.blue_score_lbl = tk.Label(frame, textvariable=self.blue_var,
                                       bg=self.BG, fg=self.BLUE_BRIGHT,
                                       font=score_font)
        self.blue_score_lbl.grid(row=1, column=2, sticky="nsew")

        # Glow effect – thin inner shadow labels (offset trick)
        self._add_glow_effect()

    def _add_glow_effect(self):
        """Simulate neon glow by periodically pulsing brightness."""
        self._pulse_state = 0
        self._pulse()

    def _pulse(self):
        self._pulse_state = (self._pulse_state + 1) % 20
        bright = self._pulse_state < 10
        self.red_score_lbl.configure(
            fg=self.RED_BRIGHT if bright else self.RED_GLOW)
        self.blue_score_lbl.configure(
            fg=self.BLUE_BRIGHT if bright else self.BLUE_GLOW)
        self.root.after(500, self._pulse)

    # ── public update methods (thread-safe via after()) ──────────────────────

    def set_scores(self, red: int, blue: int):
        self.root.after(0, self._update_scores, red, blue)

    def _update_scores(self, red, blue):
        self.red_var.set(str(red))
        self.blue_var.set(str(blue))

    def set_status(self, text: str):
        self.root.after(0, self.status_var.set, text)

    def run(self):
        self.root.mainloop()


# ══════════════════════════════════════════════════════════════════════════════
#  SERVER
# ══════════════════════════════════════════════════════════════════════════════

class Server:
    def __init__(self, test_mode=False):
        self.test_mode   = test_mode
        self.scoring     = False          # True while scoring is active
        self.red_scores  = {}             # client_id → score
        self.blue_scores = {}             # client_id → score
        self.clients     = {}             # client_id → (conn, team)
        self._lock       = threading.Lock()
        self._client_id  = 0
        self.gui         = ScoreboardGUI("Scoreboard – SERVER")

    # ── total helpers ─────────────────────────────────────────────────────────

    def _totals(self):
        with self._lock:
            return sum(self.red_scores.values()), sum(self.blue_scores.values())

    # ── broadcast a command to all clients ───────────────────────────────────

    def _broadcast(self, cmd: str):
        msg = (json.dumps({"cmd": cmd}) + "\n").encode()
        dead = []
        with self._lock:
            ids = list(self.clients.keys())
        for cid in ids:
            try:
                conn = self.clients[cid][0]
                conn.sendall(msg)
            except Exception:
                dead.append(cid)
        for cid in dead:
            self._drop_client(cid)

    # ── client handler thread ─────────────────────────────────────────────────

    def _handle_client(self, conn, addr, cid):
        buf = ""
        try:
            while True:
                data = conn.recv(1024)
                if not data:
                    break
                buf += data.decode(errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        team  = msg.get("team")
                        score = int(msg.get("score", 0))
                        with self._lock:
                            if team == "red":
                                self.red_scores[cid] = score
                            elif team == "blue":
                                self.blue_scores[cid] = score
                    except (json.JSONDecodeError, ValueError):
                        pass
        except Exception:
            pass
        finally:
            self._drop_client(cid)
            print(f"  [server] Client {cid} ({addr}) disconnected.")

    def _drop_client(self, cid):
        with self._lock:
            if cid in self.clients:
                try:
                    self.clients[cid][0].close()
                except Exception:
                    pass
                team = self.clients[cid][1]
                del self.clients[cid]
                if team == "red" and cid in self.red_scores:
                    del self.red_scores[cid]
                elif team == "blue" and cid in self.blue_scores:
                    del self.blue_scores[cid]

    # ── accept loop ───────────────────────────────────────────────────────────

    def _accept_loop(self, server_sock):
        while True:
            try:
                conn, addr = server_sock.accept()
            except Exception:
                break
            with self._lock:
                self._client_id += 1
                cid = self._client_id
                # Peek at first message to learn the team
                # We'll store as "unknown" until first data arrives
                self.clients[cid] = (conn, "unknown")
                self.red_scores[cid]  = 0   # placeholder; overwritten on data
                self.blue_scores[cid] = 0
            print(f"  [server] Client {cid} connected from {addr}")
            t = threading.Thread(target=self._handle_client,
                                 args=(conn, addr, cid), daemon=True)
            t.start()

    # ── GUI refresh loop ──────────────────────────────────────────────────────

    def _gui_update_loop(self):
        while True:
            r, b = self._totals()
            self.gui.set_scores(r, b)
            state = "SCORING" if self.scoring else "STOPPED"
            self.gui.set_status(
                f"Status: {state}  |  Clients: {len(self.clients)}  |  "
                f"RED: {r}   BLUE: {b}")
            time.sleep(0.1)

    # ── test-mode simulated clients ───────────────────────────────────────────

    def _simulated_client(self, team, client_num):
        """Simulated client: connects over loopback and scores 1 pt/sec."""
        time.sleep(1.5)   # let server start
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(("127.0.0.1", PORT))
        except Exception as e:
            print(f"  [sim-client-{team}-{client_num}] connect failed: {e}")
            return
        score = 0
        buf = ""
        # Thread to read commands from server
        def reader():
            nonlocal score
            b = ""
            while True:
                try:
                    data = sock.recv(1024)
                    if not data:
                        break
                    b += data.decode(errors="replace")
                    while "\n" in b:
                        line, b = b.split("\n", 1)
                        try:
                            msg = json.loads(line.strip())
                            cmd = msg.get("cmd")
                            if cmd == "reset":
                                score = 0
                        except Exception:
                            pass
                except Exception:
                    break
        threading.Thread(target=reader, daemon=True).start()

        last_tick = time.time()
        while True:
            now = time.time()
            if self.scoring and (now - last_tick) >= 1.0:
                score += 1
                last_tick = now
            try:
                msg = json.dumps({"team": team, "score": score}) + "\n"
                sock.sendall(msg.encode())
            except Exception:
                break
            time.sleep(UPDATE_RATE)

    # ── terminal command loop ─────────────────────────────────────────────────

    def _terminal_loop(self):
        print("\n  SERVER COMMANDS")
        print("  ───────────────")
        print("  s  – Start scoring")
        print("  p  – Stop  scoring")
        print("  r  – Reset all scores (requires confirmation)")
        print("  q  – Quit\n")

        while True:
            try:
                cmd = input("  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                self._do_quit()
                return

            if cmd == "s":
                self.scoring = True
                self._broadcast("start")
                print("  [server] Scoring STARTED.")
            elif cmd == "p":
                self.scoring = False
                self._broadcast("stop")
                print("  [server] Scoring STOPPED.")
            elif cmd == "r":
                confirm = input("  Confirm reset? (yes/no): ").strip().lower()
                if confirm in ("yes", "y"):
                    self.scoring = False
                    with self._lock:
                        for k in self.red_scores:
                            self.red_scores[k] = 0
                        for k in self.blue_scores:
                            self.blue_scores[k] = 0
                    self._broadcast("reset")
                    print("  [server] Scores RESET.")
                else:
                    print("  [server] Reset cancelled.")
            elif cmd == "q":
                self._do_quit()
                return
            else:
                print("  Unknown command. Use s / p / r / q")

    def _do_quit(self):
        print("  [server] Shutting down.")
        self._broadcast("stop")
        sys.exit(0)

    # ── main entry ────────────────────────────────────────────────────────────

    def run(self):
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("0.0.0.0", PORT))
        server_sock.listen(20)
        ip = socket.gethostbyname(socket.gethostname())
        print(f"\n  [server] Listening on {ip}:{PORT}")

        # Accept thread
        threading.Thread(target=self._accept_loop,
                         args=(server_sock,), daemon=True).start()

        # GUI update thread
        threading.Thread(target=self._gui_update_loop, daemon=True).start()

        # Test-mode simulated clients
        if self.test_mode:
            print("  [server] TEST MODE – spawning 4 simulated clients")
            for team, n in [("red", 1), ("red", 2), ("blue", 1), ("blue", 2)]:
                threading.Thread(target=self._simulated_client,
                                 args=(team, n), daemon=True).start()

        # Terminal commands in a background thread so GUI can run on main thread
        threading.Thread(target=self._terminal_loop, daemon=True).start()

        # GUI runs on main thread (required by Tk)
        self.gui.run()


# ══════════════════════════════════════════════════════════════════════════════
#  CLIENT
# ══════════════════════════════════════════════════════════════════════════════

class Client:
    def __init__(self, server_ip: str, team: str, test_mode=False):
        if team not in ("red", "blue"):
            sys.exit("team must be 'red' or 'blue'")
        self.server_ip = server_ip
        self.team      = team
        self.test_mode = test_mode
        self.scoring   = False
        self.score     = 0
        self._lock     = threading.Lock()
        # title = f"Scoreboard – CLIENT  [{team.upper()}]"
        # self.gui = ScoreboardGUI(title)

    # ── sensor / test scoring ─────────────────────────────────────────────────

    def _scoring_loop(self):
        """Increment score. Replace this with real sensor logic."""
        while True:
            time.sleep(1.0)
            if self.scoring and self.test_mode:
                with self._lock:
                    self.score += 1

    # ── send scores to server at 10 Hz ───────────────────────────────────────

    def _send_loop(self, sock):
        while True:
            with self._lock:
                s = self.score
            try:
                msg = json.dumps({"team": self.team, "score": s}) + "\n"
                sock.sendall(msg.encode())
            except Exception:
                break
            time.sleep(UPDATE_RATE)

    # ── receive commands from server ──────────────────────────────────────────

    def _recv_loop(self, sock):
        buf = ""
        while True:
            try:
                data = sock.recv(1024)
                if not data:
                    print("  [client] Server disconnected.")
                    break
                buf += data.decode(errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        cmd = msg.get("cmd")
                        if cmd == "start":
                            self.scoring = True
                            self.gui.set_status("Status: SCORING")
                            print("  [client] Scoring STARTED.")
                        elif cmd == "stop":
                            self.scoring = False
                            self.gui.set_status("Status: STOPPED")
                            print("  [client] Scoring STOPPED.")
                        elif cmd == "reset":
                            self.scoring = False
                            with self._lock:
                                self.score = 0
                            self.gui.set_status("Status: RESET")
                            print("  [client] Scores RESET.")
                    except json.JSONDecodeError:
                        pass
            except Exception:
                break

    # ── GUI refresh ───────────────────────────────────────────────────────────

    # def _gui_update_loop(self):
    #     while True:
    #         with self._lock:
    #             s = self.score
    #         if self.team == "red":
    #             self.gui.set_scores(s, 0)
    #         else:
    #             self.gui.set_scores(0, s)
    #         time.sleep(0.1)

    # ── main entry ────────────────────────────────────────────────────────────

    def run(self):
        print(f"  [client] Connecting to {self.server_ip}:{PORT} as {self.team}…")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connected = False
        while not connected:
            try:
                sock.connect((self.server_ip, PORT))
                connected = True
            except Exception as e:
                print(f"  [client] Cannot connect: {e}")
                time.sleep(1.0)
        print(f"  [client] Connected.")
        # self.gui.set_status(f"Connected to {self.server_ip}  |  Team: {self.team.upper()}  |  Waiting…")

        threading.Thread(target=self._recv_loop,  args=(sock,), daemon=True).start()
        threading.Thread(target=self._send_loop,  args=(sock,), daemon=True).start()
        threading.Thread(target=self._scoring_loop,              daemon=True).start()
        # threading.Thread(target=self._gui_update_loop,           daemon=True).start()

        if self.test_mode:
            print("  [client] TEST MODE – scoring 1 pt/sec when active.")

        # self.gui.run()
        while True:
            time.sleep(0.1)


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Networked Scoreboard")
    sub = parser.add_subparsers(dest="mode", required=True)

    # server sub-command
    sp = sub.add_parser("server", help="Run as score server")
    sp.add_argument("-t", "--test", action="store_true",
                    help="Simulate 4 clients (2 red, 2 blue)")

    # client sub-command
    cp = sub.add_parser("client", help="Run as score client")
    cp.add_argument("server_ip", help="Server IP address")
    cp.add_argument("team", choices=["red", "blue"], help="Team colour")
    cp.add_argument("-t", "--test", action="store_true",
                    help="Simulate 1 point per second")

    args = parser.parse_args()

    if args.mode == "server":
        Server(test_mode=args.test).run()
    else:
        Client(args.server_ip, args.team, test_mode=args.test).run()


if __name__ == "__main__":
    main()
