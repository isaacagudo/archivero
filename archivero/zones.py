"""Zones and provenance.

Rebuilds the folder tree from file paths, recognizes system drives and user profiles
from the names they contain (partial copies included), gives every folder a zone
(user_data, user_appdata, cache, system, applications, trash) and groups everything
into *sources*: the machines or profiles a copied tree came from, described from
clues such as layout era, vendor tools, drivers and file dates.

The same engine works on disk paths and on the members of an archive or disk image.
"""

import fnmatch
import os
import re
import time
from collections import Counter, defaultdict

from .detectors.base import PRUNED

from .layouts import (APP_PROFILE_PATHS, HARDWARE_CLUES, HOME_SCOPES, LAPTOP_CLUES, MARKER_FILES, MODEL_CLUES,
                      PROFILE_CONTAINERS, ROOT_SCOPES, SCOPES, SOFTWARE_ERA_CLUES, VALUABLE_APPDATA,
                      VENDOR_CLUES, VERSION_FILES, ZONE_RULES)

# ----------------------------------------------------------------------------- rules


def _compile_rules():
    exact, globbed = defaultdict(list), []
    for spec, zone in ZONE_RULES:
        scope, _, path = spec.partition(":")
        segs = tuple(s for s in path.lower().split("/") if s)
        rule = (scope, segs, zone)
        if any(ch in segs[0] for ch in "*?["):
            globbed.append(rule)
        else:
            exact[(scope, segs[0])].append(rule)
    leaves = {spec.partition(":")[2].lower().split("/")[-1] for spec, _ in ZONE_RULES}
    return exact, globbed, {x for x in leaves if "." in x}


_EXACT, _GLOBBED, _LEAF_FILES = _compile_rules()
_VERSION_RANK = {rel: i for i, (rel, _) in enumerate(VERSION_FILES)}


def _seg_match(pat, seg):
    return pat == seg or (any(ch in pat for ch in "*?[") and fnmatch.fnmatchcase(seg, pat))


def _match_at(pattern, segs, i):
    if i + len(pattern) > len(segs):
        return False
    return all(_seg_match(p, segs[i + k]) for k, p in enumerate(pattern))


_CLUE_RE = re.compile("|".join([r for _, r, _ in VENDOR_CLUES] + [r for _, r in MODEL_CLUES] + [LAPTOP_CLUES]
                               + [r for _, r in HARDWARE_CLUES] + [r for r, _ in SOFTWARE_ERA_CLUES]), re.I)
_VENDOR = [(v, re.compile(r, re.I), w) for v, r, w in VENDOR_CLUES]
_MODEL = [(v, re.compile(r, re.I)) for v, r in MODEL_CLUES]
_LAPTOP = re.compile(LAPTOP_CLUES, re.I)
_HW = [(h, re.compile(r, re.I)) for h, r in HARDWARE_CLUES]
_ERA = [(re.compile(r, re.I), h) for r, h in SOFTWARE_ERA_CLUES]

_MODEL_CASE = {"thinkpad": "ThinkPad", "macbook": "MacBook", "imac": "iMac", "ideapad": "IdeaPad",
               "elitebook": "EliteBook", "probook": "ProBook", "zenbook": "ZenBook", "vivobook": "VivoBook",
               "vaio": "VAIO", "xps": "XPS", "eee": "Eee", "pc": "PC"}


def _pretty_model(text):
    return " ".join(_MODEL_CASE.get(w, w.upper() if any(c.isdigit() for c in w) else w.capitalize())
                    for w in text.split())


# ------------------------------------------------------------------------------ tree


class Place:
    """A folder recognized as a system drive or a user profile."""

    def __init__(self, segs, scope, eras, arch, markers, strength):
        self.segs = segs
        self.scope = scope
        self.os = SCOPES[scope]["os"]
        self.label = SCOPES[scope]["label"]
        self.eras = eras
        self.arch = arch
        self.markers = markers
        self.strength = strength  # score / threshold (1 = just enough evidence)


