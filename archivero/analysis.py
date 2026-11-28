"""Inventory analysis: per-folder aggregates, duplicates, units and cleanup
proposals. Used by 'report' and 'plan'."""

import json
import os
import re
import time
from collections import defaultdict, Counter

from .categories import DOC_CATS, MEDIA_CATS, is_software
from .db import get_meta, require_root
from .detectors import PRUNED, UNIT_KINDS
from .util import ancestors, human
from .zones import Layout, app_profile_hint, valuable_appdata, year_of

# Paths that look like backup copies (multilingual on purpose).
BACKUPISH = re.compile(r"(\bcopia\b|\bcopy\b|backup|\bbak\b|\bold\b|antiguo|duplicad|\(\d+\)|~$)", re.I)

# Pruned folder kinds that are proposed for cleanup, and why.
PRUNED_CLEANUP = {
    "dependencies": ("regenerable", "dependencies that can be reinstalled (npm install, etc.)"),
    "cache": ("regenerable", "regenerable cache"),
    "virtualenv": ("regenerable", "regenerable virtual environment"),
    "trash": ("trash", "system trash folder"),
    "system": ("system", "internal operating-system folder"),
}

PERSONAL_CATS = {"image", "raw", "video_short", "video_long", "audio", "pdf", "document", "spreadsheet",
                 "presentation", "email", "design"}

SUGGESTIONS = {
    "already_extracted": "Content already on disk: the archive can go",
    "partially_extracted": "Part of the content is already on disk: inspect to see what is missing",
    "inspect": "Holds personal files not found elsewhere: inspect or extract",
    "software_image": "Software or OS image",
    "no_personal_content": "Nothing personal detected",
    "empty": "Empty",
    "deferred": "No index and too big to list: run 'archives inspect'",
    "unsupported": "Format not readable yet",
    "error": "Could not be read",
    "not_listed": "Not listed yet: run 'archives list'",
}

ZONE_CLEANUP = {
    "system": ("os_files", "operating-system files"),
    "applications": ("installed_programs", "installed programs (reinstall from the vendor if needed)"),
    "cache": ("cache_files", "caches and temporary files"),
    "trash": ("trash", "trash folder"),
}
KEEP_ZONES = {"user_data", "unknown"}  # where personal-looking files count as personal

UNIT_LABELS = {"code_project": "Code project", "thunderbird_profile": "Thunderbird profile",
               "browser_profile": "Browser profile"}


def set_name(label):
    """Readable name of a known-file set label (known.py): 'nsrl:RDS_2025' -> 'NSRL'."""
    if label.startswith("nsrl:"):
        return "NSRL"
    if label.startswith("bloom:") or label.startswith("circl"):
        return "CIRCL hashlookup"
    return label or "a known-file set"


