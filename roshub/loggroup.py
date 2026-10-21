#!/usr/bin/env python3
"""ROSHub log grouping: lines that differ only in timestamps, PIDs, addresses or
numbers share one fingerprint, so repeats collapse into one entry with a count
and first/last-seen times. Shared by ui_server.py and diag_bundle.py. Stdlib only."""
import os
import re
import time
import calendar

# --- what gets stripped before comparing two lines --------------------------
_ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_EPOCH_BR = re.compile(r"\[\s*\d{9,}(?:\.\d+)?\s*\]")            # [1792368938.553462213]
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\b")         # 01:42:10.123
_HEX = re.compile(r"\b0x[0-9a-fA-F]+\b")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_LONGHEX = re.compile(r"\b[0-9a-fA-F]{16,}\b")                    # zenoh ZIDs, hashes
_PROC_SUFFIX = re.compile(r"(\[[A-Za-z_][\w./]*?)-\d+(\])")        # [component_container-1]
_PID = re.compile(r"\b(pid|PID)[\s:=]*\d+")
# a number not glued to a letter/underscore: "robot1" keeps its 1, "x: -6.53" -> "x: #"
_NUM = re.compile(r"(?<![A-Za-z_\d.])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![A-Za-z_\d])")
_WS = re.compile(r"\s+")

_EPOCH_CAPTURE = re.compile(r"\[\s*(\d{10})(?:\.(\d+))?\s*\]")
_ISO_CAPTURE = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:[.,]\d+)?(Z)?")
_LEVEL_TAG = re.compile(r"\[(DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\]")
_NODE_TAG = re.compile(r"\[(DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\]\s*\[[^\]]*\]\s*\[([^\]]+)\]")
_PROC_TAG = re.compile(r"^\[([A-Za-z_][\w./]*?)(?:-\d+)?\]")

_ERR_WORDS = ("traceback", "exception", "exited with failure", "process has died",
              "segmentation fault", "core dumped", "invalid frame id", "could not", "failed")
_WARN_WORDS = ("warn", "timed out", "timeout", "deprecat", "dropping message", "lookup would require extrapolation")
LEVEL_ORDER = {"FATAL": 0, "ERROR": 1, "WARN": 2, "INFO": 3, "DEBUG": 4, "PLAIN": 5}


def fingerprint(line):
    s = line.strip()
    s = _ISO_TS.sub("<ts>", s)
    s = _EPOCH_BR.sub("[<ts>]", s)
    s = _CLOCK.sub("<t>", s)
    s = _UUID.sub("<id>", s)
    s = _HEX.sub("<hex>", s)
    s = _LONGHEX.sub("<id>", s)
    s = _PROC_SUFFIX.sub(r"\1\2", s)
    s = _PID.sub(r"\1 #", s)
    s = _NUM.sub("#", s)
    return _WS.sub(" ", s).strip()


def parse_level(line):
    """Explicit ROS level tag wins; keyword heuristics only when there is none."""
    m = _LEVEL_TAG.search(line)
    if m:
        lv = m.group(1)
        return "WARN" if lv == "WARNING" else lv
    low = line.lower()
    if any(w in low for w in _ERR_WORDS) or re.search(r"\berror\b", low):
        return "ERROR"
    if any(w in low for w in _WARN_WORDS):
        return "WARN"
    return "PLAIN"


def parse_time(line):
    """Wall time (epoch float) from a ROS [sec.nsec] stamp or an ISO stamp, else None."""
    m = _EPOCH_CAPTURE.search(line)
    if m:
        try:
            return float(m.group(1) + "." + (m.group(2) or "0"))
        except ValueError:
            pass
    m = _ISO_CAPTURE.search(line)
    if m:
        try:
            st = time.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M:%S")
            return calendar.timegm(st) if m.group(3) else time.mktime(st)
        except ValueError:
            pass
    return None


def parse_source(line, default):
    """'launch_proc -> ros_node' when both are visible, else whatever is."""
    proc = None
    pm = _PROC_TAG.match(line.strip())
    if pm and pm.group(1).upper() not in LEVEL_ORDER:
        proc = pm.group(1)
    nm = _NODE_TAG.search(line)
    node = nm.group(2) if nm else None
    parts = [p for p in (default, proc, node) if p]
    out = []
    for p in parts:
        if p not in out:
            out.append(p)
    return " -> ".join(out) if out else default


