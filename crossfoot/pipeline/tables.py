from crossfoot.types import Word


def assign_column(word: Word, columns: list[tuple[str, float]]) -> str:
    """columns: [(name, x_start), ...] sorted ascending by x_start."""
    name = columns[0][0]
    for col_name, x_start in columns:
        if word.x0 >= x_start:
            name = col_name
        else:
            break
    return name


def _group_into_lines(words: list[Word]) -> list[list[Word]]:
    lines: dict[float, list[Word]] = {}
    for w in words:
        key = round(w.y0 * 2) / 2   # nearest 0.5pt
        lines.setdefault(key, []).append(w)
    return [lines[y] for y in sorted(lines)]


def band_rows(words: list[Word], anchor, y_tolerance: float) -> list[list[Word]]:
    """Groups words into row-bands anchored on the line(s) for which anchor(line) is True.

    A non-anchor line is absorbed into the nearest anchor band (above or
    below) if within y_tolerance; otherwise it is dropped as page furniture.
    """
    lines = _group_into_lines(words)
    anchor_indices = [i for i, line in enumerate(lines) if anchor(line)]
    if not anchor_indices:
        return []

    bands: list[list[Word]] = [list(lines[i]) for i in anchor_indices]
    anchor_ys = [lines[i][0].y0 for i in anchor_indices]

    for i, line in enumerate(lines):
        if i in anchor_indices:
            continue
        line_y = line[0].y0
        distances = [abs(line_y - ay) for ay in anchor_ys]
        best = min(range(len(distances)), key=lambda k: distances[k])
        if distances[best] <= y_tolerance:
            bands[best].extend(line)

    return bands
