"""The icon by the clock, which shows session status while the panel is hidden."""

import threading
from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw, ImageFont

from session_panel.model import Row, need_attention_count

MAX_TOOLTIP = 127  # Windows allows 128 characters, and pystray raises beyond that
MAX_MENU_ROWS = 12
MAX_LINE = 80
ICON_SIZE = 64
ATTENTION_COLOR = "#ffb454"
ACTIVE_COLOR = "#8dbdff"
IDLE_COLOR = "#a7aeb9"
TEXT_COLOR = "#14161a"


def tooltip(rows: list[Row]) -> str:
    if not rows:
        return "No Claude Code sessions running"
    sessions = "1 session" if len(rows) == 1 else f"{len(rows)} sessions"
    return f"{sessions}, {need_attention_count(rows)} need attention"[:MAX_TOOLTIP]


def _line(row: Row) -> str:
    text = f"{row.name}: {row.badge}" + (f", {row.time_text}" if row.time_text else "")
    if len(text) > MAX_LINE:
        text = text[: MAX_LINE - 1] + "…"
    return text.replace("&", "&&")  # a single & would underline the next letter in a menu


def status_lines(rows: list[Row]) -> list[str]:
    lines = [_line(row) for row in rows[:MAX_MENU_ROWS]]
    if len(rows) > MAX_MENU_ROWS:
        lines.append(f"and {len(rows) - MAX_MENU_ROWS} more")
    return lines


def draw_icon(attention: int, active: bool) -> Image.Image:
    color = ATTENTION_COLOR if attention else ACTIVE_COLOR if active else IDLE_COLOR
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, ICON_SIZE - 1, ICON_SIZE - 1), fill=color)
    if attention:
        center = (ICON_SIZE // 2, ICON_SIZE // 2)
        text = str(attention) if attention < 10 else "9+"
        draw.text(center, text, fill=TEXT_COLOR, font=ImageFont.load_default(44), anchor="mm")
    return image


class Tray:
    def __init__(
        self,
        open_panel: Callable[[], None],
        quit_app: Callable[[], None],
        icon_class=pystray.Icon,
    ) -> None:
        self._open_panel = open_panel
        self._quit_app = quit_app
        self._lines: list[str] = []
        self._look: tuple[int, bool] = (0, False)
        self._icon = icon_class(
            "claude-sessions",
            draw_icon(*self._look),
            tooltip([]),
            pystray.Menu(self._menu_items),
        )

    def _menu_items(self):
        yield pystray.MenuItem("Open panel", self._in_background(self._open_panel), default=True)
        yield pystray.Menu.SEPARATOR
        for line in self._lines:
            yield pystray.MenuItem(line, None, enabled=False)
        if self._lines:
            yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Quit", self._in_background(self._quit_app))

    @staticmethod
    def _in_background(action: Callable[[], None]) -> Callable[[], None]:
        """These open windows and dialogs, which must not block the tray's own thread."""

        def run() -> None:
            threading.Thread(target=action, daemon=True).start()

        return run

    def start(self) -> None:
        self._icon.run_detached()

    def stop(self) -> None:
        """Required at exit: the icon's thread would otherwise keep the app alive."""
        self._icon.stop()

    def update(self, rows: list[Row]) -> None:
        self._icon.title = tooltip(rows)
        look = (need_attention_count(rows), bool(rows))
        if look != self._look:
            self._look = look
            self._icon.icon = draw_icon(*look)
        lines = status_lines(rows)
        if lines != self._lines:
            self._lines = lines
            self._icon.update_menu()
