"""
swaps one addition hitsound for another across a whole .osu diff

usage: open a terminal and cd to the dir with the python file (or just type the path to the dir)
python fixhs.py path/to/osu/the_.osu --from [bad-hitsample] --to [good-hitsample] 
example: python fixhs.py "C:/Users/peppyosu/AppData/Local/osu!/Songs/21581 Perfume ft peppy - Baby Cruising Love/Perfume ft. peppy - Baby Cruising Love (me) [super cool map]" --from soft-hitfinish67 --to drum-hitclap

(if your path contains any spaces (like in "Perfume ft peppy") then you have to put quotes around your path)
"""


import argparse
import re
import shutil
import sys
from bisect import bisect_right
from pathlib import Path

SAMPLE_RE = re.compile(r"^(normal|soft|drum)-(hitnormal|hitwhistle|hitfinish|hitclap|sliderslide|slidertick|sliderwhistle)(\d*)\.(wav|ogg|mp3)$", re.I)

SET_NAMES = {1: "normal", 2: "soft", 3: "drum"}
GENERAL_SETS = {name: num for num, name in SET_NAMES.items()}
DEFAULT_POINT = ["0", "-100", "4", "1", "0", "100", "0", "0"]
BITS = {"hitwhistle": 2, "hitfinish": 4, "hitclap": 8}
MAX_INDEX = 99  # osu! stops reading sample indices after this

def suffix(idx):
    return "" if idx <= 1 else str(idx)

def fmt_time(ms):
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}:{ms % 1000:03d}"

def is_red(point):
    return float(point[1]) > 0

def p_time(point):
    return int(round(float(point[0])))

def read_osu(path):
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    crlf = "\r\n" in text
    lines = [line.rstrip("\r") for line in text.split("\n")]
    return lines, crlf, bom

def write_osu(path, lines, crlf, bom):
    text = ("\r\n" if crlf else "\n").join(lines)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))

def section(lines, name):
    start = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == name:
            start = index + 1
        elif start is not None and stripped.startswith("[") and stripped.endswith("]"):
            return start, index
    if start is None:
        return 0, 0
    return start, len(lines)

def setting(lines, sec, key, default):
    start, end = section(lines, sec)
    for line in lines[start:end]:
        name, _, value = line.partition(":")
        if name.strip() == key:
            return value.strip()
    return default

def parse_name(name):
    name = name.strip().lower()
    name = re.sub(r"\.(wav|ogg|mp3)$", "", name)
    match = re.match(r"^(normal|soft|drum)-(hitnormal|hitwhistle|hitfinish|hitclap|sliderslide|slidertick|sliderwhistle)(\d*)$", name)
    if not match:
        sys.exit(f"'{name}' isn't a sample name i know (try something like soft-hitfinish or soft-hitfinish3)")
    return match.group(1), match.group(2), (max(int(match.group(3)), 1) if match.group(3) else None)

def read_points(lines):
    start, end = section(lines, "[TimingPoints]")
    rows = []
    for line in lines[start:end]:
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        fields = line.split(",")
        raw = line
        fields = fields + DEFAULT_POINT[len(fields):]
        rows.append((fields, raw))
    rows.sort(key=lambda row: p_time(row[0]))
    return [row[0] for row in rows], [row[1] for row in rows]

def timing_at(points, times, time_ms):
    index = max(bisect_right(times, time_ms) - 1, 0)
    green = None
    red = None
    for pos in range(index, -1, -1):
        if is_red(points[pos]):
            red = points[pos]
            break
        if green is None:
            green = points[pos]
    if red is None:
        red = next(point for point in points if is_red(point))
    return points[index], red, green

def make_green(time_ms, current, green, index):
    beat = green[1] if green is not None else "-100"
    return [str(time_ms), beat, current[2], current[3], str(index), current[5], "0", current[7]]

def hit_sample(parts):
    nums = parts[-1].split(":")[:3]
    if len(nums) == 3 and all(num.isdigit() for num in nums):
        return [int(num) for num in nums]
    return [0, 0, 0]

