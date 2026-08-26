#!/usr/bin/env python3
"""Track progress against IMPLEMENTATION_PLAN.md.

    python3 progress.py            # report
    python3 progress.py --update   # also rewrite the §0.1 rollup table in place
    python3 progress.py --next 8   # show the next N startable tasks

Status markers in the plan:  [ ] todo  [~] in progress  [x] done  [!] blocked  [-] dropped
"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).parent
PLAN = next((c for c in (ROOT / "docs/IMPLEMENTATION_PLAN.md", ROOT / "IMPLEMENTATION_PLAN.md") if c.exists()),
            ROOT / "docs/IMPLEMENTATION_PLAN.md")
ROW = re.compile(r'^\| ((M[A-Z0-9]+)-\d+[a-e]?) \|(.+?)\| ([0-9.]+) (h|m) \| `\[(.)\]` \|', re.M)

#: Build order. MC (the contour-map API phase) sits between M1 and M2, so it
#: needs an explicit rank rather than being parsed out of the phase name.
PHASE_RANK = {"M0": 0, "M1": 1, "MC": 1.5, "M2": 2, "M3": 3, "M4": 4, "M5": 5,
              "M6": 6, "M7": 7, "M8": 8, "M9": 9, "M10": 10, "M11": 11}


def rank(phase: str) -> float:
    return PHASE_RANK.get(phase, 99.0)


def load():
    text = PLAN.read_text(encoding="utf-8")
    tasks = []
    for m in ROW.finditer(text):
        tid, ph, desc, val, unit, mark = m.groups()
        tasks.append({
            "id": tid, "phase": ph, "n": rank(ph),
            "desc": re.sub(r'[`*★↳]', '', desc).strip(),
            "h": float(val) / 60 if unit == "m" else float(val),
            "mark": mark,
        })
    return text, tasks


def phase_stats(tasks):
    out = {}
    for t in tasks:
        p = out.setdefault(t["phase"], {"n": t["n"], "done": 0, "wip": 0, "blocked": 0,
                                        "dropped": 0, "todo": 0, "h_done": 0.0, "h_all": 0.0})
        p["h_all"] += t["h"]
        if t["mark"] == "x":
            p["done"] += 1; p["h_done"] += t["h"]
        elif t["mark"] == "~": p["wip"] += 1
        elif t["mark"] == "!": p["blocked"] += 1
        elif t["mark"] == "-": p["dropped"] += 1
        else: p["todo"] += 1
    return dict(sorted(out.items(), key=lambda kv: kv[1]["n"]))


def pct(p):
    live = p["done"] + p["wip"] + p["blocked"] + p["todo"]
    return 0 if live == 0 else round(100 * p["done"] / live)


def report(tasks, nnext):
    st = phase_stats(tasks)
    print(f"{'phase':6} {'done':>9} {'hours':>13} {'%':>4}  status")
    print("-" * 62)
    for ph, p in st.items():
        live = p["done"] + p["wip"] + p["blocked"] + p["todo"]
        flag = ""
        if p["blocked"]: flag += f" {p['blocked']} BLOCKED"
        if p["wip"]:     flag += f" {p['wip']} in progress"
        if live and p["done"] == live: flag = " COMPLETE"
        print(f"{ph:6} {p['done']:>4}/{live:<4} {p['h_done']:>5.1f}/{p['h_all']:<6.1f} {pct(p):>3}%{flag}")

    r1 = [p for ph, p in st.items() if p["n"] <= 7]
    r2 = [p for ph, p in st.items() if p["n"] >= 8]
    for name, grp in (("Ring 1 (submittable)", r1), ("Ring 2 (complete)", r2)):
        hd, ha = sum(p["h_done"] for p in grp), sum(p["h_all"] for p in grp)
        td = sum(p["done"] for p in grp)
        tt = sum(p["done"] + p["wip"] + p["blocked"] + p["todo"] for p in grp)
        bar = int(24 * hd / ha) if ha else 0
        print(f"\n{name:22} [{'#'*bar}{'.'*(24-bar)}] {hd:.0f}/{ha:.0f} h  ({td}/{tt} tasks)")
    left = sum(p["h_all"] - p["h_done"] for p in r1)
    if left > 0:
        print(f"\nRing 1 remaining: {left:.0f} h  ->  " + "  ".join(
            f"{w} h/wk: ~{left/w:.0f} wk" for w in (8, 15, 25, 40)))

    blocked = [t for t in tasks if t["mark"] == "!"]
    if blocked:
        print("\nBLOCKED — resolve before anything else:")
        for t in blocked:
            print(f"  {t['id']:8} {t['desc'][:66]}")

    wip = [t for t in tasks if t["mark"] == "~"]
    if wip:
        print("\nIn progress:")
        for t in wip:
            print(f"  {t['id']:8} {t['desc'][:66]}")
    if len(wip) > 2:
        print("  ^ more than two tasks open at once — finish one before starting another")

    # next startable: earliest phase with unfinished work; phases run in order
    for ph, p in phase_stats(tasks).items():
        if p["done"] + p["dropped"] < p["done"] + p["wip"] + p["blocked"] + p["todo"] + p["dropped"]:
            cand = [t for t in tasks if t["phase"] == ph and t["mark"] in " ~"]
            if cand:
                print(f"\nNEXT UP in {ph} (phases run in order — see plan §6):")
                for t in cand[:nnext]:
                    print(f"  [{t['mark']}] {t['id']:8} {t['h']:>4.1f} h  {t['desc'][:58]}")
                break
    return st


def update_rollup(text, st):
    def sub(m):
        ph = m.group(1)
        if ph not in st: return m.group(0)
        p = st[ph]
        return f"| **{ph}** |{m.group(2)}| {p['h_all']:g} h | {m.group(4)} | `[{'x' if pct(p)==100 else ' '}]` | {pct(p)} |"
    new = re.sub(r'\| \*\*(M[A-Z0-9]+)\*\* \|(.+?)\| ([0-9.]+) h \| (\d) \| `\[.\]` \| \d+ \|', sub, text)
    if new != text:
        PLAN.write_text(new, encoding="utf-8")
        print("\n§0.1 rollup updated in place.")
    else:
        print("\n§0.1 rollup already current.")


if __name__ == "__main__":
    nnext = 6
    if "--next" in sys.argv:
        nnext = int(sys.argv[sys.argv.index("--next") + 1])
    text, tasks = load()
    if not tasks:
        sys.exit("No task rows parsed — has the plan's table format changed?")
    st = report(tasks, nnext)
    if "--update" in sys.argv:
        update_rollup(text, st)
