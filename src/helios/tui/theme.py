"""
HELIOS terminal visual kernel — the equipment-console visual language.

Design doctrine (see README "Visual identity"):

* HELIOS looks like a piece of fielded scientific control equipment that has
  existed for fifteen years: equipment identification plates, 1px rules,
  rectangular panels, hard alignment, dense monospace metadata, serial and
  revision identifiers, measurement-like graphics.
* The green is an INSTRUMENT SIGNAL (● ONLINE / ● ENFORCING), never
  decoration. No gradients, no glow, no rounded cards, no emoji.
* Quiet. Severe. Technical.

The kernel is the single source of truth for palette, glyph semantics,
panel geometry, and the equipment-label header. Everything in the TUI
renders through it, and every glyph has a pure-ASCII equivalent so the
interface survives NO_COLOR, legacy encodings, and SSH on bad fonts
(§ accessibility fallback).
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime, timezone

# -- palette -----------------------------------------------------------------
# BACKGROUND #050706 / SURFACE #0A0D0B live in the SVG assets; a terminal
# owns its own background, so here we define signal colors only.
_TRUE = {
    "text":   (231, 227, 216),   # warm off-white
    "dim":    (138, 148, 141),   # muted grey-green
    "faint":  (94, 102, 97),
    "accent": (0, 230, 118),     # electric HELIOS green — instrument signal
    "sea":    (52, 211, 153),    # secondary structure green
    "warn":   (255, 209, 102),   # restrained amber
    "crit":   (255, 92, 92),     # restrained red
}


def _colors_enabled() -> bool:
    if os.environ.get("HELIOS_TUI_FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    try:
        return sys.stdout.isatty()
    except Exception:  # noqa: BLE001
        return False


COLORS = _colors_enabled()
RESET = "\x1b[0m" if COLORS else ""


def c(text: str, color: str = "text", bold: bool = False, dim: bool = False) -> str:
    if not COLORS:
        return text
    r, g, b = _TRUE.get(color, _TRUE["text"])
    prefix = f"\x1b[38;2;{r};{g};{b}m"
    if bold:
        prefix += "\x1b[1m"
    if dim:
        prefix += "\x1b[2m"
    return f"{prefix}{text}{RESET}"


# -- glyph system (unicode with strict ASCII fallback) -------------------------

def unicode_ok() -> bool:
    if os.environ.get("HELIOS_ASCII"):
        return False
    encoding = (getattr(sys.stdout, "encoding", "") or "").upper()
    return "UTF" in encoding


UNI = unicode_ok()

GL = {
    "rule": "─" if UNI else "-",
    "vrule": "│" if UNI else "|",
    "tl": "┌" if UNI else "+",
    "tr": "┐" if UNI else "+",
    "bl": "└" if UNI else "+",
    "br": "┘" if UNI else "+",
    "lt": "├" if UNI else "+",
    "rt": "┤" if UNI else "+",
    "allow": "✓" if UNI else "+",
    "gate": "!" if UNI else "!",
    "deny": "×" if UNI else "x",
    "review": "◆" if UNI else "*",
    "pending": "~" if UNI else "~",
    "dot": "●" if UNI else "*",
    "dot_off": "○" if UNI else "o",
    "arrow": "→" if UNI else "->",
    "down": "↓" if UNI else "v",
    "bar": "█" if UNI else "#",
    "bar_off": "░" if UNI else ".",
    "circle": "◯" if UNI else "O",
    "bullet": "▪" if UNI else "-",
}

# decision / state semantics — never color-only (§25)
DECISION_GLYPH = {
    "allow": GL["allow"], "allowed": GL["allow"], "executed": GL["allow"],
    "executed_ok": GL["allow"], "approved": GL["allow"], "ok": GL["allow"],
    "completed": GL["allow"], "compliant": GL["allow"],
    "require_approval": GL["gate"], "approval_required": GL["gate"],
    "awaiting_approval": GL["gate"], "pending": GL["pending"],
    "require_human_review": GL["review"], "review": GL["review"],
    "human_review": GL["review"],
    "deny": GL["deny"], "denied": GL["deny"], "blocked": GL["deny"],
    "expired": GL["deny"], "rejected": GL["deny"], "failed": GL["deny"],
}

DECISION_COLOR = {
    GL["allow"]: "accent", GL["gate"]: "warn", GL["pending"]: "dim",
    GL["review"]: "sea", GL["deny"]: "crit",
}


def glyph_for(decision: str | None) -> str:
    return DECISION_GLYPH.get((decision or "").lower(), GL["pending"])


def glyphed(decision: str | None, text: str | None = None) -> str:
    g = glyph_for(decision)
    return c(f"{g} {text if text is not None else (decision or '').upper()}",
             DECISION_COLOR.get(g, "dim"))


# -- geometry ------------------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def W(default: int = 100) -> int:
    try:
        cols = shutil.get_terminal_size().columns
    except Exception:  # noqa: BLE001
        cols = default
    return max(72, min(cols, 160))


def fit(text: str, width: int) -> str:
    """Hard-truncate a rendered line so nothing ever overflows horizontally."""
    if visible_len(text) <= width:
        return text
    plain_over = visible_len(text) - width + 1
    return text[: max(0, len(text) - plain_over)] + "…" if UNI else \
        text[: max(0, len(text) - plain_over)] + "."


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - visible_len(text))


def rule(width: int | None = None, color: str = "faint") -> str:
    return c(GL["rule"] * (width or W()), color)


def section(label: str, width: int | None = None) -> str:
    """HELIOS / SECTION header rule: label then a 1px rule to the margin."""
    width = width or W()
    head = c(label.upper(), "dim", bold=True)
    used = visible_len(label) + 1
    return f"{head} {c(GL['rule'] * max(0, width - used), 'faint')}"


def kv(key: str, value: str, key_width: int = 12, indent: int = 2) -> str:
    return f"{' ' * indent}{c(key.upper().ljust(key_width), 'dim')} {value}"


def box(lines: list[str], width: int | None = None, title: str | None = None,
        color: str = "faint") -> str:
    """Rectangular 1px panel. Hard corners. No radius. No chips."""
    width = width or W()
    inner = width - 2
    if title:
        label = f" {title.upper()} "
        top = (c(GL["tl"] + GL["rule"], color) + c(label, color, bold=True)
               + c(GL["rule"] * max(0, inner - visible_len(label) - 1)
                   + GL["tr"], color))
    else:
        top = c(GL["tl"] + GL["rule"] * inner + GL["tr"], color)
    body = []
    for line in lines:
        body.append(c(GL["vrule"], color) + " "
                    + pad(fit(line, inner - 1), inner - 1)
                    + c(GL["vrule"], color))
    bottom = c(GL["bl"] + GL["rule"] * inner + GL["br"], color)
    return "\n".join([top, *body, bottom])


def table(headers: list[str], rows: list[list[str]], width: int | None = None,
          indent: int = 2, drop: list[str] | None = None) -> str:
    """
    Dense aligned table: dim uppercase header, 1px rule, no decoration.

    Responsive contract (§22): when the natural width exceeds the available
    width, SECONDARY columns (named in `drop`, priority order) are removed
    first — identifiers and governance states survive. Only if that is not
    enough are cells truncated. Nothing ever overflows horizontally.
    """
    width = width or W()
    if not rows:
        return f"{' ' * indent}{c('(none)', 'faint')}"
    drop = list(drop or [])

    def widths_for(cols):
        out = []
        for i in cols:
            col = [visible_len(str(r[i])) for r in rows if len(r) > i]
            out.append(max([visible_len(headers[i])] + col))
        return out

    def total(ws):
        return sum(ws) + 3 * (len(ws) - 1) + indent

    cols = list(range(len(headers)))
    widths = widths_for(cols)
    while total(widths) > width and drop:
        name = drop.pop(0)
        if name in headers:
            j = headers.index(name)
            if j in cols and len(cols) > 2:
                cols.remove(j)
                widths = widths_for(cols)
    if total(widths) > width:
        over = total(widths) - width
        for k in sorted(range(len(widths)), key=lambda k: -widths[k]):
            cut = min(widths[k] - 4, over)
            if cut > 0:
                widths[k] -= cut
                over -= cut
            if over <= 0:
                break

    def fmt(cells, style=None) -> str:
        out = []
        for pos, i in enumerate(cols):
            cell = fit(str(cells[i]) if len(cells) > i else "", widths[pos])
            out.append(pad(cell, widths[pos]))
        line = "   ".join(out).rstrip()
        return " " * indent + (style(line) if style else line)

    head_line = fmt(headers, lambda s: c(s.upper(), "dim"))
    rule_len = min(width - indent, sum(widths) + 3 * (len(widths) - 1))
    rule_line = " " * indent + c(GL["rule"] * rule_len, "faint")
    return "\n".join([head_line, rule_line, *[fmt(r) for r in rows]])


# -- measurement-like graphics ---------------------------------------------------

def bar(fraction: float, width: int = 10) -> str:
    fraction = max(0.0, min(1.0, fraction or 0.0))
    filled = int(round(fraction * width))
    return c(GL["bar"] * filled, "accent") + c(GL["bar_off"] * (width - filled),
                                               "faint")


_SPARK = "▁▂▃▄▅▆▇█" if UNI else "_,-~=*#"


def spark(values: list[float], width: int = 24) -> str:
    """Instrument histogram over real series data (empty series = flat rule)."""
    if not values:
        return c(GL["rule"] * width, "faint")
    series = values[-width:]
    peak = max(series) or 1.0
    return c("".join(_SPARK[min(len(_SPARK) - 1,
                                int(v / peak * (len(_SPARK) - 1)))]
                     for v in series), "sea")


def insignia() -> list[str]:
    """The HELIOS mark: an observation instrument, not a sun."""
    if UNI:
        return ["   │   ", f" ──{GL['circle']}── ", "   │   ", "   {dot}   "
                .format(dot=GL["dot"])]
    return ["   |   ", " -O- ", "   |   ", "   *   "]


# -- equipment identification plate ----------------------------------------------

def equipment_header(*, version: str, environment: str, gateway: str,
                     tenant: str | None, state: str, width: int | None = None,
                     governed: bool = True) -> str:
    """
    The aged HELIOS equipment label, translated into terminal graphics.
    Serial/reference identifiers derive from REAL state (tenant id prefix,
    package revision, live UTC time); HX-001 / FIELD UNIT are plate texture.
    """
    width = width or W()
    inner = width - 2
    serial = (tenant or "local")[:8].upper()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    def line(content: str = "") -> str:
        return (c(GL["vrule"], "faint") + " "
                + pad(fit(content, inner - 1), inner - 1)
                + c(GL["vrule"], "faint"))

    mid = c(GL["rule"] * max(0, (inner - 46) // 2), "faint")
    top = (c(GL["tl"] + GL["rule"], "faint") + c(" HX-001 ", "dim", bold=True)
           + mid + c(" PROPERTY OF HELIOS ", "dim", bold=True) + mid
           + c(" FIELD UNIT ", "dim", bold=True)
           + c(GL["rule"] * max(0, inner - 3 - visible_len(" HX-001 ")
               - visible_len(" PROPERTY OF HELIOS ") - visible_len(" FIELD UNIT ")
               - 2 * len(mid)) + GL["tr"], "faint"))

    mark = insignia()
    rows = [
        "",
        f"   {c('H E L I O S', 'text', bold=True)}"
        f"{' ' * 22}{c(mark[0], 'sea')}"
        f"{' ' * 6}{c('SERIAL', 'faint')} {c(serial, 'dim')}",
        f"   {c('GOVERNED AI COMMAND CENTER', 'dim')}"
        f"{' ' * 5}{c(mark[1], 'sea')}"
        f"{' ' * 6}{c('REV', 'faint')}    {c('V' + version, 'dim')}",
        f"{' ' * 35}{c(mark[2], 'sea')}"
        f"{' ' * 6}{c('UNIT', 'faint')}   {c('CONTROL PLANE', 'dim')}",
        f"   {c('OBSERVE / CONSTRAIN / ENABLE', 'faint')}"
        f"{' ' * 5}{c(mark[3], 'accent')}"
        f"{' ' * 6}{c('STATE', 'faint')}  "
        + (c(state.upper(), "accent", bold=True) if governed
           else c(state.upper(), "warn", bold=True)),
        f"   {c(GL['rule'] * max(0, inner - 6), 'faint')}",
        f"   {c('ENV', 'faint')} {c(environment.upper(), 'dim')}"
        f"   {c('GATEWAY', 'faint')} {c(gateway, 'dim')}"
        f"   {c('UTC', 'faint')} {c(now, 'dim')}",
    ]
    bottom = c(GL["bl"] + GL["rule"] * inner + GL["br"], "faint")
    return "\n".join([top, *[line(r) for r in rows], bottom])


def footer(*, view: str, gateway: str, governed: bool, width: int | None = None,
           hint: str = "") -> str:
    width = width or W()
    mode = c("GOVERNED", "accent", bold=True) if governed \
        else c("DIRECT", "warn", bold=True)
    left = f" {c('HELIOS', 'dim', bold=True)} {c('//' , 'faint')} {c(view.upper(), 'text')}"
    right = f"{mode} {c('//' , 'faint')} {c(gateway, 'dim')} "
    mid_rule = GL["rule"] * max(1, width - visible_len(left) - len(right) - 8)
    line = f"{left} {c(mid_rule, 'faint')} {right}"
    if hint:
        line += "\n" + f" {c(hint, 'faint')}"
    return line


def prompt_str() -> str:
    return c("helios@control", "accent") + c(":~$ ", "dim")


# -- diagnostics (§24): errors are system diagnostics, never vague ---------------

def diag(title: str, fields: list[tuple[str, str]], note: str | None = None,
         width: int | None = None, severity: str = "crit") -> str:
    color = {"crit": "crit", "warn": "warn", "info": "dim"}.get(severity, "crit")
    lines = [f"{c(GL['rule'] * ((width or W()) - 4), color)}"]
    lines.append(c(f"  {title.upper()}", color, bold=True))
    lines.append(f"{c(GL['rule'] * ((width or W()) - 4), color)}")
    for key, value in fields:
        lines.append(kv(key, value, key_width=12))
    if note:
        lines.append("")
        lines.append(f"  {c(note, 'dim')}")
    lines.append(c(GL["rule"] * ((width or W()) - 4), color))
    return "\n".join(lines)


def ts_of(iso: str | None) -> str:
    if not iso:
        return "--:--:--"
    try:
        return iso[11:19]
    except Exception:  # noqa: BLE001
        return iso[:8]


VERSION_LABEL = "1.5.0"
