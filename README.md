# Claude Session Panel

A slim window for Windows and macOS that lists every Claude Code session on this computer, with its state, waiting estimate and recap, and notifies you when one needs you.

It runs in the background with an icon by the clock, which shows how many sessions need attention. The window's X only hides the panel. To stop the app, use Quit in the icon's menu or the panel's header.

Click a session to bring its VS Code window to the front; the Recap button shows its recap. This needs the Claude Code extension in VS Code, and VS Code asks you to confirm the first time. A session with no open VS Code window does nothing when clicked.

**Windows:** hover over the icon for a summary, right-click it for every session's status, and left-click it to open the panel. Windows may tuck a new icon under the `^` arrow by the clock. To keep it visible, drag it onto the taskbar.

**macOS:** click the icon in the menu bar for every session's status and Open panel. Notifications come from Script Editor, so allow them in System Settings > Notifications. A Focus mode can hold them back, and on macOS the app has no alert of its own to cover that.

- Install: `uv tool install git+https://github.com/7174Andy/claude-code-session-manager`, then run `session-panel`
- Run: `uv run session-panel`
- Test: `uv run pytest`
- Lint: `uv run ruff check`
