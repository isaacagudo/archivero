"""Media index (ADR 0005 and 0006): one row per unique photo or video, keyed by its
content hash, with normalized metadata, a WebP thumbnail, a 64-bit dHash and a
non-personal flag.

Each photo is read once: the same bytes give the BLAKE2b hash, the metadata, the
thumbnail and the dHash. Videos are hashed in one streaming pass; ffprobe and
ffmpeg then read their header and a single frame. Identical copies are processed
once, and a run skips everything already indexed, so it can be stopped and
resumed. Nothing is ever written next to the source: thumbnails go to a cache
beside the database (<db>.media/ab/cd/<hash>.webp).
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from .dates import plausible
from .db import require_root
from .detectors import PRUNED
from .util import Progress, human
from .zones import Layout

KIND_OF = {"image": "image", "raw": "raw", "video_short": "video", "video_long": "video"}
CHUNK = 1024 * 1024
HEIF_EXT = {"heic", "heif", "hif", "avif"}

# ---------------------------------------------------------------- ADR 0006 rules
# Zones whose images nobody took (zones.py). 'trash' and 'user_appdata' are not
# here: a deleted or app-held photo can still be personal.
NONPERSONAL_ZONES = {"system", "applications", "cache"}
ICON_DIRS = {"icons", "icon", ".icons", "iconos", "emoticons", "emoticonos", "smileys", "smilies", "emoji",
             "emojis", "pixmaps", "cursors", "skins", "themes", "sprites", "clipart", "clip art", "buttons"}
SAMPLE_DIRS = {"sample pictures", "sample images", "imágenes de muestra", "imagenes de muestra",
               "wallpaper", "wallpapers", "desktop pictures", "desktop backgrounds", "fondos de pantalla",
               "fondos de escritorio", "fonds d'écran", "hintergrundbilder"}
CACHE_DIRS = {"temporary internet files", "cache", "caches", "cache2", "thumbnails", ".thumbnails", "thumbs",
              "thumbcache", "webcache"}
# Stock pictures that shipped with Windows (sample pictures and wallpapers) and
# macOS. Only used when the picture has no camera in its EXIF, since some of
# these names are ordinary words.
SAMPLE_NAMES = {
    "blue hills.jpg", "sunset.jpg", "water lilies.jpg", "winter.jpg",  # XP sample pictures
    "chrysanthemum.jpg", "desert.jpg", "hydrangeas.jpg", "jellyfish.jpg", "koala.jpg", "lighthouse.jpg",
    "penguins.jpg", "tulips.jpg",  # 7 sample pictures
    "bliss.jpg", "autumn.jpg", "azul.jpg", "crystal.jpg", "follow.jpg", "friend.jpg", "home.jpg",
    "moon flower.jpg", "peace.jpg", "power.jpg", "purple flower.jpg", "radiance.jpg", "red moon desert.jpg",
    "ripple.jpg", "stonehenge.jpg", "vortec space.jpg", "wind.jpg",  # XP wallpapers
    "img0.jpg", "img1.jpg", "img2.jpg",  # Windows 7–10 default wallpapers
    "aqua blue.jpg", "aqua graphite.jpg", "aurora.jpg", "leopard aurora.jpg", "snow leopard aurora.jpg",
    "lion.jpg", "mountain lion.jpg", "mavericks.jpg", "yosemite.jpg", "el capitan.jpg",  # macOS
}
UNIT_REASONS = {"code_project": "code_project", "browser_profile": "browser_profile"}


# ---------------------------------------------------------------- capabilities
class Caps:
    """Optional tools, detected once."""

    def __init__(self):
        try:
            from PIL import Image, ImageFile
        except ImportError:
            sys.exit("'archivero media' needs Pillow: pip install \"archivero[photos]\"")
        Image.MAX_IMAGE_PIXELS = None
        ImageFile.LOAD_TRUNCATED_IMAGES = True  # old backups are full of half-copied JPEGs
        self.Image = Image
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
            self.heif = True
        except Exception:
            self.heif = False
        self.ffprobe = shutil.which("ffprobe")
        self.ffmpeg = shutil.which("ffmpeg")
        self.video = bool(self.ffprobe and self.ffmpeg)

    def satisfiable(self, on_disk):
        """Reasons for a 'partial' row that this run can now fix."""
        s = set()
        if self.video and on_disk:
            s.add("ffmpeg")
        if self.heif:
            s.add("heif")
        if on_disk:
            s.add("archive_video")
        return s


# ---------------------------------------------------------------- hashing
def hash_bytes(data):
    return hashlib.blake2b(data, digest_size=20).hexdigest()


def hash_stream(f):
    h = hashlib.blake2b(digest_size=20)
    while True:
        chunk = f.read(CHUNK)
        if not chunk:
            break
        h.update(chunk)
    return h.hexdigest()


def hash_file(path):
    with open(path, "rb") as f:
        return hash_stream(f)


# ---------------------------------------------------------------- metadata
def _clean(v):
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    v = str(v).replace("\x00", "").strip()
    return v or None


def _exif_date(v):
    v = _clean(v)
    if not v:
        return None
    m = re.match(r"(\d{4})[:\-](\d\d)[:\-](\d\d)[ T](\d\d):(\d\d):(\d\d)", v)
    if not m:
        return None
    d = "%s-%s-%s %s:%s:%s" % m.groups()
    return d if plausible(d) else None


def _offset(v):
    v = _clean(v)
    if not v:
        return None
    m = re.fullmatch(r"([+-])(\d\d):?(\d\d)", v)
    return "%s%s:%s" % m.groups() if m else ("+00:00" if v.upper() == "Z" else None)


def _ratio(v):
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        try:
            return float(v[0]) / float(v[1])
        except Exception:
            return None


def _dms(v):
    try:
        d, m, s = (_ratio(x) for x in v)
        return d + m / 60.0 + s / 3600.0
    except Exception:
        return None


def exif_meta(exif):
    """Normalized fields from a PIL Exif object."""
    out = {}
    if not exif:
        return out
    try:
        sub = exif.get_ifd(0x8769)
    except Exception:
        sub = {}
    try:
        gps = exif.get_ifd(0x8825)
    except Exception:
        gps = {}
    for tag, src, off_tag in ((0x9003, "exif_original", 0x9011), (0x9004, "exif_digitized", 0x9012)):
        d = _exif_date(sub.get(tag))
        if d:
            out.update(date_taken=d, date_source=src, date_offset=_offset(sub.get(off_tag)))
            break
    else:
        d = _exif_date(exif.get(0x0132))
        if d:
            out.update(date_taken=d, date_source="exif_datetime", date_offset=_offset(sub.get(0x9010)))
    if gps:
        lat, lon = _dms(gps.get(2)), _dms(gps.get(4))
        if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
            if _clean(gps.get(1)) == "S":
                lat = -lat
            if _clean(gps.get(3)) == "W":
                lon = -lon
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                out["gps_lat"], out["gps_lon"] = round(lat, 7), round(lon, 7)
                alt = _ratio(gps.get(6))
                if alt is not None:
                    out["gps_alt"] = round(-alt if gps.get(5) in (1, b"\x01") else alt, 2)
    out["make"] = _clean(exif.get(0x010F))
    out["model"] = _clean(exif.get(0x0110))
    out["software"] = _clean(exif.get(0x0131))
    out["serial"] = _clean(sub.get(0xA431)) or _clean(exif.get(0xC62F))
    lens = _clean(sub.get(0xA434))
    lmake = _clean(sub.get(0xA433))
    if lens and lmake and not lens.lower().startswith(lmake.lower()):
        lens = "%s %s" % (lmake, lens)
    out["lens"] = lens
    o = exif.get(0x0112)
    if isinstance(o, int) and 1 <= o <= 8:
        out["orientation"] = o
    return {k: v for k, v in out.items() if v is not None}


def _iso6709(v):
    m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?", v or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat, lon) == (0.0, 0.0):
        return None
    return lat, lon, (float(m.group(3)) if m.group(3) else None)


def _video_date(tags):
    local = tags.get("com.apple.quicktime.creationdate")
    if local:
        d = _exif_date(local)
        m = re.search(r"(Z|[+-]\d\d:?\d\d)$", local.strip())
        if d:
            return d, "quicktime", _offset(m.group(1)) if m else None
    utc = tags.get("creation_time") or tags.get("date")
    d = _exif_date(utc)
    if d:
        return d, "video_utc", "+00:00"
    return None, None, None


# ---------------------------------------------------------------- pictures
def make_thumb(caps, im, px, quality):
    """WebP thumbnail bytes and 64-bit dHash of an already oriented image."""
    Image = caps.Image
    has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
    im = im.convert("RGBA" if has_alpha else "RGB")
    im.thumbnail((px, px), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=quality, method=4)
    g = im.convert("L").resize((9, 8), Image.LANCZOS)
    p = g.tobytes()  # 72 grey levels, row by row
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if p[row * 9 + col] < p[row * 9 + col + 1] else 0)
    return buf.getvalue(), "%016x" % bits


def _largest_embedded_jpeg(caps, data):
    """The largest JPEG preview embedded in a RAW file, as (offset, image) or None."""
    best = None
    start, tries = 0, 0
    while tries < 64:
        i = data.find(b"\xff\xd8\xff", start)
        if i < 0:
            break
        start, tries = i + 3, tries + 1
        try:
            im = caps.Image.open(io.BytesIO(data[i:]))
            area = im.size[0] * im.size[1]
        except Exception:
            continue
        if best is None or area > best[0]:
            best = (area, i)
    if best is None:
        return None
    return caps.Image.open(io.BytesIO(data[best[1]:]))


def _oriented(caps, im, orientation):
    im.load()
    if orientation and orientation != 1:
        method = {2: caps.Image.FLIP_LEFT_RIGHT, 3: caps.Image.ROTATE_180, 4: caps.Image.FLIP_TOP_BOTTOM,
                  5: caps.Image.TRANSPOSE, 6: caps.Image.ROTATE_270, 7: caps.Image.TRANSVERSE,
                  8: caps.Image.ROTATE_90}.get(orientation)
        if method is not None:
            im = im.transpose(method)
    return im


def picture_info(caps, src, kind, ext, cfg):
    """Metadata, thumbnail and dHash of a photo. src: bytes, or a path for very large files."""
    px = cfg["media_thumb_px"]
    rec = {"kind": kind}
    data = src if isinstance(src, (bytes, bytearray)) else None

    if kind == "raw":
        if data is None:
            with open(src, "rb") as f:
                data = f.read()
        meta = {}
        if data[:4] in (b"II*\x00", b"MM\x00*"):
            try:
                ex = caps.Image.Exif()
                ex.load(bytes(data[:4 * 1024 * 1024]))
                meta = exif_meta(ex)
            except Exception:
                meta = {}
        prev = _largest_embedded_jpeg(caps, data)
        if prev is None:
            try:
                prev = caps.Image.open(io.BytesIO(data))
                prev.load()
            except Exception:
                return dict(rec, status="partial", missing="raw", **meta)
        if not meta:
            meta = exif_meta(prev.getexif())
        rec.update(meta)
        rec["format"] = "RAW (%s)" % ext.upper() if ext else "RAW"
        prev.draft("RGB", (px * 2, px * 2))
        im = _oriented(caps, prev, rec.get("orientation"))
        rec["width"], rec["height"] = im.size  # the preview's size: the sensor size is not read
        rec["thumb"], rec["dhash"] = make_thumb(caps, im, px, cfg["media_thumb_quality"])
        rec["status"] = "ok"
        return rec

    try:
        im = caps.Image.open(io.BytesIO(data) if data is not None else src)
    except Exception as err:
        if ext in HEIF_EXT and not caps.heif:
            return dict(rec, status="partial", missing="heif", format="HEIF")
        return dict(rec, status="error", error=str(err)[:300])
    rec["format"] = im.format
    w, h = im.size
    rec.update(exif_meta(im.getexif()))
    if im.format == "JPEG":
        im.draft(im.mode, (px * 2, px * 2))  # decode at 1/2, 1/4 or 1/8 scale: much faster
    try:
        im = _oriented(caps, im, rec.get("orientation"))
    except Exception as err:
        return dict(rec, status="error", error=str(err)[:300])
    if rec.get("orientation") in (5, 6, 7, 8):
        w, h = h, w
    rec["width"], rec["height"] = w, h
    rec["thumb"], rec["dhash"] = make_thumb(caps, im, px, cfg["media_thumb_quality"])
    rec["status"] = "ok"
    return rec


# ---------------------------------------------------------------- videos
def video_info(caps, path, cfg):
    rec = {"kind": "video"}
    if not caps.video:
        return dict(rec, status="partial", missing="ffmpeg")
    try:
        out = subprocess.run([caps.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
                             capture_output=True, text=True, timeout=120).stdout
        info = json.loads(out or "{}")
    except Exception as err:
        return dict(rec, status="error", error="ffprobe: %s" % str(err)[:280])
    fmt = info.get("format") or {}
    streams = info.get("streams") or []
    if not fmt and not streams:
        return dict(rec, status="error", error="ffprobe could not read this file")
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    rec["format"] = fmt.get("format_long_name") or fmt.get("format_name")
    try:
        rec["duration"] = round(float(fmt.get("duration")), 3)
    except (TypeError, ValueError):
        pass
    vs = next((s for s in streams if s.get("codec_type") == "video"
               and not (s.get("disposition") or {}).get("attached_pic")), None)
    aus = next((s for s in streams if s.get("codec_type") == "audio"), None)
    rot = 0
    if vs:
        rec["video_codec"] = vs.get("codec_name")
        w, h = vs.get("width"), vs.get("height")
        stags = {k.lower(): v for k, v in (vs.get("tags") or {}).items()}
        try:
            rot = int(float(stags.get("rotate", 0)))
        except ValueError:
            rot = 0
        for sd in vs.get("side_data_list") or ():
            if "rotation" in sd:
                rot = int(sd["rotation"])
        rot = (-rot) % 360 if rot < 0 else rot % 360
        if rot in (90, 270) and w and h:
            w, h = h, w
        rec["width"], rec["height"], rec["orientation"] = w, h, rot
        for k, v in stags.items():
            tags.setdefault(k, v)
    if aus:
        rec["audio_codec"] = aus.get("codec_name")
    d, src, off = _video_date(tags)
    if d:
        rec.update(date_taken=d, date_source=src, date_offset=off)
    loc = _iso6709(tags.get("com.apple.quicktime.location.iso6709") or tags.get("location")
                   or tags.get("location-eng"))
    if loc:
        rec["gps_lat"], rec["gps_lon"] = loc[0], loc[1]
        if loc[2] is not None:
            rec["gps_alt"] = loc[2]
    rec["make"] = _clean(tags.get("com.apple.quicktime.make") or tags.get("make")
                         or tags.get("com.android.manufacturer"))
    rec["model"] = _clean(tags.get("com.apple.quicktime.model") or tags.get("model")
                          or tags.get("com.android.model"))
    rec["software"] = _clean(tags.get("com.apple.quicktime.software") or tags.get("software"))
    rec = {k: v for k, v in rec.items() if v is not None}
    if not vs:
        return dict(rec, status="ok")  # audio-only container: nothing to draw
    frame = b""
    dur = rec.get("duration") or 0
    for at in ([min(1.0, dur / 2)] if dur else []) + [0]:
        try:
            frame = subprocess.run([caps.ffmpeg, "-v", "error", "-ss", "%.3f" % at, "-i", path, "-frames:v", "1",
                                    "-f", "image2pipe", "-vcodec", "png", "-"],
                                   capture_output=True, timeout=120).stdout
        except Exception:
            frame = b""
        if frame:
            break
    if not frame:
        return dict(rec, status="error", error="ffmpeg could not decode a frame")
    im = caps.Image.open(io.BytesIO(frame))  # ffmpeg already applied the rotation
    rec["thumb"], rec["dhash"] = make_thumb(caps, im, cfg["media_thumb_px"], cfg["media_thumb_quality"])
    rec["status"] = "ok"
    return rec


# ---------------------------------------------------------------- storage
COLUMNS = ("hash", "kind", "format", "size", "width", "height", "orientation", "duration", "video_codec",
           "audio_codec", "date_taken", "date_source", "date_offset", "gps_lat", "gps_lon", "gps_alt", "make",
           "model", "serial", "lens", "software", "dhash", "thumb", "status", "missing", "error", "indexed_at")


def media_dir(con):
    """Thumbnail cache beside the database: <db>.media/"""
    db_file = next((r[2] for r in con.execute("PRAGMA database_list") if r[1] == "main"), "")
    if not db_file:
        sys.exit("The media index needs a database file (not an in-memory database).")
    return db_file + ".media"


def thumb_relpath(h):
    return "%s/%s/%s.webp" % (h[:2], h[2:4], h)


def _write_thumb(root, h, data):
    rel = thumb_relpath(h)
    dest = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = "%s.%d.tmp" % (dest, threading.get_ident())
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)
    return rel


def _store(con, h, size, rec):
    row = dict(rec)
    row.update(hash=h, size=size, indexed_at=time.time())
    con.execute("INSERT OR REPLACE INTO media(%s, nonpersonal, nonpersonal_why) VALUES(%s, "
                "COALESCE((SELECT nonpersonal FROM media WHERE hash=?), 0), "
                "(SELECT nonpersonal_why FROM media WHERE hash=?))"
                % (",".join(COLUMNS), ",".join("?" * len(COLUMNS))),
                [row.get(c) for c in COLUMNS] + [h, h])


# ---------------------------------------------------------------- the run
class _Run:
    def __init__(self, con, cfg, force):
        self.con, self.cfg, self.force = con, cfg, force
        self.caps = Caps()
        self.thumbs = media_dir(con)
        self.max_read = cfg["media_max_read_mb"] * 1024 * 1024
        self.snapshot = set()  # hashes complete before this run (empty with --force)
        self.done = set()  # hashes indexed in this run (or complete before it)
        self.stats = defaultdict(int)
        self.bytes = 0

    def known(self, h):
        """True when content h needs no more work (safe to call from worker threads)."""
        return h in self.done or h in self.snapshot

    def pending_sql(self, table, on_disk):
        sat = sorted(self.caps.satisfiable(on_disk))
        if self.force:
            return "1", []
        marks = ",".join("?" * len(sat)) or "''"
        cond = ("(t.full_hash IS NULL OR NOT EXISTS (SELECT 1 FROM media m WHERE m.hash = t.full_hash AND "
                "NOT (m.status = 'partial' AND m.missing IN (%s))))" % marks)
        return cond.replace("t.", table + "."), sat

    # -- one file on disk (runs in a worker thread)
    def _disk_job(self, row):
        fid, path, size, cat, h = row
        kind = KIND_OF[cat]
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        try:
            if kind == "video":
                if not h:
                    h = hash_file(path)
                if self.known(h):
                    return fid, h, None, None
                return fid, h, video_info(self.caps, path, self.cfg), None
            if size <= self.max_read:
                with open(path, "rb") as f:
                    src = f.read()
                h = h or hash_bytes(src)
            else:
                h = h or hash_file(path)
                src = path
            if self.known(h):
                return fid, h, None, None
            rec = picture_info(self.caps, src, kind, ext, self.cfg)
            return fid, h, rec, None
        except OSError as err:
            return fid, None, None, str(err)
        except Exception as err:  # a broken file must never stop the sweep
            if not h:
                return fid, None, None, str(err)
            return fid, h, {"kind": kind, "status": "error", "error": str(err)[:300]}, None

    def _finish(self, h, size, rec):
        if rec.get("thumb"):
            rec["thumb"] = _write_thumb(self.thumbs, h, rec["thumb"])
            self.stats["thumbs"] += 1
        _store(self.con, h, size, rec)
        self.done.add(h)
        self.stats[rec["status"]] += 1

    def files(self, include_long):
        cats = ["image", "raw", "video_short"] + (["video_long"] if include_long else [])
        cond, args = self.pending_sql("files", True)
        rows = self.con.execute(
            "SELECT id, path, size, category, full_hash FROM files WHERE size > 0 AND category IN (%s) AND %s "
            "ORDER BY dir, name" % (",".join("?" * len(cats)), cond), cats + args).fetchall()
        seen, todo = set(), []
        for r in rows:  # identical copies already hashed: only the first one is read
            if r[4]:
                if r[4] in seen:
                    continue
                seen.add(r[4])
            todo.append(r)
        if not todo:
            print("Media on disk: nothing pending.")
            return
        total = sum(r[2] for r in todo)
        print("Media on disk pending: %d files, %s" % (len(todo), human(total)))
        prog = Progress()
        n = pending = 0
        workers = max(1, int(self.cfg["media_workers"]))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for start in range(0, len(todo), 500):
                batch = todo[start:start + 500]
                for (fid, h, rec, err), r in zip(ex.map(self._disk_job, batch), batch):
                    n += 1
                    self.bytes += r[2]
                    if err:
                        self.con.execute("INSERT INTO errors(path,error,at) VALUES(?,?,?)",
                                         (r[1], "media: " + err, time.time()))
                        self.stats["unreadable"] += 1
                    else:
                        if not r[4]:
                            self.con.execute("UPDATE files SET full_hash=? WHERE id=?", (h, fid))
                        if rec is not None and h not in self.done:
                            self._finish(h, r[2], rec)
                        else:
                            self.stats["copies"] += 1
                    pending += 1
                    if pending >= 200:
                        self.con.commit()
                        pending = 0
                    prog.show("Media: %d/%d · %s/%s · %.1f MB/s" % (
                        n, len(todo), human(self.bytes), human(total),
                        self.bytes / 1048576 / max(prog.elapsed(), 0.001)))
        self.con.commit()
        prog.done("Media on disk: %d files read in %.0f s." % (n, prog.elapsed()))

    def members(self):
        """Pictures inside archives and disk images, streamed in memory (no extraction)."""
        from .archives import backend_for

        cond, args = self.pending_sql("members", False)
        rows = self.con.execute(
            "SELECT members.container, members.inner_path, members.category, members.size, c.format "
            "FROM members JOIN containers c ON c.path = members.container "
            "WHERE members.is_dir = 0 AND members.encrypted = 0 AND members.size > 0 "
            "AND members.category IN ('image','raw','video_short','video_long') "
            "AND c.status IN ('listed','inspected') AND %s ORDER BY members.container" % cond, args).fetchall()
        wanted = defaultdict(dict)
        fmts = {}
        for c, inner, cat, size, fmt in rows:
            wanted[c][inner] = (cat, size)
            fmts[c] = fmt
        if not wanted:
            print("Media inside archives: nothing pending.")
            return
        print("Media inside archives pending: %d members in %d archives" % (len(rows), len(wanted)))
        prog = Progress()
        n = 0
        for i, (c, items) in enumerate(sorted(wanted.items()), 1):
            b = backend_for(fmts[c])
            if b is None:
                continue
            try:
                for m, f in b.streams(c):
                    key = m.inner_path.rstrip("/")
                    if key not in items:
                        continue
                    cat, size = items[key]
                    kind = KIND_OF[cat]
                    if size > self.max_read:
                        continue
                    data = f.read()
                    h = hash_bytes(data)
                    self.bytes += len(data)
                    n += 1
                    self.con.execute("UPDATE members SET full_hash=? WHERE container=? AND inner_path=?",
                                     (h, c, key))
                    if self.known(h):
                        self.stats["copies"] += 1
                        continue
                    if kind == "video":
                        rec = {"kind": "video", "status": "partial", "missing": "archive_video"}
                    else:
                        try:
                            rec = picture_info(self.caps, data, kind, os.path.splitext(key)[1].lower()[1:],
                                               self.cfg)
                        except Exception as err:
                            rec = {"kind": kind, "status": "error", "error": str(err)[:300]}
                    self._finish(h, len(data), rec)
                    prog.show("Media in archives: %d/%d archives · %d members · %s" % (
                        i, len(wanted), n, os.path.basename(c)))
            except Exception as err:
                self.con.execute("INSERT INTO errors(path,error,at) VALUES(?,?,?)",
                                 (c, "media: %s" % str(err)[:300], time.time()))
            self.con.commit()
        prog.done("Media inside archives: %d members read in %.0f s." % (n, prog.elapsed()))


# ---------------------------------------------------------------- non-personal flags
def place_reasons(segs_low, name_low, zone, unit_kind):
    """ADR 0006 reasons that come from where one copy sits (empty set: looks personal)."""
    r = set()
    if zone in NONPERSONAL_ZONES:
        r.add("zone:" + zone)
    dirs = set(segs_low)
    if dirs & ICON_DIRS:
        r.add("icon_folder")
    if dirs & SAMPLE_DIRS:
        r.add("wallpaper_or_sample_folder")
    if dirs & CACHE_DIRS:
        r.add("cache_folder")
    if unit_kind in UNIT_REASONS:
        r.add(UNIT_REASONS[unit_kind])
    return r


def content_reasons(rec, name_lows, cfg):
    r = set()
    camera = rec["make"] or rec["model"]
    if rec["kind"] != "video" and rec["width"] and rec["height"] and not camera and \
            max(rec["width"], rec["height"]) <= cfg["media_tiny_px"]:
        r.add("tiny")
    if not camera and name_lows & SAMPLE_NAMES:
        r.add("stock_name")
    return r


def flag_nonpersonal(con, cfg):
    """Recompute the ADR 0006 flag of every asset from its content and all its copies."""
    root = require_root(con)
    media = {r[0]: {"kind": r[1], "width": r[2], "height": r[3], "make": r[4], "model": r[5]}
             for r in con.execute("SELECT hash, kind, width, height, make, model FROM media")}
    if not media:
        return 0
    places = defaultdict(list)  # hash -> [set of reasons per copy]
    names = defaultdict(set)

    def segs(p):
        rel = os.path.relpath(p, root)
        return () if rel == "." else tuple(rel.split(os.sep))

    files = con.execute("SELECT path, dir, name, full_hash FROM files").fetchall()
    lay = Layout()
    for path, d, name, h in files:
        lay.add_file(segs(path))
    for (p,) in con.execute("SELECT path FROM detections WHERE mode=?", (PRUNED,)):
        lay.add_dir(segs(p))
    lay.finish()
    units = sorted(((p, k) for p, k in con.execute("SELECT path, kind FROM detections")
                    if k in UNIT_REASONS), key=lambda t: -len(t[0]))

    def unit_of(d):
        for p, k in units:
            if d == p or d.startswith(p + os.sep):
                return k
        return None

    for path, d, name, h in files:
        if h not in media:
            continue
        ds = segs(d)
        zone = lay.file_zone(ds, name)[0]
        places[h].append(place_reasons(tuple(s.lower() for s in ds), name.lower(), zone, unit_of(d)))
        names[h].add(name.lower())

    by_container = defaultdict(list)
    for c, inner, h in con.execute("SELECT container, inner_path, full_hash FROM members WHERE is_dir=0"):
        by_container[c].append((inner.replace("\\", "/"), h))
    for c, mem in by_container.items():
        if not any(h in media for _, h in mem):
            continue
        clay = Layout()
        for inner, h in mem:
            clay.add_file(tuple(p for p in inner.split("/") if p))
        clay.finish()
        outer = place_reasons(tuple(s.lower() for s in segs(os.path.dirname(c))), "", lay.file_zone(
            segs(os.path.dirname(c)), os.path.basename(c))[0], unit_of(os.path.dirname(c)))
        for inner, h in mem:
            if h not in media:
                continue
            parts = tuple(p for p in inner.split("/") if p)
            zone = clay.file_zone(parts[:-1], parts[-1] if parts else "")[0]
            places[h].append(outer | place_reasons(tuple(s.lower() for s in parts[:-1]), parts[-1].lower(),
                                                   zone, None))
            names[h].add(parts[-1].lower() if parts else "")

    n = 0
    for h, rec in media.items():
        why = content_reasons(rec, names[h], cfg)
        copies = places.get(h) or []
        if copies and all(copies):
            why |= set().union(*copies)
        con.execute("UPDATE media SET nonpersonal=?, nonpersonal_why=? WHERE hash=?",
                    (1 if why else 0, ",".join(sorted(why)) or None, h))
        n += bool(why)
    con.commit()
    return n


def build_media_index(con, cfg, archives=False, long_videos=False, force=False):
    """'archivero media': index every photo and video (resumable)."""
    require_root(con)
    run = _Run(con, cfg, force)
    if not run.caps.video:
        print("Warning: ffmpeg/ffprobe not found; videos are hashed but get no metadata or thumbnail "
              "(they are completed on a later run once ffmpeg is installed).", file=sys.stderr)
    if not run.caps.heif:
        print("Note: pillow-heif is not installed; HEIC/HEIF photos are hashed but not decoded "
              "(pip install \"archivero[heif]\").", file=sys.stderr)
    run.snapshot = _complete_hashes(con, run.caps) if not force else set()
    os.makedirs(run.thumbs, exist_ok=True)
    run.files(long_videos)
    if archives:
        run.members()
    flagged = flag_nonpersonal(con, cfg)
    total, ok, partial, err, thumbs = con.execute(
        "SELECT COUNT(*), SUM(status='ok'), SUM(status='partial'), SUM(status='error'), "
        "SUM(thumb IS NOT NULL) FROM media").fetchone()
    kinds = dict(con.execute("SELECT kind, COUNT(*) FROM media GROUP BY kind").fetchall())
    print("Media index: %d unique assets (%d images, %d RAW, %d videos), %d thumbnails in %s; "
          "%d flagged as non-personal; %d partial, %d errors." % (
              total or 0, kinds.get("image", 0), kinds.get("raw", 0), kinds.get("video", 0), thumbs or 0,
              run.thumbs, flagged, partial or 0, err or 0))
    if run.stats["copies"]:
        print("Identical copies recognized without decoding again: %d." % run.stats["copies"])
    return run.stats


def _complete_hashes(con, caps):
    sat = caps.satisfiable(True)
    return {h for h, st, miss in con.execute("SELECT hash, status, missing FROM media")
            if not (st == "partial" and miss in sat)}