class Source:
    """A machine (or a lone profile) that part of the tree came from."""

    def __init__(self, segs, implied=False):
        self.segs = segs
        self.implied = implied     # root inferred from a profile container, not seen itself
        self.places = []
        self.files = 0
        self.bytes = 0
        self.personal_years = Counter()
        self.mtime_days = Counter()
        self.vendor = Counter()
        self.vendor_ev = defaultdict(set)
        self.models = Counter()
        self.laptop = set()
        self.hardware = set()
        self.software = set()
        self.apps = []             # (kind, path segs, hint)
        self.os_version = None     # from a version file inside the drive (osversion.py)
        self._seen_names = set()

    # -- evidence -----------------------------------------------------------------
    def observe_name(self, name):
        low = name.lower()
        if low in self._seen_names or not _CLUE_RE.search(low):
            return
        self._seen_names.add(low)
        for v, rx, w in _VENDOR:
            if rx.search(low):
                self.vendor[v] += w
                self.vendor_ev[v].add(name)
        for v, rx in _MODEL:
            m = rx.search(low)
            if m:
                self.models[(v, _pretty_model(m.group(0)))] += 1
        if _LAPTOP.search(low):
            self.laptop.add(name)
        for h, rx in _HW:
            if rx.search(low):
                self.hardware.add(h)
        for rx, hint in _ERA:
            if rx.search(low):
                self.software.add(hint)

    def observe_file(self, size, mtime, year, personal):
        self.files += 1
        self.bytes += size or 0
        if mtime:
            self.mtime_days[int(mtime // 86400)] += 1
        if personal and year:
            self.personal_years[year] += 1

    # -- description --------------------------------------------------------------
    @property
    def os(self):
        c = Counter(p.os for p in self.places)
        return c.most_common(1)[0][0] if c else "unknown"

    def era(self):
        eras = Counter()
        for p in self.places:
            eras.update(p.eras)
        if not eras:
            return ""
        top = eras.most_common()
        era = top[0][0]
        if self.os == "Windows":
            years = self.years()
            peak = years[2] if years else None
            if era == "Vista or later" and peak:
                era = ("probably Vista or 7" if peak <= 2009 else "probably 7" if peak <= 2012
                       else "probably 7 or 8" if peak <= 2015 else "probably 10 or later")
            elif era == "2000/XP" and peak:
                era = "probably 2000" if peak <= 2001 else "probably XP"
            if len(top) > 1 and {"2000/XP", "Vista or later"} <= set(eras):
                era += ", with older XP-era folders (upgraded or migrated)"
        return era

    def years(self):
        """(first, last, peak) years of personal files, ignoring outliers."""
        if not self.personal_years:
            return None
        ys = sorted(self.personal_years.elements())
        lo = ys[int(len(ys) * 0.05)]
        hi = ys[min(len(ys) - 1, int(len(ys) * 0.95))]
        peak = self.personal_years.most_common(1)[0][0]
        return lo, hi, peak

    def copy_dated(self):
        """True when most files share one modification day: dates were reset by a copy."""
        if self.files < 20 or not self.mtime_days:
            return False
        return self.mtime_days.most_common(1)[0][1] / self.files >= 0.8

    def vendor_guess(self):
        if self.os == "macOS":
            return "Apple"
        if not self.vendor:
            return None
        v, w = self.vendor.most_common(1)[0]
        return v if w >= 2 and "(printer)" not in v else None

    def model_guess(self, vendor):
        ms = [(m, n) for (v, m), n in self.models.items()
              if not vendor or v.split("/")[0].split(" ")[0] in vendor]
        # Prefer the most specific (longest) model name, then the most frequent
        return max(ms, key=lambda x: (len(x[0]), x[1]))[0] if ms else None

    def kind(self):
        scopes = {p.scope for p in self.places}
        roots = scopes & ROOT_SCOPES
        if roots:
            return SCOPES[sorted(roots)[0]]["label"]
        if self.implied:
            return "%s drive (partial copy: only user profiles)" % self.os
        homes = scopes & HOME_SCOPES
        return SCOPES[sorted(homes)[0]]["label"] if homes else "Unknown"

    def profiles(self):
        return [p.segs[-1] for p in self.places if p.scope in HOME_SCOPES]

    def set_version(self, rec):
        """Keep the version file with the highest priority (lowest 'rank')."""
        if self.os_version is None or rec.get("rank", 99) < self.os_version.get("rank", 99):
            self.os_version = rec

    def describe(self):
        parts = [self.kind()]
        era = self.era()
        if self.os_version:
            v = self.os_version
            build = v.get("build")
            parts[0] += " (%s%s)" % (v["name"], ", build %s" % build if build and build not in v["name"] else "")
        else:
            if era.startswith("probably "):
                os_txt = "probably %s %s" % (self.os, era[len("probably "):])
            else:
                os_txt = self.os + (" " + era if era else "")
            if self.os != "unknown" and (os_txt != self.os or self.os not in parts[0]):
                parts[0] += " (%s)" % os_txt
        vendor = self.vendor_guess()
        model = self.model_guess(vendor)
        device = "laptop" if self.laptop else "computer"
        if vendor:
            article = "an" if vendor[0] in "AEIOU" else "a"
            parts.append("probably %s %s %s%s" % (article, vendor, device, " (%s)" % model if model else ""))
        elif self.laptop and self.os != "unknown":
            parts.append("probably a laptop")
        y = self.years()
        if y:
            lo, hi, peak = y
            span = str(lo) if lo == hi else "%d–%d" % (lo, hi)
            parts.append("personal files from %s (most in %d)" % (span, peak) if lo != hi else
                         "personal files from %s" % span)
        text = ", ".join(parts)
        if self.copy_dated():
            text += ". Note: most dates look like the date of a copy, not of the content"
        return text

    def evidence(self):
        ev = []
        if self.os_version:
            ev.append("version from %s: %s" % (self.os_version["file"], self.os_version["name"]))
        for p in self.places:
            ev.append("%s at '%s' (%s)" % (p.label, "/".join(p.segs) or ".", ", ".join(p.markers[:6])))
        if self.vendor:
            for v, _ in self.vendor.most_common(2):
                ev.append("%s: %s" % (v, ", ".join(sorted(self.vendor_ev[v])[:4])))
        if self.laptop:
            ev.append("laptop hints: " + ", ".join(sorted(self.laptop)[:4]))
        if self.hardware:
            ev.append("hardware: " + ", ".join(sorted(self.hardware)))
        if self.software:
            ev.append("software: " + ", ".join(sorted(self.software)))
        return ev


class Layout:
    """Build with add_file()/add_dir(), then call finish()."""

    def __init__(self):
        self.children = defaultdict(set)
        self._seen = {()}
        self.places = {}
        self.sources = {}
        self._zone_cache = {}
        self._src_cache = {}

    def add_dir(self, segs):
        segs = tuple(segs)
        while segs not in self._seen:
            self._seen.add(segs)
            self.children[segs[:-1]].add(segs[-1].lower())
            segs = segs[:-1]

    def add_file(self, segs):
        segs = tuple(segs)
        self.add_dir(segs[:-1])
        low = segs[-1].lower()
        if low in MARKER_FILES:
            self.children[segs[:-1]].add(low)

    def dirs(self):
        return self._seen

    # -- detection ----------------------------------------------------------------
    def finish(self):
        for d, names in self.children.items():
            best = None
            for scope, spec in SCOPES.items():
                hits = names & spec["markers"].keys()
                if not hits:
                    continue
                score = sum(spec["markers"][h] for h in hits)
                if score < spec["threshold"]:
                    continue
                req = spec.get("requires")
                if req and not names & req:
                    continue
                strength = score / spec["threshold"]
                if best is None or strength > best[1]:
                    best = (scope, strength, hits)
            if best:
                scope, strength, hits = best
                spec = SCOPES[scope]
                eras = Counter(spec.get("era", {})[h] for h in hits if h in spec.get("era", {}))
                arch = {spec["arch"][h] for h in hits if h in spec.get("arch", {})}
                self.places[d] = Place(d, scope, eras, arch, sorted(hits, key=lambda h: -spec["markers"][h]),
                                       strength)
        roots = {d for d, p in self.places.items() if p.scope in ROOT_SCOPES}
        for d, p in sorted(self.places.items(), key=lambda kv: len(kv[0])):
            implied = False
            if p.scope in ROOT_SCOPES:
                src = d
            else:
                src = next((d[:i] for i in range(len(d) - 1, -1, -1) if d[:i] in roots), None)
                if src is None and len(d) >= 2 and d[-2].lower() in PROFILE_CONTAINERS:
                    src, implied = d[:-2], True
                elif src is None:
                    src = d
            s = self.sources.get(src)
            if s is None:
                s = self.sources[src] = Source(src, implied)
            s.implied = s.implied and implied
            s.places.append(p)
        return self

    def add_version(self, dir_segs, rec):
        """Attach a parsed version file (osversion.py) to the drive it belongs to, if one was detected."""
        rel = rec.get("file", "")
        parent = tuple(rel.split("/")[:-1])
        dir_segs = tuple(dir_segs)
        if not rel or len(parent) > len(dir_segs):
            return None
        root = dir_segs[:len(dir_segs) - len(parent)]
        src = self.sources.get(root)
        if src is None or tuple(s.lower() for s in dir_segs[len(root):]) != parent:
            return None
        src.set_version(dict(rec, rank=_VERSION_RANK.get(rel, 99)))
        return src

    # -- queries ------------------------------------------------------------------
    def zone(self, segs):
        """Zone of a folder (tuple of segments). Returns (zone, rule) or ('unknown', None)."""
        segs = tuple(segs)
        if segs in self._zone_cache:
            return self._zone_cache[segs]
        low = tuple(s.lower() for s in segs)
        best = None  # (end, length, scoped, zone, rule)
        for i in range(len(low)):
            cands = list(_EXACT.get(("any", low[i]), ())) + [r for r in _GLOBBED if r[0] == "any"]
            anc = segs[:i]
            place = self.places.get(anc)
            if place:
                cands += _EXACT.get((place.scope, low[i]), [])
                cands += [r for r in _GLOBBED if r[0] == place.scope]
            for scope, pat, zone in cands:
                if scope != "any" and (not place or place.scope != scope):
                    continue
                if _match_at(pat, low, i):
                    key = (i + len(pat), len(pat), scope != "any")
                    if best is None or key > best[:3]:
                        best = key + (zone, "%s:%s" % (scope, "/".join(pat)))
        res = (best[3], best[4]) if best else ("unknown", None)
        self._zone_cache[segs] = res
        return res

    def file_zone(self, dir_segs, name):
        low = name.lower()
        if low in _LEAF_FILES:
            z = self.zone(tuple(dir_segs) + (name,))
            if z[0] != "unknown":
                return z
        place = self.places.get(tuple(dir_segs))
        if place and place.scope in ("linux_home", "mac_home") and low.startswith("."):
            return ("user_appdata", "%s:.*" % place.scope)  # dotfiles in a home folder
        return self.zone(dir_segs)

    def source_of(self, segs):
        segs = tuple(segs)
        if segs in self._src_cache:
            return self._src_cache[segs]
        res = None
        for i in range(len(segs), -1, -1):
            if segs[:i] in self.sources:
                res = self.sources[segs[:i]]
                break
        self._src_cache[segs] = res
        return res


def disk_layout(con, root):
    """Layout of the files on disk in an inventory, plus the path -> segments function it uses."""
    def segs(p):
        rel = os.path.relpath(p, root)
        return () if rel == "." else tuple(rel.split(os.sep))

    lay = Layout()
    for (path,) in con.execute("SELECT path FROM files"):
        lay.add_file(segs(path))
    for (p,) in con.execute("SELECT path FROM detections WHERE mode=?", (PRUNED,)):
        lay.add_dir(segs(p))
    return lay.finish(), segs


def app_profile_hint(segs):
    """OS and era of a Firefox/Thunderbird profile from where it sits."""
    path = "/".join(s.lower() for s in segs)
    for frag, os_name, era in APP_PROFILE_PATHS:
        if frag in path:
            return os_name, era
    return None, None


def valuable_appdata(segs):
    path = "/".join(s.lower() for s in segs)
    for frag, label in VALUABLE_APPDATA:
        if frag in path:
            return label
    return None


def year_of(date):
    try:
        y = int(date[:4])
    except (TypeError, ValueError):
        return None
    return y if 1980 <= y <= time.localtime().tm_year + 1 else None
