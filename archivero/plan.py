"""Phase 4: reorganization proposal (editable CSV) and phase 5: apply it by copying."""

import csv
import json
import os
import shutil
from collections import defaultdict

from .categories import DOC_CATS
from .util import Progress, human, safe_name

CAT_DEST = {
    "image": "Photos_and_videos", "raw": "Photos_and_videos", "video_short": "Photos_and_videos",
    "video_long": "Long_videos", "audio": "Music",
    "pdf": "Documents", "document": "Documents", "spreadsheet": "Documents", "presentation": "Documents",
    "code": "Loose_code", "compressed": "Compressed",
    "disk_image": "Software", "executable": "Software",
    "email": "Email/Loose", "database": "Databases", "design": "Design",
    "certificate": "Certificates_and_keys", "config": "Other", "no_extension": "Other", "other": "Other",
}
UNIT_DEST = {"code_project": "Projects", "thunderbird_profile": "Email/Thunderbird",
             "browser_profile": "Other/Browser_profiles"}


def make_plan(an, dest, out):
    dest = os.path.abspath(dest)
    used = set()

    def unique(p):
        base, ext = os.path.splitext(p)
        cand, i = p, 2
        while cand.lower() in used:
            cand = "%s (%d)%s" % (base, i, ext)
            i += 1
        used.add(cand.lower())
        return cand

    stats = defaultdict(lambda: [0, 0])
    cleanup_type = {p["path"]: p["type"] for p in an.cleanup}

    def count(action, size):
        stats[action][0] += 1
        stats[action][1] += size or 0

    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["action", "source", "destination", "category", "date", "date_source", "bytes", "note"])

        # Units (projects, profiles) and software bundles are copied as a whole.
        for u in an.top_units():
            if an._under(u, an.cleanup_dirs):
                continue
            kind = an.units[u]
            size = an.agg.get(u, {}).get("size", 0)
            name = safe_name(os.path.basename(u))
            if kind == "thunderbird_profile":
                name = "%s__%s" % (safe_name(os.path.basename(os.path.dirname(u))), name)
            w.writerow(["copy_dir", u, unique(os.path.join(dest, UNIT_DEST[kind], name)), kind,
                        "", "", size, ""])
            count("copy_dir", size)
        for path, kind, mode, note, regen, nfiles, nbytes, newest, oldest in an.pruned:
            if kind != "software_bundle" or an._under(path, an.cleanup_dirs):
                continue
            if an.unit_of_dir(os.path.dirname(path)):
                continue
            w.writerow(["copy_dir", path, unique(os.path.join(dest, "Software", "Bundles",
                                                              safe_name(os.path.basename(path)))),
                        kind, "", "", nbytes or 0, ""])
            count("copy_dir", nbytes)

        under_cache = {}
        for fid, path, d, name, cat, sub, size, mtime, date, src, extra, h in sorted(an.files, key=lambda r: r[1]):
            if an.unit_of_dir(d):
                continue  # travels with its unit
            if d not in under_cache:
                under_cache[d] = an._under(d, an.cleanup_dirs)
            if fid in an.cleanup_files or under_cache[d]:
                ptype = cleanup_type.get(path, "inside a folder proposed for cleanup")
                note = an.dup_of.get(fid, "") if ptype == "duplicate" else ptype
                w.writerow(["skip_cleanup", path, "", cat, date or "", src or "", size, note])
                count("skip_cleanup", size)
                continue
            zone = an.file_zone.get(fid)
            if zone == "user_appdata":
                source = an.file_source.get(fid)
                base = an.source_name(source) if source else d
                tag = safe_name(os.path.basename(base) or "_root")
                target = os.path.join(dest, "App_data", tag, os.path.relpath(path, base))
                w.writerow(["copy", path, unique(target), cat, date or "", src or "", size, "application data"])
                count("copy", size)
                continue
            year = date[:4] if date else "no_date"
            month = date[:7] if date else "no_date"
            parent = safe_name(os.path.basename(d)) if d != an.root else "_root"
            top = CAT_DEST.get(cat, "Other")
            fname = safe_name(name)
            if cat in ("image", "raw", "video_short"):
                target = os.path.join(dest, top, year, month, fname)
            elif cat in DOC_CATS:
                target = os.path.join(dest, top, year, parent, fname)
            elif cat == "video_long":
                target = os.path.join(dest, top, fname)
            elif cat == "audio":
                t = json.loads(extra) if extra else {}
                if t.get("artist") or t.get("album"):
                    target = os.path.join(dest, top, safe_name(t.get("artist", "Unknown_artist")),
                                          safe_name(t.get("album", "Unknown_album")), fname)
                else:
                    target = os.path.join(dest, top, parent, fname)
            else:
                target = os.path.join(dest, top, parent, fname)
            w.writerow(["copy", path, unique(target), cat, date or "", src or "", size, ""])
            count("copy", size)

    print("Plan written to %s" % out)
    for k, (n, s) in sorted(stats.items()):
        print("  %-14s %9d  %s" % (k, n, human(s)))
    print("You can edit the 'action' and 'destination' columns before running 'apply'.")


def _copy_file(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        shutil.copy2(src, dst)
    except OSError:
        shutil.copyfile(src, dst)
        st = os.stat(src)
        os.utime(dst, (st.st_atime, st.st_mtime))


def apply_plan(plan, execute=False):
    with open(plan, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["action"] in ("copy", "copy_dir")]
    total = sum(int(r["bytes"] or 0) for r in rows)
    print("%d copy actions, %s in total." % (len(rows), human(total)))
    if not execute:
        for r in rows[:25]:
            print("  %s\n    -> %s" % (r["source"], r["destination"]))
        if len(rows) > 25:
            print("  … (%d more)" % (len(rows) - 25))
        print("Dry run. Add --execute to actually copy (nothing is ever moved or deleted).")
        return
    prog = Progress()
    done = errors = 0
    ignore = shutil.ignore_patterns("node_modules", "__pycache__", ".DS_Store", "._*")
    with open(plan + ".errors.log", "a", encoding="utf-8") as log:
        for i, r in enumerate(rows, 1):
            src, dst, size = r["source"], r["destination"], int(r["bytes"] or 0)
            try:
                if r["action"] == "copy_dir":
                    shutil.copytree(src, dst, symlinks=True, ignore=ignore, dirs_exist_ok=True)
                elif not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src)):
                    _copy_file(src, dst)
            except (OSError, shutil.Error) as err:
                errors += 1
                log.write("%s\t%s\t%s\n" % (src, dst, err))
            done += size
            prog.show("%d/%d · %s/%s · %.1f MB/s · errors: %d" % (
                i, len(rows), human(done), human(total), done / 1048576 / max(prog.elapsed(), 0.001), errors))
    prog.done("Copy finished: %d actions, %d errors (see %s.errors.log)." % (len(rows), errors, plan))
