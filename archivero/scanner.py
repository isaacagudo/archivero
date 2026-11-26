"""Phase 1: walk and classify (resumable)."""

import json
import os
import sys
import time

from . import dates, media
from .categories import build_ext_map, classify
from .db import get_meta, set_meta
from .detectors import CHILD_DETECTORS, DIR_DETECTORS, PRUNED, UNIT
from .util import Progress, human


def measure(path):
    """Count files, bytes and newest/oldest mtime of a pruned folder."""
    files = total = 0
    newest = oldest = None
    stack = [path]
    while stack:
        p = stack.pop()
        try:
            it = os.scandir(p)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_symlink():
                        continue
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                files += 1
                total += st.st_size
                m = st.st_mtime
                newest = m if newest is None or m > newest else newest
                oldest = m if oldest is None or m < oldest else oldest
    return files, total, newest, oldest


def save_detection(con, path, det, measured=None):
    files = total = newest = oldest = None
    if measured:
        files, total, newest, oldest = measured
    con.execute("""INSERT INTO detections(path,kind,mode,note,regenerable,files,bytes,newest,oldest)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(path,kind) DO UPDATE SET mode=excluded.mode, note=excluded.note,
                     regenerable=excluded.regenerable,
                     files=COALESCE(excluded.files, detections.files),
                     bytes=COALESCE(excluded.bytes, detections.bytes),
                     newest=COALESCE(excluded.newest, detections.newest),
                     oldest=COALESCE(excluded.oldest, detections.oldest)""",
                (path, det.kind, det.mode, det.note, int(det.regenerable), files, total, newest, oldest))


def scan(con, root, cfg, exif=False, probe_video=False, tags=False, measure_pruned=True):
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        sys.exit("Folder not found: %s" % root)
    prev = get_meta(con, "root")
    if prev and prev != root:
        sys.exit("This database already holds an inventory of %s. Use another --db." % prev)
    set_meta(con, "root", root)
    set_meta(con, "config", json.dumps(cfg, ensure_ascii=False))
    con.commit()
    build_ext_map(cfg.get("extra_extensions"))

    long_secs = cfg["long_video_min"] * 60
    long_bytes = cfg["long_video_gb"] * 1024 ** 3
    prog = Progress()
    n_seen = n_new = bytes_seen = pending = 0
    stack = [(root, frozenset())]  # (folder, kinds of the units that contain it)

    def log_error(p, err):
        con.execute("INSERT INTO errors(path,error,at) VALUES(?,?,?)", (p, str(err), time.time()))

    while stack:
        d, ctx = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError as err:
            log_error(d, err)
            continue
        names_low = {e.name.lower() for e in entries}

        kinds = set(ctx)
        for det_fn in DIR_DETECTORS:
            det = det_fn(d, entries, names_low, cfg)
            if det:
                save_detection(con, d, det)
                if det.mode == UNIT:
                    kinds.add(det.kind)
        kinds = frozenset(kinds)
        in_mail = "thunderbird_profile" in kinds

        subdirs = []
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    det = None
                    for det_fn in CHILD_DETECTORS:
                        det = det_fn(e.name, e.path)
                        if det:
                            break
                    if det and det.mode == PRUNED:
                        measured = None
                        if measure_pruned:
                            row = con.execute("SELECT files FROM detections WHERE path=? AND kind=?",
                                              (e.path, det.kind)).fetchone()
                            if not row or row[0] is None:
                                prog.show("Measuring %s …" % e.path[len(root):], force=True)
                                measured = measure(e.path)
                        save_detection(con, e.path, det, measured)
                        continue
                    if det:
                        save_detection(con, e.path, det)
                    subdirs.append(e.path)
                    continue
                if not e.is_file(follow_symlinks=False):
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError as err:
                log_error(e.path, err)
                continue

            n_seen += 1
            bytes_seen += st.st_size
            row = con.execute("SELECT size, mtime FROM files WHERE path=?", (e.path,)).fetchone()
            if row and row[0] == st.st_size and abs((row[1] or 0) - st.st_mtime) < 1:
                continue  # already inventoried and unchanged

            ext, cat, subtype = classify(e.name, names_low, in_mail)
            method = duration = None
            extra = {}
            cands = []
            if cat in ("image", "raw") and exif:
                cands.append((dates.exif_date(e.path), "exif"))
            if cat == "video":
                vdate = None
                if probe_video:
                    duration, vdate = media.probe_video(e.path)
                if duration is not None:
                    cat, method = ("video_long" if duration >= long_secs else "video_short"), "duration"
                else:
                    cat, method = ("video_long" if st.st_size >= long_bytes else "video_short"), "size"
                cands.append((vdate, "video_metadata"))
            if cat == "audio" and tags:
                t, duration = media.audio_tags(e.path)
                if t:
                    extra.update(t)
            cands.append((dates.from_filename(e.name), "filename"))
            cands.append((dates.ts_to_str(st.st_mtime), "mtime"))
            date_best, date_src = dates.best_date(cands)

            con.execute(
                """INSERT INTO files(path,dir,name,ext,size,mtime,category,subtype,method,duration,
                                     date_best,date_source,extra,quick_hash,full_hash,scanned_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,?)
                   ON CONFLICT(path) DO UPDATE SET
                     size=excluded.size, mtime=excluded.mtime, category=excluded.category,
                     subtype=excluded.subtype, method=excluded.method, duration=excluded.duration,
                     date_best=excluded.date_best, date_source=excluded.date_source,
                     extra=excluded.extra, quick_hash=NULL, full_hash=NULL,
                     scanned_at=excluded.scanned_at""",
                (e.path, d, e.name, ext, st.st_size, st.st_mtime, cat, subtype, method, duration,
                 date_best, date_src, json.dumps(extra, ensure_ascii=False) if extra else None,
                 time.time()))
            n_new += 1
            pending += 1
            if pending >= 2000:
                con.commit()
                pending = 0
            prog.show("%d files (%s) · %d new · %.0f files/s · %s" % (
                n_seen, human(bytes_seen), n_new, n_seen / max(prog.elapsed(), 0.001),
                d[len(root):] or "/"))

        stack.extend((s, kinds) for s in sorted(subdirs, reverse=True))

    con.commit()
    n_err = con.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
    n_det = con.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
    prog.done("Scan finished: %d files (%s), %d new or changed, %d folders detected, "
              "%d errors, %.0f s." % (n_seen, human(bytes_seen), n_new, n_det, n_err, prog.elapsed()))
