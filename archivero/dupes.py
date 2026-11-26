"""Phase 2: content duplicates (size -> quick hash -> full hash)."""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor

from .util import Progress, human

EDGE = 64 * 1024
EXCL = "category != 'system_junk'"


def quick_hash(path, size):
    h = hashlib.blake2b(digest_size=16)
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(EDGE))
        if size > 2 * EDGE:
            f.seek(size - EDGE)
            h.update(f.read(EDGE))
        elif size > EDGE:
            h.update(f.read())
    return h.hexdigest()


def full_hash(path, size=None):
    h = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _job(fn, fid, path, size):
    try:
        return fid, fn(path, size), None
    except OSError as err:
        return fid, None, str(err)


def _phase(con, rows, fn, column, label, workers, cost):
    prog = Progress()
    total = sum(cost(r[2]) for r in rows)
    done = n = pending = 0
    sizes = {r[0]: r[2] for r in rows}
    # Submitted in batches so we never create millions of futures at once.
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for start in range(0, len(rows), 2000):
            batch = rows[start:start + 2000]
            for fid, digest, err in ex.map(lambda r: _job(fn, *r), batch):
                n += 1
                done += cost(sizes[fid])
                if err:
                    con.execute("INSERT INTO errors(path,error,at) SELECT path,?,? FROM files WHERE id=?",
                                (err, time.time(), fid))
                else:
                    con.execute("UPDATE files SET %s=? WHERE id=?" % column, (digest, fid))
                pending += 1
                if pending >= 500:
                    con.commit()
                    pending = 0
                prog.show("%s: %d/%d · %s/%s · %.1f MB/s" % (
                    label, n, len(rows), human(done), human(total),
                    done / 1048576 / max(prog.elapsed(), 0.001)))
    con.commit()
    prog.done("%s: %d files in %.0f s." % (label, len(rows), prog.elapsed()))


def find_duplicates(con, cfg):
    min_size = cfg["dupes_min_size"]
    workers = cfg["dupes_workers"]
    rows = con.execute(f"""
        SELECT id, path, size FROM files
        WHERE {EXCL} AND size >= ? AND quick_hash IS NULL AND size IN (
            SELECT size FROM files WHERE {EXCL} AND size >= ? GROUP BY size HAVING COUNT(*) > 1)
        ORDER BY dir""", (min_size, min_size)).fetchall()
    print("Candidates (same size) pending quick hash: %d" % len(rows))
    if rows:
        _phase(con, rows, quick_hash, "quick_hash", "Quick hash", workers, lambda s: min(s, 2 * EDGE))

    rows = con.execute(f"""
        SELECT f.id, f.path, f.size FROM files f JOIN (
            SELECT size, quick_hash FROM files WHERE {EXCL} AND quick_hash IS NOT NULL
            GROUP BY size, quick_hash HAVING COUNT(*) > 1) g
          ON f.size = g.size AND f.quick_hash = g.quick_hash
        WHERE f.full_hash IS NULL ORDER BY f.dir""").fetchall()
    print("Pending full hash: %d files, %s" % (len(rows), human(sum(r[2] for r in rows))))
    if rows:
        _phase(con, rows, full_hash, "full_hash", "Full hash", workers, lambda s: s)

    groups, copies, wasted = con.execute(f"""
        SELECT COUNT(*), COALESCE(SUM(n),0), COALESCE(SUM((n-1)*size),0) FROM (
            SELECT size, COUNT(*) n FROM files WHERE {EXCL} AND full_hash IS NOT NULL
            GROUP BY full_hash, size HAVING COUNT(*) > 1)""").fetchone()
    print("Confirmed duplicates: %d groups, %d files, %s reclaimable." % (groups, copies, human(wasted)))
