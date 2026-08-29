#!/usr/bin/env python3
"""
Render captured ANSI terminal output as a terminal-window SVG.

Used to generate the README screenshots from REAL TUI sessions:

    printf '/workspace list\\n/quit\\n' | helios tui | \
        python3 scripts/ansi_to_svg.py --title "helios tui" -o shot.svg

Supports the SGR subset the Helios TUI emits: truecolor fg/bg (38;2 / 48;2),
bold (1), dim (2), reset (0).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, replace
from xml.sax.saxutils import escape

CW = 7.9          # monospace advance at 13px
LH = 19           # line height
FONT = ("SFMono-Regular, 'SF Mono', Menlo, Monaco, 'DejaVu Sans Mono', "
        "Consolas, monospace")
BG = "#06110A"
CHROME = "#0B1D12"
BORDER = "#134D2B"
DEFAULT_FG = "#D6E4DC"

SGR = re.compile(r"\x1b\[([0-9;]*)m")
# Strip non-SGR escapes (cursor moves etc. — final byte anything but 'm')
# and stray control chars (readline's \x01/\x02 prompt markers, \r).
OTHER_ESC = re.compile(r"\x1b\[[0-9;?]*[A-LN-Za-ln-z]|[\r\x00-\x08\x0b-\x1a\x1c-\x1f]")


@dataclass(frozen=True)
class Style:
    fg: str = DEFAULT_FG
    bg: str | None = None
    bold: bool = False
    dim: bool = False


def parse_line(line: str) -> list[tuple[str, Style]]:
    """Split one line into (text, style) runs."""
    line = OTHER_ESC.sub("", line)
    runs: list[tuple[str, Style]] = []
    style = Style()
    pos = 0
    for match in SGR.finditer(line):
        if match.start() > pos:
            runs.append((line[pos:match.start()], style))
        params = [int(p) for p in match.group(1).split(";") if p != ""] or [0]
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                style = Style()
            elif p == 1:
                style = replace(style, bold=True)
            elif p == 2:
                style = replace(style, dim=True)
            elif p == 38 and params[i:i + 2][1:] == [2]:
                r, g, b = params[i + 2:i + 5]
                style = replace(style, fg=f"#{r:02X}{g:02X}{b:02X}")
                i += 4
            elif p == 48 and params[i:i + 2][1:] == [2]:
                r, g, b = params[i + 2:i + 5]
                style = replace(style, bg=f"#{r:02X}{g:02X}{b:02X}")
                i += 4
            i += 1
        pos = match.end()
    if pos < len(line):
        runs.append((line[pos:], style))
    return runs


def render(lines: list[str], title: str) -> str:
    parsed = [parse_line(line) for line in lines]
    cols = max((sum(len(t) for t, _ in runs) for runs in parsed), default=80)
    cols = max(cols, len(title) + 10)

    pad_x, top, bottom = 18, 46, 16
    width = int(cols * CW + pad_x * 2)
    height = int(top + len(parsed) * LH + bottom)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">',
        f'<rect width="{width}" height="{height}" rx="12" fill="{BG}" '
        f'stroke="{BORDER}" stroke-width="1.5"/>',
        f'<path d="M 0 34 h {width}" stroke="{BORDER}" stroke-width="1"/>',
        f'<rect width="{width}" height="34" rx="12" fill="{CHROME}"/>',
        f'<rect y="22" width="{width}" height="12" fill="{CHROME}"/>',
        # traffic lights
        '<circle cx="22" cy="17" r="6" fill="#FF5F57"/>',
        '<circle cx="44" cy="17" r="6" fill="#FEBC2E"/>',
        '<circle cx="66" cy="17" r="6" fill="#28C840"/>',
        f'<text x="{width / 2:.0f}" y="21" text-anchor="middle" '
        f'font-family="{FONT}" font-size="12" fill="#6E8A79">{escape(title)}</text>',
    ]

    for row, runs in enumerate(parsed):
        y = top + row * LH
        col = 0
        spans = []
        for text, style in runs:
            if not text:
                continue
            x = pad_x + col * CW
            if style.bg:
                out.append(
                    f'<rect x="{x - 2:.1f}" y="{y - 13.5:.1f}" '
                    f'width="{len(text) * CW + 6:.1f}" height="{LH - 1}" rx="3" '
                    f'fill="{style.bg}"/>'
                )
            # Draw full-block runs as rects — pixel-perfect regardless of the
            # viewer's monospace font (used by the HELIOS banner).
            for segment in re.finditer(r"█+|[^█]+", text):
                seg = segment.group(0)
                seg_x = pad_x + (col + segment.start()) * CW
                if seg.startswith("█"):
                    opacity = ' opacity="0.66"' if style.dim else ""
                    out.append(
                        f'<rect x="{seg_x:.1f}" y="{y - 14.5:.1f}" '
                        f'width="{len(seg) * CW + 0.5:.1f}" height="{LH + 0.5}" '
                        f'fill="{style.fg}"{opacity}/>'
                    )
                    continue
                attrs = f' fill="{style.fg}"'
                if style.bold:
                    attrs += ' font-weight="700"'
                if style.dim:
                    attrs += ' opacity="0.66"'
                spans.append(
                    f'<tspan x="{seg_x:.1f}"{attrs} xml:space="preserve">'
                    f'{escape(seg)}</tspan>'
                )
            col += len(text)
        if spans:
            out.append(
                f'<text y="{y:.1f}" font-family="{FONT}" font-size="13" '
                f'xml:space="preserve">{"".join(spans)}</text>'
            )
    out.append("</svg>")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="helios")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--max-lines", type=int, default=60)
    args = parser.parse_args()

    text = sys.stdin.read()
    lines = [line.rstrip() for line in text.splitlines()]
    # trim leading/trailing blank lines
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    lines = lines[: args.max_lines]

    with open(args.output, "w") as handle:
        handle.write(render(lines, args.title))
    print(f"wrote {args.output} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
