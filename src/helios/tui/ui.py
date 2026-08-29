"""
Helios TUI toolkit — the emerald terminal theme.

Zero dependencies: truecolor ANSI, box drawing, aligned tables, badges, and
a worker-thread spinner. Honors NO_COLOR / TERM=dumb, degrades to plain
text, and stays readable over SSH and in narrow terminals.
"""

from __future__ import annotations

import itertools
import os
import re
import shutil
import sys
import threading
import time

# -- palette (matches the lantern-ring brand) ------------------------------

_TRUE = {
    "green":  (0, 230, 118),    # #00E676 — primary
    "mint":   (140, 255, 192),  # #8CFFC0 — highlight
    "sea":    (52, 211, 153),   # #34D399 — secondary
    "deep":   (11, 138, 67),    # #0B8A43 — deep ring green
    "fg":     (214, 228, 220),  # near-white with a green cast
    "dim":    (101, 128, 112),  # muted green-grey
    "red":    (255, 92, 92),
    "yellow": (255, 209, 102),
    "black":  (5, 10, 7),
}


def _colors_enabled() -> bool:
    if os.environ.get("HELIOS_TUI_FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return sys.stdout.isatty()


COLORS = _colors_enabled()
RESET = "\x1b[0m" if COLORS else ""


def _fg(name: str) -> str:
    r, g, b = _TRUE[name]
    return f"\x1b[38;2;{r};{g};{b}m"


def _bg(name: str) -> str:
    r, g, b = _TRUE[name]
    return f"\x1b[48;2;{r};{g};{b}m"


def c(text: str, color: str = "fg", bold: bool = False, dim: bool = False) -> str:
    """Colorize text (no-op when colors are disabled)."""
    if not COLORS:
        return text
    prefix = _fg(color)
    if bold:
        prefix += "\x1b[1m"
    if dim:
        prefix += "\x1b[2m"
    return f"{prefix}{text}{RESET}"


def badge(text: str, color: str = "green") -> str:
    """Inverse block badge:  GOVERNED  in green-on-black-text."""
    if not COLORS:
        return f"[{text}]"
    return f"{_bg(color)}{_fg('black')}\x1b[1m {text} {RESET}"


RISK_COLORS = {
    "informational": "dim",
    "low": "sea",
    "medium": "yellow",
    "high": "red",
    "critical": "red",
}


def risk_badge(risk: str) -> str:
    return badge(risk.upper(), RISK_COLORS.get(risk, "dim"))


def status_dot(ok: bool | str) -> str:
    if isinstance(ok, str):
        ok = ok in ("healthy", "ok", "completed", "success", "active")
    return c("●", "green") if ok else c("○", "yellow")


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def term_width(default: int = 100) -> int:
    try:
        return min(shutil.get_terminal_size().columns, 110)
    except Exception:  # noqa: BLE001
        return default


# -- components ------------------------------------------------------------


def hr(width: int | None = None) -> str:
    return c("─" * (width or term_width()), "dim")


def kv(key: str, value: str, key_width: int = 11) -> str:
    return f"  {c(key.ljust(key_width), 'dim')} {value}"


def panel(title: str, lines: list[str], color: str = "green") -> str:
    """Rounded box with a title chip on the top border."""
    width = min(term_width(), max(
        [visible_len(line) for line in lines] + [len(title) + 6, 40]
    ) + 4)
    inner = width - 2
    top = (
        c("╭─", color) + badge(title, color) + c("─" * max(0, inner - len(title) - 3), color)
        + c("╮", color)
        if COLORS
        else f"+-[ {title} ]" + "-" * max(0, inner - len(title) - 5) + "+"
    )
    body = []
    for line in lines:
        pad = max(0, inner - 1 - visible_len(line))
        body.append(c("│", color) + " " + line + " " * pad + c("│", color))
    bottom = c("╰" + "─" * inner + "╯", color) if COLORS else "+" + "-" * inner + "+"
    return "\n".join([top, *body, bottom])


def table(headers: list[str], rows: list[list[str]], indent: int = 2) -> str:
    """Aligned table with a dim header rule. ANSI-safe width math."""
    if not rows:
        return c("  (none)", "dim")
    widths = [
        max(visible_len(str(cell)) for cell in col)
        for col in zip(headers, *rows)
    ]

    def fmt(cells, style=None):
        out = []
        for cell, width in zip(cells, widths):
            cell = str(cell)
            pad = " " * (width - visible_len(cell))
            out.append((style(cell) if style else cell) + pad)
        return " " * indent + "  ".join(out).rstrip()

    lines = [
        fmt(headers, style=lambda s: c(s.upper(), "sea", bold=True)),
        " " * indent + c("┈" * (sum(widths) + 2 * (len(widths) - 1)), "dim"),
    ]
    lines.extend(fmt(row) for row in rows)
    return "\n".join(lines)


def bullet(text: str, mark: str = "▪", color: str = "green") -> str:
    return f"  {c(mark, color)} {text}"


def error(text: str) -> str:
    return c(f"  ✗ {text}", "red")


def success(text: str) -> str:
    return f"  {c('✓', 'green')} {text}"


# -- banner ----------------------------------------------------------------

_LOGO = [
    "██  ██  ██████  ██      ██████  ██████  ██████",
    "██  ██  ██      ██        ██    ██  ██  ██    ",
    "██████  ████    ██        ██    ██  ██  ██████",
    "██  ██  ██      ██        ██    ██  ██      ██",
    "██  ██  ██████  ██████  ██████  ██████  ██████",
]
_LOGO_SHADES = ["mint", "green", "green", "sea", "deep"]


def banner(subtitle: str, meta: str) -> str:
    lines = [""]
    for row, shade in zip(_LOGO, _LOGO_SHADES):
        lines.append("  " + c(row, shade, bold=True))
    lines.append("")
    lines.append("  " + c("◉", "green", bold=True) + " " + c(subtitle, "fg", bold=True))
    lines.append("  " + c(meta, "dim"))
    lines.append("")
    return "\n".join(lines)


# -- readline-safe prompt --------------------------------------------------


def prompt_segment(text: str, color: str, bold: bool = False) -> str:
    """ANSI wrapped in \\001…\\002 so readline computes the width correctly."""
    if not COLORS:
        return text
    codes = _fg(color) + ("\x1b[1m" if bold else "")
    return f"\001{codes}\002{text}\001{RESET}\002"


def build_prompt(gateway: str, governed: bool, workspace: str | None, model: str) -> str:
    parts = [prompt_segment(gateway, "green" if governed else "yellow", bold=True)]
    if workspace:
        parts.append(prompt_segment("▸", "dim") + prompt_segment(workspace, "sea", bold=True))
    parts.append(prompt_segment("▸", "dim") + prompt_segment(model, "dim"))
    return " ".join(parts) + " " + prompt_segment("❯", "green", bold=True) + " "


# -- spinner ---------------------------------------------------------------

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner:
    """Braille spinner on stderr; silent when output is not a TTY."""

    def __init__(self, message: str = "thinking") -> None:
        self.message = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.enabled = COLORS and sys.stderr.isatty() and sys.stdin.isatty()

    def _spin(self) -> None:
        for frame in itertools.cycle(_FRAMES):
            if self._stop.is_set():
                break
            sys.stderr.write(
                f"\r{_fg('green')}{frame}{RESET} {_fg('dim')}"
                f"{self.message}…{RESET} "
            )
            sys.stderr.flush()
            time.sleep(0.08)
        sys.stderr.write("\r" + " " * (len(self.message) + 6) + "\r")
        sys.stderr.flush()

    def __enter__(self) -> "Spinner":
        if self.enabled:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._thread:
            self._stop.set()
            self._thread.join(timeout=1)
