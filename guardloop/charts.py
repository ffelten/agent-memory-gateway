"""Draw the guard-loop progress charts (static SVG, light/dark aware) from guardloop/results.json.

    .venv/bin/python -m guardloop.charts
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS = ROOT / "guardloop" / "results.json"
OUT = ROOT / "docs" / "img"

W, H = 720, 340
L, R, T, B = 64, 150, 92, 52  # plot margins; right margin holds direct labels
STYLE = """<style>
  .s { fill: #fcfcfb } .t1 { fill: #0b0b0b } .t2 { fill: #52514e } .mu { fill: #898781 }
  .g { stroke: #e1e0d9 } .ax { stroke: #c3c2b7 } .on { stroke: #2a78d6; fill: #2a78d6 }
  .off { stroke: #eb6834; fill: #eb6834 } .ring { stroke: #fcfcfb }
  @media (prefers-color-scheme: dark) {
    .s { fill: #1a1a19 } .t1 { fill: #f0efec } .t2 { fill: #c3c2b7 }
    .g { stroke: #2c2c2a } .ax { stroke: #383835 } .on { stroke: #3987e5; fill: #3987e5 }
    .off { stroke: #d95926; fill: #d95926 } .ring { stroke: #1a1a19 }
  }
  .ln { fill: none }
  text { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif }
</style>"""


def _frame(title, subtitle, aria):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
            f'role="img" aria-label="{aria}">', f"<title>{aria}</title>", STYLE,
            f'<rect class="s" width="{W}" height="{H}" rx="10"/>',
            f'<text class="t1" x="{L - 40}" y="32" font-size="17" font-weight="600">{title}</text>',
            f'<text class="t2" x="{L - 40}" y="54" font-size="13">{subtitle}</text>']


def _legend(y=76):
    return [f'<rect class="on" x="{L - 40}" y="{y - 9}" width="12" height="12" rx="3" stroke="none"/>',
            f'<text class="t2" x="{L - 22}" y="{y + 1}" font-size="12">With learned rules</text>',
            f'<rect class="off" x="{L + 130}" y="{y - 9}" width="12" height="12" rx="3" stroke="none"/>',
            f'<text class="t2" x="{L + 148}" y="{y + 1}" font-size="12">Built-in scanner only</text>']


def _x(i, n):
    return L + (W - L - R) * (i / (n - 1) if n > 1 else 0.5)


def _axes(n, ymax, ticks, fmt, xpos=_x):
    out, bottom = [], H - B
    for v in ticks:
        y = bottom - (H - T - B) * v / ymax
        out.append(f'<line class="g" x1="{L}" x2="{W - R + 16}" y1="{y:.1f}" y2="{y:.1f}" stroke-width="1"/>')
        out.append(f'<text class="mu" x="{L - 10}" y="{y + 4:.1f}" font-size="11" text-anchor="end">{fmt(v)}</text>')
    out.append(f'<line class="ax" x1="{L}" x2="{W - R + 16}" y1="{bottom}" y2="{bottom}" stroke-width="1"/>')
    for i in range(n):
        out.append(f'<text class="mu" x="{xpos(i, n):.1f}" y="{bottom + 20}" font-size="11" '
                   f'text-anchor="middle">Round {i + 1}</text>')
    return out


def block_rate_chart(res):
    rounds = res["rounds"]
    on = [r["blocked_at_door"] / r["leak_attempts"] * 100 for r in rounds]
    off = [r["builtin_only_blocked"] / r["leak_attempts"] * 100 for r in rounds]
    n, bottom = len(on), H - B
    found = sum(r["auditor_found"] for r in rounds)
    benign = sum(r["benign"] for r in rounds)
    benign_blocked = sum(r["benign_blocked_at_door"] for r in rounds)

    def y(v):
        return bottom - (H - T - B) * v / 100
    out = _frame("Leaks stopped at the door",
                 f"Leaks found later in Senso by the auditor: {found} · benign notes blocked: {benign_blocked} of {benign}",
                 "Line chart: share of leak attempts stopped at the door per round, with learned rules versus built-in scanner only")
    out += _legend() + _axes(n, 100, (0, 25, 50, 75, 100), lambda v: f"{v:.0f}%")
    for cls, vals in (("off", off), ("on", on)):
        pts = " ".join(f"{_x(i, n):.1f},{y(v):.1f}" for i, v in enumerate(vals))
        out.append(f'<polyline class="{cls} ln" points="{pts}" stroke-width="2" '
                   f'stroke-linejoin="round" stroke-linecap="round"/>')
        for i, v in enumerate(vals):
            out.append(f'<circle class="{cls} ring" cx="{_x(i, n):.1f}" cy="{y(v):.1f}" r="5" stroke-width="2"/>')
    for cls, vals, label in (("on", on, "Learned"), ("off", off, "Built-in")):
        out.append(f'<text class="t1" x="{_x(n - 1, n) + 14:.1f}" y="{y(vals[-1]) + 4:.1f}" font-size="12" '
                   f'font-weight="600">{vals[-1]:.0f}% <tspan class="t2" font-weight="400">{label}</tspan></text>')
    return "\n".join(out + ["</svg>"])


def main():
    res = json.loads(RESULTS.read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "guard-loop-block-rate.svg").write_text(block_rate_chart(res) + "\n")
    print("wrote docs/img/guard-loop-block-rate.svg")


if __name__ == "__main__":
    main()