def fmt_time(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "?"


def group_lines(lines, source="", file="", min_level="WARN", default_ts=None, skip=None):
    """Group an iterable of lines. Returns {key: entry}. min_level=None keeps everything."""
    groups = {}
    limit = LEVEL_ORDER.get(min_level, 5) if min_level else 99
    last_ts = default_ts
    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip() or (skip is not None and skip.search(line)):
            continue
        ts = parse_time(line)
        if ts:
            last_ts = ts
        level = parse_level(line)
        if LEVEL_ORDER.get(level, 5) > limit:
            continue
        src = parse_source(line, source)
        fp = fingerprint(line)
        key = (level, src, fp)
        g = groups.get(key)
        if g is None:
            groups[key] = {"level": level, "source": src, "fingerprint": fp, "sample": line.strip(),
                           "count": 1, "first": ts or last_ts, "last": ts or last_ts,
                           "files": {file} if file else set()}
        else:
            g["count"] += 1
            g["sample"] = line.strip()
            t = ts or last_ts
            if t:
                g["first"] = min(g["first"] or t, t)
                g["last"] = max(g["last"] or t, t)
            if file:
                g["files"].add(file)
    return groups


def merge_groups(into, more):
    for k, g in more.items():
        cur = into.get(k)
        if cur is None:
            into[k] = g
            continue
        cur["count"] += g["count"]
        cur["sample"] = g["sample"]
        firsts = [t for t in (cur["first"], g["first"]) if t]
        lasts = [t for t in (cur["last"], g["last"]) if t]
        cur["first"] = min(firsts) if firsts else None
        cur["last"] = max(lasts) if lasts else None
        cur["files"] |= g["files"]
    return into


def group_files(paths, min_level="WARN", source_of=None, skip=None):
    """Group several log files together. source_of(path) -> label for that file."""
    out = {}
    for p in paths:
        try:
            with open(p, "r", errors="ignore") as f:
                lines = f.readlines()
        except Exception:
            continue
        label = source_of(p) if source_of else os.path.basename(p)
        merge_groups(out, group_lines(lines, label, os.path.basename(p), min_level,
                                      default_ts=os.path.getmtime(p), skip=skip))
    return out


def sorted_entries(groups):
    items = list(groups.values())
    items.sort(key=lambda g: (LEVEL_ORDER.get(g["level"], 5), -g["count"], g["source"]))
    for g in items:
        g["files"] = sorted(g["files"]) if isinstance(g["files"], set) else g["files"]
        g["first_str"] = fmt_time(g["first"])
        g["last_str"] = fmt_time(g["last"])
    return items


def merge_variants(entries, min_members=3):
    """Fold sorted entries that differ in exactly ONE word (e.g. the frame name in
    'TF_OLD_DATA ... for frame <X> ...') into a single entry listing the variants."""
    toks = [e["fingerprint"].split(" ") for e in entries]
    keys = {}
    for i, (e, t) in enumerate(zip(entries, toks)):
        for p in range(len(t)):
            keys.setdefault((e["level"], e["source"], len(t), p, tuple(t[:p] + ["*"] + t[p + 1:])), []).append(i)
    used, out = set(), []
    best_for = {}
    for k, members in keys.items():
        if len(members) >= min_members:
            for i in members:
                if i not in best_for or len(members) > len(keys[best_for[i]]):
                    best_for[i] = k
    for i, e in enumerate(entries):
        if i in used:
            continue
        k = best_for.get(i)
        if not k:
            out.append(e)
            continue
        members = [j for j in keys[k] if j not in used and best_for.get(j) == k]
        if len(members) < min_members:
            out.append(e)
            continue
        p = k[3]
        grp = [entries[j] for j in members]
        used.update(members)
        m = dict(grp[0])
        m["count"] = sum(g["count"] for g in grp)
        firsts = [g["first"] for g in grp if g.get("first")]
        lasts = [g["last"] for g in grp if g.get("last")]
        m["first"], m["last"] = (min(firsts) if firsts else None), (max(lasts) if lasts else None)
        m["first_str"], m["last_str"] = fmt_time(m["first"]), fmt_time(m["last"])
        m["variants"] = [toks[j][p] for j in members]
        out.append(m)
    out.sort(key=lambda g: (LEVEL_ORDER.get(g["level"], 5), -g["count"], g["source"]))
    return out