class Analysis:
    def __init__(self, con, cfg):
        self.con = con
        self.cfg = cfg
        self.root = require_root(con)
        self.now = time.time()
        self.cutoff = self.now - cfg["obsolete_years"] * 365.25 * 86400

        det = con.execute("SELECT path, kind, mode, note, regenerable, files, bytes, newest, oldest "
                          "FROM detections").fetchall()
        self.detections = det
        self.units = {r[0]: r[1] for r in det if r[1] in UNIT_KINDS}
        self.pruned = [r for r in det if r[2] == PRUNED]
        self.tags = [r for r in det if r[2] != PRUNED and r[1] not in UNIT_KINDS]
        self._unit_cache = {}

        self.files = con.execute(
            "SELECT id, path, dir, name, category, subtype, size, mtime, date_best, date_source, "
            "extra, full_hash FROM files").fetchall()
        self._known()
        self._duplicates()
        self._layout()
        self._containers()
        self._aggregate()
        self._cleanup()

    # ------------------------------------------------------------------ units
    def unit_of_dir(self, d):
        """(path, kind) of the top-most unit containing folder d (inclusive), or None."""
        if d in self._unit_cache:
            return self._unit_cache[d]
        res = None
        for c in reversed(ancestors(d, self.root)):
            if c in self.units:
                res = (c, self.units[c])
                break
        self._unit_cache[d] = res
        return res

    def top_units(self):
        return sorted(p for p in self.units if self.unit_of_dir(p)[0] == p)

    # ------------------------------------------------------------- known files
    def _known(self):
        """Results of the known-file filter (ADR 0007), per file."""
        status = {h: (st, src, name) for h, st, src, name in
                  self.con.execute("SELECT hash, status, source, name FROM known WHERE status IS NOT NULL")}
        self.known_sets = json.loads(get_meta(self.con, "known_sets") or "[]")
        self.known_status = {}  # file id -> (status, source, name)
        for f in self.files:
            if f[11] in status:
                self.known_status[f[0]] = status[f[11]]
        self.known_ids = {fid for fid, v in self.known_status.items() if v[0] == "known"}
        self.n_known_hashed = self.con.execute("SELECT COUNT(*) FROM known").fetchone()[0]

    def known_label(self):
        """'CIRCL hashlookup and NSRL': the sets the filter used, for reasons in proposals."""
        names = []
        for s in self.known_sets:
            n = set_name(s.get("label", ""))
            if n not in names:
                names.append(n)
        return " and ".join(names) or "public hash sets"

    # ------------------------------------------------------------- duplicates
    def _duplicates(self):
        groups = defaultdict(list)
        for f in self.files:
            fid, path, d, name, cat, sub, size, mtime, date, src, extra, h = f
            if h and cat != "system_junk":
                groups[(h, size)].append(f)
        self.dup_groups = []
        self.dup_of = {}  # id -> path of the copy that is kept
        for (h, size), members in groups.items():
            if len(members) < 2:
                continue

            def key(f):
                # Keep: inside a unit > not backup-looking > oldest > shortest path.
                return (0 if self.unit_of_dir(f[2]) else 1, 1 if BACKUPISH.search(f[1]) else 0,
                        f[8] or "9999", len(f[1]), f[1])
            keeper = min(members, key=key)
            for f in members:
                if f[0] != keeper[0]:
                    self.dup_of[f[0]] = keeper[1]
            self.dup_groups.append((h, size, members, keeper[0]))
        self.dup_groups.sort(key=lambda g: -(len(g[2]) - 1) * g[1])
        self.dup_wasted = sum((len(g[2]) - 1) * g[1] for g in self.dup_groups)

    # ------------------------------------------------------------ zones / sources
    def segs(self, path):
        rel = os.path.relpath(path, self.root)
        return () if rel == "." else tuple(rel.split(os.sep))

    def _layout(self):
        """Zones for every file, and the sources (machines / profiles) they came from."""
        lay = self.layout = Layout()
        for f in self.files:
            lay.add_file(self.segs(f[1]))
        for r in self.pruned:
            lay.add_dir(self.segs(r[0]))
        lay.finish()
        for fid, path, d, name, cat, sub, size, mtime, date, src, extra, h in self.files:
            if extra and '"os_version"' in extra:
                try:
                    lay.add_version(self.segs(d), json.loads(extra)["os_version"])
                except (ValueError, KeyError, TypeError):
                    pass
        self.file_zone = {}
        self.file_source = {}
        self.zone_tot = defaultdict(lambda: [0, 0])
        self.personal_ids = set()
        unusual = Counter()
        for fid, path, d, name, cat, sub, size, mtime, date, src, extra, h in self.files:
            ds = self.segs(d)
            zone = lay.file_zone(ds, name)[0]
            self.file_zone[fid] = zone
            self.zone_tot[zone][0] += 1
            self.zone_tot[zone][1] += size or 0
            personal = cat in PERSONAL_CATS and zone in KEEP_ZONES and fid not in self.known_ids
            if personal:
                self.personal_ids.add(fid)
            elif cat in PERSONAL_CATS and zone in ("system", "applications"):
                unusual[d] += 1
            source = lay.source_of(ds)
            if source is not None:
                self.file_source[fid] = source
                source.observe_file(size, mtime, year_of(date), personal)
                if cat in ("executable", "disk_image", "compressed", "config", "other", "no_extension"):
                    source.observe_name(name)
        for dsegs in lay.dirs():
            if dsegs:
                source = lay.source_of(dsegs)
                if source is not None:
                    source.observe_name(dsegs[-1])
        self.unusual_personal = unusual
        # Executables in system or program folders that no known-file set has: custom software or tampering.
        self.unknown_sw = sorted(
            ((f[1], f[6] or 0) for f in self.files if f[4] == "executable"
             and self.file_zone.get(f[0]) in ("system", "applications")
             and self.known_status.get(f[0], ("",))[0] == "unknown"), key=lambda t: t[0])
        # Mail and browser profiles: where do they come from?
        self.app_profiles = []
        for path, kind in sorted(self.units.items()):
            if kind not in ("thunderbird_profile", "browser_profile"):
                continue
            ps = self.segs(path)
            os_name, era = app_profile_hint(ps)
            source = lay.source_of(ps)
            what = {"thunderbird_profile": "Thunderbird profile", "browser_profile": "Firefox profile"}[kind]
            text = what
            if os_name:
                text += " from %s%s" % (os_name, " " + era if era else "")
            if source is not None:
                text += "; source: %s" % source.describe()
            self.app_profiles.append((path, kind, text))
        # Valuable application data (iPhone backups, mail stores, keys…), top-most folder only.
        self.valuable = []
        seen = set()
        for dsegs in sorted(lay.dirs(), key=len):
            if not dsegs or any(dsegs[:i] in seen for i in range(len(dsegs))):
                continue
            label = valuable_appdata(dsegs)
            if label and label != valuable_appdata(dsegs[:-1]):
                seen.add(dsegs)
                self.valuable.append((os.path.join(self.root, *dsegs), label))

    def source_name(self, source):
        return os.path.join(self.root, *source.segs) if source else ""

    # ------------------------------------------------------ archives / images
    def _containers(self):
        """Compare each archive's members with the files on disk and suggest a next step."""
        self.containers = {}
        self.non_software = set()  # archives with personal content: never "software"
        rows = self.con.execute("SELECT path, format, backend, status, n_members, n_dirs, total_size, note "
                                "FROM containers").fetchall()
        if not rows:
            return
        by_name = defaultdict(list)  # (name, size) -> [(dir, full_hash)]
        by_hash = set()
        for fid, path, d, name, cat, sub, size, mtime, date, src, extra, h in self.files:
            if size:
                by_name[(name.lower(), size)].append((d, h))
            if h:
                by_hash.add(h)
        members = defaultdict(list)
        for c, inner, size, cat, h, name in self.con.execute(
                "SELECT container, inner_path, size, category, full_hash, name FROM members WHERE is_dir=0"):
            members[c].append((inner.replace("\\", "/"), size or 0, cat, h, name or ""))
        self.member_zone = {}

        for path, fmt, backend, status, n, nd, total, note in rows:
            mem = members.get(path, [])
            info = {"path": path, "format": fmt, "backend": backend, "status": status, "note": note or "",
                    "files": len(mem), "size": total or 0, "cnt": Counter(), "sz": Counter(),
                    "found": 0, "found_share": 0.0, "confidence": "", "extracted_at": "",
                    "personal": 0, "personal_missing": Counter(), "sw_bytes": 0, "suggestion": "",
                    "zones": Counter(), "hint": ""}
            votes = defaultdict(lambda: [0, 0, set()])  # anchor -> [name+size matches, hash matches, dirs]
            tops = set()
            inspected = status == "inspected"
            lay = Layout()
            for inner, size, cat, h, name in mem:
                lay.add_file(tuple(p for p in inner.split("/") if p))
            lay.finish()
            zones = Counter()
            for inner, size, cat, h, name in mem:
                parts = tuple(p for p in inner.split("/") if p)
                z = lay.file_zone(parts[:-1], parts[-1] if parts else "")[0]
                zones[z] += size
                self.member_zone[(path, inner)] = z
                src = lay.source_of(parts[:-1])
                if src is not None:
                    src.observe_file(size, None, None, cat in PERSONAL_CATS and z in KEEP_ZONES)
                    src.observe_name(parts[-1] if parts else "")
            for dsegs in lay.dirs():
                src = lay.source_of(dsegs) if dsegs else None
                if src is not None:
                    src.observe_name(dsegs[-1])
            info["zones"] = zones
            info["hint"] = "; ".join(s.describe() for s in lay.sources.values())
            for inner, size, cat, h, name in mem:
                info["cnt"][cat] += 1
                info["sz"][cat] += size
                if is_software(cat, name):
                    info["sw_bytes"] += size
                parts = [p for p in inner.split("/") if p]
                if parts:
                    tops.add(parts[0] if len(parts) > 1 else "")
                cands = by_name.get((name.lower(), size)) if size else None
                found = bool(cands) or (inspected and h in by_hash)
                keep = self.member_zone[(path, inner)] in KEEP_ZONES
                if found:
                    info["found"] += 1
                elif cat in PERSONAL_CATS and keep:
                    info["personal_missing"][cat] += 1
                if cat in PERSONAL_CATS and keep:
                    info["personal"] += 1
                # Which folder would correspond to the archive root?
                depth = len(parts) - 1
                seen = set()
                for d, fh in cands or ():
                    anchor = d
                    for _ in range(depth):
                        anchor = os.path.dirname(anchor)
                    if len(anchor) < len(self.root) or anchor in seen:
                        continue
                    seen.add(anchor)
                    votes[anchor][0] += 1
                    votes[anchor][2].add(d)
                    if h and fh == h:
                        votes[anchor][1] += 1
            nf = sum(1 for m in mem if m[1] > 0) or 0
            if votes and nf:
                anchor, (nmatch, nexact, mdirs) = max(votes.items(), key=lambda kv: kv[1][0])
                info["found_share"] = nmatch / nf
                info["confidence"] = "confirmed (hash)" if nexact >= nmatch * 0.99 and nexact else "likely (name+size)"
                # With a single top folder in the archive, point at the matching folder on disk
                # (it may have been renamed); otherwise at the folder that maps to the archive root.
                common = os.path.commonpath(sorted(mdirs)) if mdirs else anchor
                info["extracted_at"] = common if (len(tops) == 1 and "" not in tops) else anchor
            missing = sum(info["personal_missing"].values())
            if status in ("deferred", "unsupported", "error"):
                sug = status
            elif status not in ("listed", "inspected"):
                sug = "not_listed"
            elif nf == 0:
                sug = "empty"
            elif info["found_share"] >= self.cfg["archive_match_share"]:
                sug = "already_extracted"
            elif missing and (missing >= 0.2 * nf or missing >= 50):
                sug = "inspect"
            elif info["size"] and (info["sw_bytes"] + zones["system"] + zones["applications"]) / info["size"] >= 0.6:
                sug = "software_image"
            elif info["found_share"] >= 0.3:
                sug = "partially_extracted"
            elif missing:
                sug = "inspect"
            else:
                sug = "no_personal_content"
            info["suggestion"] = sug
            if sug in ("inspect", "already_extracted", "partially_extracted"):
                self.non_software.add(path)
            self.containers[path] = info

    def is_sw(self, path, cat, name):
        return path not in self.non_software and is_software(cat, name)

    # ------------------------------------------------------------- aggregates
    def _new_agg(self):
        return {"n": 0, "size": 0, "cnt": defaultdict(int), "sz": defaultdict(int), "dmin": None,
                "dmax": None, "mmax": None, "sw": 0, "kn": 0, "dupn": 0, "dupb": 0}

    def _add(self, start, cat, size, n, date, mtime, sw, dup, known=False):
        for p in ancestors(start, self.root):
            a = self.agg.get(p)
            if a is None:
                a = self.agg[p] = self._new_agg()
            a["n"] += n
            a["size"] += size
            a["cnt"][cat] += n
            a["sz"][cat] += size
            if date:
                if a["dmin"] is None or date < a["dmin"]:
                    a["dmin"] = date
                if a["dmax"] is None or date > a["dmax"]:
                    a["dmax"] = date
            if mtime and (a["mmax"] is None or mtime > a["mmax"]):
                a["mmax"] = mtime
            if sw:
                a["sw"] += size
            if known:
                a["kn"] += size
            if dup:
                a["dupn"] += n
                a["dupb"] += size

    def _aggregate(self):
        self.agg = {}
        self.cat_tot = defaultdict(lambda: [0, 0])
        self.year_cat = defaultdict(lambda: defaultdict(int))
        self.unit_stats = defaultdict(lambda: {"files": 0, "bytes": 0, "mbox": 0, "mbox_bytes": 0,
                                               "dmin": None, "dmax": None})
        self.music = {"files": 0, "bytes": 0, "artists": defaultdict(lambda: [0, 0])}
        self.loose_mail = defaultdict(lambda: [0, 0])
        self.total_n = self.total_b = 0
        for fid, path, d, name, cat, sub, size, mtime, date, src, extra, h in self.files:
            size = size or 0
            self.total_n += 1
            self.total_b += size
            self.cat_tot[cat][0] += 1
            self.cat_tot[cat][1] += size
            self.year_cat[date[:4] if date else "no date"][cat] += size
            known = fid in self.known_ids
            self._add(d, cat, size, 1, date, mtime, known or self.is_sw(path, cat, name), fid in self.dup_of, known)
            unit = self.unit_of_dir(d)
            if unit:
                us = self.unit_stats[unit[0]]
                us["files"] += 1
                us["bytes"] += size
                if sub == "mbox":
                    us["mbox"] += 1
                    us["mbox_bytes"] += size
                if date:
                    us["dmin"] = min(us["dmin"] or date, date)
                    us["dmax"] = max(us["dmax"] or date, date)
            elif cat == "email" and sub in ("mbox", "pst", "eml", "msg"):
                self.loose_mail[sub][0] += 1
                self.loose_mail[sub][1] += size
            if cat == "audio":
                self.music["files"] += 1
                self.music["bytes"] += size
                if extra:
                    try:
                        artist = json.loads(extra).get("artist")
                    except ValueError:
                        artist = None
                    if artist:
                        self.music["artists"][artist][0] += 1
                        self.music["artists"][artist][1] += size
        for path, kind, mode, note, regen, nfiles, nbytes, newest, oldest in self.pruned:
            nbytes = nbytes or 0
            nfiles = nfiles or 0
            date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(newest)) if newest else None
            cat = "[%s]" % kind
            self.cat_tot[cat][0] += nfiles
            self.cat_tot[cat][1] += nbytes
            self._add(path, cat, nbytes, nfiles, date, newest, kind == "software_bundle", False)

    # ---------------------------------------------------------------- cleanup
    def _under(self, path, dirs):
        return any(p in dirs for p in ancestors(path, self.root))

    def _cleanup(self):
        cfg = self.cfg
        props = []
        cleanup_dirs = set()
        cleanup_files = set()

        # 0) Operating-system, program and cache folders found by layout (top-most only).
        lay = self.layout
        zone_dirs = sorted((d for d in lay.dirs() if d), key=len)
        regions = {}
        for d in zone_dirs:
            z = lay.zone(d)[0]
            if z in ZONE_CLEANUP and lay.zone(d[:-1])[0] != z and not any(d[:i] in regions for i in range(len(d))):
                regions[d] = z
        personal_in = Counter()
        for fid, path, d, name, cat, sub, size, mtime, date, src, extra, h in self.files:
            if cat in PERSONAL_CATS and self.file_zone.get(fid) in ZONE_CLEANUP:
                ds = self.segs(d)
                for i in range(len(ds), 0, -1):
                    if ds[:i] in regions:
                        personal_in[ds[:i]] += 1
                        break
        for d, z in sorted(regions.items()):
            p = os.path.join(self.root, *d)
            a = self.agg.get(p)
            if not a or self.unit_of_dir(p):
                continue
            ptype, what = ZONE_CLEANUP[z]
            source = lay.source_of(d)
            why = what + (" of " + source.describe() if source else "")
            if personal_in[d]:
                why += ". Review: %d files inside look personal (documents, photos…)" % personal_in[d]
            props.append(self._prop(ptype, p, True, a["n"], a["size"], a["mmax"], why))
            cleanup_dirs.add(p)

        # 1) Software folders untouched for years (top-most qualifying folders only).
        sw_set = set()
        for p in sorted(self.agg):
            a = self.agg[p]
            if p == self.root or a["size"] < cfg["software_min_bytes"] or not a["mmax"]:
                continue
            share = a["sw"] / a["size"] if a["size"] else 0
            if share < cfg["software_share"] or a["mmax"] >= self.cutoff:
                continue
            if self.unit_of_dir(p) or self._under(p, sw_set) or self._under(p, cleanup_dirs):
                continue
            sw_set.add(p)
            props.append(self._prop("obsolete_software", p, True, a["n"], a["size"], a["mmax"],
                                    "%.0f%% software; nothing modified since %s" % (
                                        100 * share, time.strftime("%Y", time.localtime(a["mmax"])))))
        cleanup_dirs.update(sw_set)

        # 1b) Folders made of files known to the hash sets (ADR 0007), wherever they were copied.
        if self.known_ids:
            kn_set = set()
            sets = self.known_label()
            for p in sorted(self.agg):
                a = self.agg[p]
                if p == self.root or a["size"] < cfg["software_min_bytes"]:
                    continue
                if a["kn"] / a["size"] < cfg["software_share"]:
                    continue
                if self.unit_of_dir(p) or self._under(p, kn_set) or self._under(p, cleanup_dirs):
                    continue
                kn_set.add(p)
                why = "%.0f%% of its bytes are files known to %s (OS and software vendors)" % (
                    100 * a["kn"] / a["size"], sets)
                personal = sum(n for c, n in a["cnt"].items() if c in PERSONAL_CATS)
                if personal:
                    why += ". Review: %d files inside look personal (documents, photos…)" % personal
                props.append(self._prop("known_software", p, True, a["n"], a["size"], a["mmax"], why))
            cleanup_dirs.update(kn_set)

        # 2) Pruned folders: dependencies, caches, trash, old software bundles.
        for path, kind, mode, note, regen, nfiles, nbytes, newest, oldest in self.pruned:
            if self._under(path, cleanup_dirs):
                continue
            if kind in PRUNED_CLEANUP:
                ptype, why = PRUNED_CLEANUP[kind]
            elif kind == "software_bundle" and newest and newest < self.cutoff and not self.unit_of_dir(path):
                ptype, why = "obsolete_software", "software bundle unchanged since %s" % time.strftime(
                    "%Y", time.localtime(newest))
            else:
                continue
            props.append(self._prop(ptype, path, True, nfiles, nbytes, newest, why))
            cleanup_dirs.add(path)

        # 3) Single files: old installers, system junk and duplicates.
        under_cache = {}
        for fid, path, d, name, cat, sub, size, mtime, date, src, extra, h in self.files:
            if d not in under_cache:
                under_cache[d] = self._under(d, cleanup_dirs)
            if under_cache[d]:
                continue
            in_unit = self.unit_of_dir(d) is not None
            c = self.containers.get(path)
            if cat == "system_junk":
                props.append(self._prop("system_junk", path, False, 1, size, mtime, "system helper file"))
            elif (c and c["suggestion"] == "already_extracted" and c["confidence"].startswith("confirmed")
                  and not in_unit):
                props.append(self._prop("extracted_archive", path, False, 1, size, mtime,
                                        "%.0f%% of its %d files already on disk, %s" % (
                                            100 * c["found_share"], c["files"], c["confidence"]),
                                        c["extracted_at"]))
            elif fid in self.dup_of and not in_unit:
                props.append(self._prop("duplicate", path, False, 1, size, mtime, "identical copy",
                                        self.dup_of[fid]))
            elif not in_unit and fid in self.known_ids and (size or 0) >= cfg["software_min_bytes"]:
                st, kset, kname = self.known_status[fid]
                props.append(self._prop("known_software", path, False, 1, size, mtime,
                                        "file known to %s%s" % (set_name(kset), " as %s" % kname if kname and
                                                                kname.lower() != name.lower() else "")))
            elif (not in_unit and self.is_sw(path, cat, name) and (size or 0) >= 1024 * 1024
                  and mtime and mtime < self.cutoff):
                props.append(self._prop("old_installer", path, False, 1, size, mtime,
                                        "installer or disk image from %s" % time.strftime(
                                            "%Y", time.localtime(mtime))))
            else:
                continue
            cleanup_files.add(fid)

        self.cleanup = props
        self.cleanup_dirs = cleanup_dirs
        self.cleanup_files = cleanup_files

    def _prop(self, ptype, path, is_dir, n, size, mtime, why, ref=""):
        return {"type": ptype, "path": path, "is_dir": is_dir, "files": n or 0, "bytes": size or 0,
                "last_modified": time.strftime("%Y-%m-%d", time.localtime(mtime)) if mtime else "",
                "reason": why, "kept_copy": ref}

    # -------------------------------------------------------------- labelling
    def folder_label(self, p):
        a = self.agg[p]
        unit = self.unit_of_dir(p)
        if unit:
            return UNIT_LABELS.get(unit[1], unit[1])
        n, total, cnt, sz = a["n"], a["size"], a["cnt"], a["sz"]
        if n == 0:
            return "Empty"

        def share_n(cats):
            return sum(cnt.get(c, 0) for c in cats) / n

        def share_s(cats):
            return (sum(sz.get(c, 0) for c in cats) / total) if total else 0

        if total and a["sw"] / total >= self.cfg["software_share"]:
            return "Software"
        if sz.get("video_long") and share_s({"video_long"}) >= 0.6:
            return "Movies / long videos"
        if share_n(MEDIA_CATS - {"video_long"}) >= 0.7:
            return "Photos and videos"
        if share_n({"audio"}) >= 0.6:
            return "Music / audio"
        if share_n(DOC_CATS) >= 0.6:
            return "Documents"
        if share_n({"code"}) >= 0.6:
            return "Code"
        if share_n({"email"}) >= 0.5:
            return "Email"
        if share_s({"compressed"}) >= 0.6:
            return "Compressed files"
        if share_n({"system_junk"}) >= 0.9:
            return "System junk"
        return "Mixed"

    def cleanup_summary(self):
        out = defaultdict(lambda: [0, 0, 0])
        for p in self.cleanup:
            out[p["type"]][0] += 1
            out[p["type"]][1] += p["files"]
            out[p["type"]][2] += p["bytes"]
        return out

    def rel(self, p):
        return os.path.relpath(p, self.root)

    def describe(self):
        return "%d files, %s" % (self.total_n, human(self.total_b))