def find_swaps(lines, points, times, general_set, slider_mult, from_pre, from_bit, to_bit, has_from):
    start, end = section(lines, "[HitObjects]")
    moments = {}
    changes = {}
    conflicts = []

    for line_idx in range(start, end):
        text = lines[line_idx].strip()
        if not text:
            continue
        parts = text.split(",")
        time_ms = int(float(parts[2]))
        kind = int(parts[3])
        bits = int(parts[4])
        normal, addition, own_idx = hit_sample(parts)

        events = []
        has_edges = False
        edge_sounds = []
        if kind & 2:
            slides = int(parts[6])
            length = float(parts[7])
            current, red, green = timing_at(points, times, time_ms)
            # clamp first so a weird green line can't blow up the edge timing
            clamped_sv = 1.0 if green is None else min(max(-100 / float(green[1]), 0.1), 10)
            one_slide = length / (slider_mult * 100 * clamped_sv) * float(red[1])
            has_edges = len(parts) >= 10 and ":" not in parts[8]
            edge_sounds = parts[8].split("|") if has_edges else []
            edge_sets = parts[9].split("|") if has_edges else []

            edge_sounds = [sound if sound != "" else str(bits) for sound in edge_sounds]
            # a slider has one more edge than it has slides - the start counts too
            edge_sounds += [str(bits)] * (slides + 1 - len(edge_sounds))
            for edge_idx in range(slides + 1):
                edge_bits = int(edge_sounds[edge_idx])
                normal_set, add_set = normal, addition
                if edge_idx < len(edge_sets) and ":" in edge_sets[edge_idx]:
                    normal_set, add_set = [int(part) for part in edge_sets[edge_idx].split(":")[:2]]
                events.append((int(round(time_ms + one_slide * edge_idx)), edge_bits, normal_set, add_set, edge_idx))
        elif kind & 8:
            events.append((int(float(parts[5])), bits, normal, addition, None))
        else:
            events.append((time_ms, bits, normal, addition, None))

        new_parts = list(parts)
        touched = False
        remap_own = None
        for time_ms, edge_bits, normal_set, add_set, edge_idx in events:
            current = timing_at(points, times, time_ms)[0]
            sample_set = add_set or normal_set or int(current[3]) or general_set
            idx = max(own_idx or int(current[4]), 1)
            wants = bool(edge_bits & from_bit) and SET_NAMES.get(sample_set) == from_pre and idx in has_from

            if wants:
                touched = True
                if edge_bits & to_bit:
                    conflicts.append(time_ms)
                new_bits = (edge_bits & ~from_bit) | to_bit
                if has_edges and edge_idx is not None:
                    edge_sounds[edge_idx] = str(new_bits)
                else:
                    new_parts[4] = str(new_bits)

            if own_idx:
                if wants:
                    remap_own = idx
                continue
            if wants:
                moments[time_ms] = idx
            else:
                moments.setdefault(time_ms, None)

        if touched:
            if has_edges:
                new_parts[8] = "|".join(edge_sounds)
            changes[line_idx] = (new_parts, remap_own)

    return moments, changes, conflicts

def list_samples(folder):
    result = []
    for sample in folder.iterdir():
        match = SAMPLE_RE.match(sample.name)
        if match:
            digits = match.group(3)
            result.append((sample, match.group(1).lower(), match.group(2).lower(),
                           max(int(digits), 1) if digits else 1, match.group(4).lower()))
    return result

def indices_used(path):
    try:
        lines = read_osu(path)[0]
    except (OSError, UnicodeDecodeError):
        return set()
    used = set()
    start, end = section(lines, "[TimingPoints]")
    for line in lines[start:end]:
        fields = line.split(",")
        if len(fields) >= 5 and fields[4].strip().isdigit():
            used.add(max(int(fields[4]), 1))
    start, end = section(lines, "[HitObjects]")
    for line in lines[start:end]:
        last = line.strip().split(",")[-1].split(":")
        if len(last) >= 3 and last[2].isdigit() and int(last[2]) > 0:
            used.add(int(last[2]))
    return used

