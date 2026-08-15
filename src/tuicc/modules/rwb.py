"""RWB ("real world box") — time, date, and (when [weather] is
configured) a compact weather readout, with a detailed weather summary
in the preview panel when the box is selected. Renamed from clock.py:
the module stopped being "just the clock" once weather.py landed.

The box itself stays compact on purpose (one extra line, not a
multi-line dump) — the detail lives in the preview panel instead,
reusing tuicc's own sidebar+preview identity rather than growing the
box itself. Nothing here auto-resizes the box to fit more — that's the
user's own call via F2, same "user owns layout footprint" principle
CLAUDE/NOTES/design-decisions.md's layout-scaling notes already cover.
"""

import curses
from datetime import datetime

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, centered_x, draw_width_safe


def _weather_line(ctx):
    """Returns (text, color_pair) for the box's own compact weather
    line — icon + temperature, plus ctx.config.weather_city_code
    inline in front of it when the user's set one (e.g. "PRG ☀️18°C")
    — a plain personal-preference display badge, never auto-derived
    (see Config.weather_city_code's own docstring for why), empty by
    default. Or (None, 0) when weather isn't configured at all
    (ctx.status is None, or the "weather" domain was never registered
    — see app_setup.py's conditional registration). Distinguishes "not
    configured" (draw nothing) from "configured but erroring" (draw a
    visible ⚠) — never a guessed value in either case, same discipline
    as bars.py's own BarSpec.urgent handling.
    """
    theme = ctx.theme or {}
    if ctx.status is None:
        return None, 0
    weather = ctx.status.get("weather")
    error = ctx.status.get_error("weather")
    if weather is not None:
        code = f"{ctx.config.weather_city_code} " if ctx.config.weather_city_code else ""
        # No space between icon and temperature: the icon's own
        # double-column emoji presentation already reads as visually
        # wider than a plain character, so a following space made the
        # gap look oversized next to it — confirmed live from a
        # screenshot of the real box (☀️ 27°C had a noticeably bigger
        # gap than the code-to-icon or date-to-city-code gaps).
        return f"{code}{weather.icon}{weather.temperature_c:.0f}°C", theme.get("text", 0)
    if error is not None:
        return "⚠ unavailable", theme.get("urgent", 0) | curses.A_BOLD
    return None, 0  # not configured, or not polled yet


def _build_weather_preview(ctx) -> list[tuple[str, int]] | None:
    """The detailed view shown in preview.py while this box is
    selected — same (text, color_pair)-per-line shape every other
    module's preview_text already uses. None when weather isn't
    configured (mirrors _weather_line above), so nav_items() knows to
    stay non-interactive in that case, same as clock.py always was.
    One color per whole line (preview_text's own shape has no per-
    segment styling) — location+conditions on their own line, the
    day-by-day outlook strip on another, not interleaved.
    """
    theme = ctx.theme or {}
    if ctx.status is None:
        return None
    weather = ctx.status.get("weather")
    error = ctx.status.get_error("weather")
    if weather is None and error is None:
        return None
    if weather is None:
        return [
            ("⚠ Weather unavailable", theme.get("urgent", 0) | curses.A_BOLD),
            (error, theme.get("urgent", 0)),
        ]
    # No space between icon and temperature — same fix as
    # _weather_line's own compact box line: the icon's wide emoji
    # presentation already reads as visually wider than a plain
    # character, so a trailing space next to it looked oversized
    # compared to the other gaps on the same line (confirmed live from
    # a screenshot of the real preview panel).
    outlook = "   ".join(
        f"{day.date} {day.icon}{day.low_c:.0f}-{day.high_c:.0f}°C" for day in weather.days
    )
    return [
        (f"{weather.location_name}   {weather.icon}{weather.temperature_c:.0f}°C   {weather.description}", theme.get("accent", 0)),
        (f"feels {weather.apparent_temperature_c:.0f}°C   wind {weather.wind_speed_kmh:.0f} km/h   humidity {weather.humidity_pct}%", theme.get("text", 0)),
        ("", 0),
        (outlook, theme.get("text", 0)),
    ]


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color)

    now = datetime.now()
    time_str = now.strftime(ctx.config.rwb_time_format)
    date_str = now.strftime(ctx.config.rwb_date_format)

    inner_w = w - 2
    time_x = x + 1 + centered_x(0, inner_w, time_str)
    date_x = x + 1 + centered_x(0, inner_w, date_str)

    try:
        stdscr.addstr(y + 1, time_x, time_str, theme.get("accent", 0))
        stdscr.addstr(y + 2, date_x, date_str, theme.get("text", 0))
    except curses.error:
        pass

    weather_line, weather_color = _weather_line(ctx)
    if weather_line is not None:
        weather_x = x + 1 + centered_x(0, inner_w, weather_line)
        # draw_width_safe, not a plain addstr — weather_line can start
        # with a wide/emoji icon glyph, see its own docstring for why
        # a single addstr() call spanning one isn't safe even with
        # correct width math.
        draw_width_safe(stdscr, y + 3, weather_x, weather_line, weather_color)


def nav_items(box, ctx, module_name) -> list[NavItem]:
    preview_text = _build_weather_preview(ctx)
    if preview_text is None:
        return []  # weather not configured — box stays non-interactive, as before
    x, y, w, h = box
    error = ctx.status.get_error("weather") if ctx.status is not None else None
    return [NavItem(
        id=f"{module_name}:weather",
        rect=(x, y, w, h),
        # Dedicated, unregistered target_kind — same precedent as
        # sysmon.py's own "sysmon_diagnostics" row: no ACTION_HANDLERS
        # entry means Enter is a safe no-op, exactly right for a
        # read-only Context box with no action (VISION.md's
        # four-category test).
        target_kind="rwb_weather",
        preview_text=preview_text,
        preview_urgent=error is not None,
    )]
