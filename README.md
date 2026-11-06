# Claude Session Panel

A slim Windows window that lists every Claude Code session on this computer, with its state, waiting estimate and recap, and notifies you when one needs you.

It runs in the background with an icon by the clock, which shows how many sessions need attention. Hover over it for a summary, right-click it for every session's status, and left-click it to open the panel. The window's X only hides the panel. To stop the app, use Quit in the icon's menu or the panel's header.

Windows may tuck a new icon under the `^` arrow by the clock. To keep it visible, drag it onto the taskbar.

- Run: `uv run session-panel`
- Test: `uv run pytest`