def plan_copies(folder, samples, mapping, pre, from_base, to_base):
    copies = []
    for old, new in mapping.items():
        for path, sample_pre, base, idx, ext in samples:
            if idx != old:
                continue
            if (sample_pre, base) == (pre, from_base):
                copies.append((path, folder / f"{pre}-{to_base}{suffix(new)}.{ext}",
                               f"<- was {pre}-{from_base}{suffix(old)}"))
            elif (sample_pre, base) == (pre, to_base):
                continue
            else:
                copies.append((path, folder / f"{sample_pre}-{base}{suffix(new)}.{ext}", ""))
    return copies

def main():
    parser = argparse.ArgumentParser(description="swaps hitsound samples")
    parser.add_argument("osu", help="the .osu")
    parser.add_argument("--from", dest="src", required=True, help="the sample to replace (soft-hitfinish, soft-hitwhistle, etc)")
    parser.add_argument("--to", dest="dst", required=True, help="sample to use (soft-hitfinish, soft-hitwhistle, etc)")
    args = parser.parse_args()

    osu_path = Path(args.osu)
    if not osu_path.exists():
        sys.exit(f"{osu_path} not found")
    folder = osu_path.parent

    from_pre, from_base, from_idx = parse_name(args.src)
    to_pre, to_base, to_idx = parse_name(args.dst)
    if from_base == to_base or from_base not in BITS or to_base not in BITS:
        sys.exit("only hitwhistle, hitfinish and hitclap can be swapped, and --from and --to have to be different")
    if from_pre != to_pre:
        sys.exit("--from and --to need the same sampleset (soft -> soft, not soft -> drum)")
    if to_idx:
        sys.exit("--to is just the hitsound, like soft-hitclap. the new sampleset number is picked for you")
    from_bit, to_bit = BITS[from_base], BITS[to_base]

    lines, crlf, bom = read_osu(osu_path)
    if setting(lines, "[General]", "Mode", "0") == "3":
        sys.exit("mania maps aren't supported")

    points, raw_points = read_points(lines)
    if not any(is_red(point) for point in points):
        sys.exit("no uninherited (red) timing point in this diff?")
    times = [p_time(point) for point in points]
    general_set = GENERAL_SETS.get(setting(lines, "[General]", "SampleSet", "Normal").lower(), 1)
    slider_mult = float(setting(lines, "[Difficulty]", "SliderMultiplier", "1.4"))

    samples = list_samples(folder)
    has_from = {sample[3] for sample in samples if (sample[1], sample[2]) == (from_pre, from_base)}
    if from_idx:
        if from_idx not in has_from:
            sys.exit(f"{from_pre}-{from_base}{suffix(from_idx)} isn't in the folder")
        has_from = {from_idx}

    moments, changes, conflicts = find_swaps(lines, points, times, general_set, slider_mult, from_pre, from_bit, to_bit, has_from)
    needed = sorted({value for value in moments.values() if value} | {remap for _, remap in changes.values() if remap})
    if not needed:
        sys.exit(f"nothing in this diff plays {from_pre}-{from_base} from a sampleset that has the file")

    taken = {sample[3] for sample in samples}
    for other_osu in folder.glob("*.osu"):
        taken |= indices_used(other_osu)
    mapping = {}
    next_idx = 2
    for old in needed:
        while next_idx in taken:
            next_idx += 1
        if next_idx > MAX_INDEX:
            sys.exit("ran out of sample indices (osu! stops at 99)")
        mapping[old] = next_idx
        taken.add(next_idx)

    inserts = []
    edits = {}
    log = []
    active = None
    next_row = 0

    for time_ms in sorted(moments):
        want = moments[time_ms]

        while next_row < len(points) and times[next_row] < time_ms:
            active = None
            next_row += 1
        at_time = []
        while next_row < len(points) and times[next_row] == time_ms:
            at_time.append(next_row)
            next_row += 1

        current, red, green = timing_at(points, times, time_ms)

        if want:
            new_index = mapping[want]
            if at_time:
                green_rows = [row_idx for row_idx in at_time if not is_red(points[row_idx])]
                if green_rows:
                    green_idx = green_rows[-1]
                    fields = list(points[green_idx])
                    fields[4] = str(new_index)
                    edits[green_idx] = fields
                    log.append((time_ms, f"existing green line -> index {new_index}"))
                else:
                    inserts.append((time_ms, make_green(time_ms, current, green, new_index)))
                    log.append((time_ms, f"new green line, index {new_index}"))
            elif active != new_index:
                inserts.append((time_ms, make_green(time_ms, current, green, new_index)))
                log.append((time_ms, f"new green line, index {new_index}"))
            active = new_index
        else:
            if not at_time and active is not None:
                inserts.append((time_ms, make_green(time_ms, current, green, current[4])))
                log.append((time_ms, f"green line back to index {max(int(current[4]), 1)}"))
            active = None

    new_lines = {}
    for line_idx, (parts, own_idx) in changes.items():
        if own_idx:
            last = parts[-1].split(":")
            last[2] = str(mapping[own_idx])
            parts[-1] = ":".join(last)
        new_lines[line_idx] = ",".join(parts)
        description = f"{from_base} -> {to_base}"
        if own_idx:
            description += f" (object has its own index, {own_idx} -> {mapping[own_idx]})"
        log.append((int(float(parts[2])), description))

    copies = plan_copies(folder, samples, mapping, from_pre, from_base, to_base)
    for dest in {copy[1] for copy in copies}:
        if dest.exists():
            sys.exit(f"{dest.name} already exists, not overwriting it")

    print(f"diff: {osu_path.name}")
    for old, new in mapping.items():
        print(f"sampleset index {old} -> {new} for the changed hits")
    for source, dest, note in copies:
        print(f"  copy {source.name} -> {dest.name} {note}".rstrip())
    print()
    for time_ms, description in sorted(log):
        print(f"  {fmt_time(time_ms)}  {description}")
    print(f"\n{len(inserts)} green lines added, {len(edits)} edited, {len(new_lines)} objects changed")
    if conflicts:
        print(f"warning: these already had {to_base} on as well, so their {from_base} is merged into it "
              f"and they'll use the new {to_base} sound (check them by hand):")
        print("  " + ", ".join(fmt_time(time_ms) for time_ms in sorted(set(conflicts))))

    rows = []
    for row_idx, raw in enumerate(raw_points):
        rows.append((times[row_idx], 0, row_idx, ",".join(edits[row_idx]) if row_idx in edits else raw))
    for insert_idx, (time_ms, fields) in enumerate(inserts):
        rows.append((time_ms, 1, insert_idx, ",".join(fields)))
    rows.sort(key=lambda row: row[:3])

    start, end = section(lines, "[TimingPoints]")
    blanks = 0
    while end - blanks - 1 >= start and lines[end - blanks - 1].strip() == "":
        blanks += 1
    out = lines[:start] + [row[3] for row in rows] + [""] * blanks + lines[end:]

    shift = len(out) - len(lines)
    for line_idx, text in new_lines.items():
        out[line_idx + shift] = text

    for source, dest, _ in copies:
        shutil.copy2(source, dest)

    # back up before writing, in case the write gets interrupted/corrupted somehow idk
    backup = osu_path.with_suffix(".osu.bak")
    counter = 1
    while backup.exists():
        backup = osu_path.with_suffix(f".osu.bak{counter}")
        counter += 1
    shutil.copy2(osu_path, backup)
    write_osu(osu_path, out, crlf, bom)
    print(f"{backup.name} saved")

if __name__ == "__main__":
    main()
