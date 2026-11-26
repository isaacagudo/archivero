"""Shared helpers."""

import os
import re
import sys
import time


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return ("%.0f %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip() or "_"


def ancestors(path, root):
    """Folders from 'path' (included) up to 'root' (included)."""
    chain = []
    p = path
    while True:
        chain.append(p)
        if p == root or len(p) <= len(root):
            break
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return chain


class Progress:
    """Progress line on stderr, refreshed at most every 'every' seconds."""

    def __init__(self, every=2.0):
        self.every = every
        self.last = 0.0
        self.t0 = time.time()
        self.tty = sys.stderr.isatty()

    def elapsed(self):
        return time.time() - self.t0

    def show(self, msg, force=False):
        now = time.time()
        if force or now - self.last >= self.every:
            self.last = now
            if self.tty:
                sys.stderr.write("\r\033[K" + msg[:160])
            else:
                sys.stderr.write(msg[:160] + "\n")
            sys.stderr.flush()

    def done(self, msg):
        if self.tty:
            sys.stderr.write("\r\033[K")
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
