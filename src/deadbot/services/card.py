"""Shareable PNG profile card (Pillow). CPU-bound: call render_card via asyncio.to_thread."""

import io
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from deadbot.services.profile import ProfileReport
from deadbot.services.teammates import TeammatesReport

W, H = 1000, 560
PAD = 36
BG = (21, 23, 28)
PANEL = (32, 35, 42)
FG = (235, 235, 235)
MUTED = (150, 154, 162)
GOLD = (201, 166, 97)
GREEN = (110, 190, 120)
RED = (215, 105, 95)
ROLE_COLORS = {
    "brawler": (214, 120, 84),
    "marksman": (201, 166, 97),
    "mystic": (130, 120, 220),
    "assassin": (100, 170, 200),
}
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Docker image (fonts-dejavu-core)
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",  # macOS dev
)


@cache
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        p = Path(path.replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf") if bold else path)
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size=size)


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def render_card(
    profile: ProfileReport, teammates: TeammatesReport | None, avatar: bytes | None
) -> bytes:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 6), fill=GOLD)

    # Header: avatar, name, rank, record
    av_size = 128
    if avatar:
        try:
            av = Image.open(io.BytesIO(avatar)).convert("RGB")
            av = ImageOps.fit(av, (av_size, av_size))
            mask = Image.new("L", (av_size, av_size), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, av_size, av_size), 18, fill=255)
            img.paste(av, (PAD, PAD), mask)
        except OSError:
            avatar = None
    if not avatar:
        d.rounded_rectangle((PAD, PAD, PAD + av_size, PAD + av_size), 18, fill=PANEL)

    x = PAD + av_size + 24
    name = profile.persona_name or f"[U:1:{profile.account_id}]"
    d.text((x, PAD + 4), _fit(d, name, _font(40, True), W - x - PAD), font=_font(40, True), fill=FG)
    d.text((x, PAD + 58), profile.rank_label or "Unranked", font=_font(24), fill=GOLD)
    record = f"{profile.total_games:,} games · {_pct(profile.total_wins / profile.total_games)} WR"
    if profile.window_days:
        record += (
            f"   ·   last {profile.window_days}d: "
            f"{_pct(profile.window_wins / profile.window_games)}"
        )
    d.text((x, PAD + 92), record, font=_font(20), fill=MUTED)

    top = PAD + av_size + 30
    col_w = (W - 3 * PAD) // 2

    # Left panel: top heroes with win-rate bars, then hero-type mix
    lx = PAD
    d.rounded_rectangle((lx, top, lx + col_w, H - PAD), 14, fill=PANEL)
    d.text((lx + 20, top + 16), "TOP HEROES", font=_font(16, True), fill=MUTED)
    y = top + 46
    for h in profile.top_heroes[:5]:
        d.text((lx + 20, y), _fit(d, h.name, _font(20), 150), font=_font(20), fill=FG)
        bar_x, bar_w = lx + 180, col_w - 300
        d.rounded_rectangle((bar_x, y + 6, bar_x + bar_w, y + 18), 6, fill=BG)
        d.rounded_rectangle(
            (bar_x, y + 6, bar_x + max(6, int(bar_w * h.win_rate)), y + 18),
            6,
            fill=GREEN if h.win_rate >= 0.5 else RED,
        )
        d.text(
            (lx + col_w - 108, y), f"{_pct(h.win_rate)} · {h.games}g", font=_font(18), fill=MUTED
        )
        y += 34

    if profile.roles:
        y += 8
        bar_x, bar_w = lx + 20, col_w - 40
        cursor = bar_x
        for t, share in profile.roles:
            seg = int(bar_w * share)
            d.rectangle((cursor, y, cursor + seg, y + 14), fill=ROLE_COLORS.get(t, MUTED))
            cursor += seg
        y += 22
        lx2 = bar_x
        for t, s in profile.roles:  # colored dot + label per type, wrapping inside the panel
            label = f"{t.title()} {_pct(s)}"
            width = 14 + int(d.textlength(label, font=_font(15)))
            if lx2 + width > bar_x + bar_w:
                lx2, y = bar_x, y + 22
            d.ellipse((lx2, y + 5, lx2 + 9, y + 14), fill=ROLE_COLORS.get(t, MUTED))
            d.text((lx2 + 14, y), label, font=_font(15), fill=MUTED)
            lx2 += width + 16

    # Right panel: top teammates
    rx = PAD * 2 + col_w
    d.rounded_rectangle((rx, top, rx + col_w, H - PAD), 14, fill=PANEL)
    d.text((rx + 20, top + 16), "TOP TEAMMATES", font=_font(16, True), fill=MUTED)
    label = "SYNERGY"
    d.text(
        (rx + col_w - 20 - d.textlength(label, font=_font(14, True)), top + 18),
        label,
        font=_font(14, True),
        fill=MUTED,
    )
    y = top + 46
    entries = teammates.entries[:7] if teammates else []
    if not entries:
        d.text((rx + 20, y), "No frequent teammates yet", font=_font(20), fill=MUTED)
    for t in entries:
        d.text((rx + 20, y), _fit(d, t.name, _font(20), 190), font=_font(20), fill=FG)
        stats = f"{t.games}g · {_pct(t.win_rate)}"
        d.text((rx + 220, y), stats, font=_font(18), fill=MUTED)
        if t.synergy is not None:
            syn = f"{t.synergy * 100:+.1f}%"
            d.text(
                (rx + col_w - 20 - d.textlength(syn, font=_font(18, True)), y),
                syn,
                font=_font(18, True),
                fill=GREEN if t.synergy >= 0 else RED,
            )
        y += 34

    d.text(
        (W - PAD - 270, H - 26),
        "deadbot · data: deadlock-api.com",
        font=_font(14),
        fill=MUTED,
    )
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
