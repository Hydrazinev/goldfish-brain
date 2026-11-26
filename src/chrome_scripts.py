"""AppleScript snippets for talking to the Netflix tab in Google Chrome.

They search ALL windows for a Netflix watch tab (never `front window` — the
owner's detection broke when another Chrome window was in front) and never
launch Chrome."""
from __future__ import annotations

from typing import Optional

NETFLIX_URL_FRAGMENT = "netflix.com/watch"

# fraction of the episode played so far, '' if there is no video yet
PROGRESS_JS = ("(function(){var v=document.querySelector('video');"
               "return (v&&v.duration)?String(v.currentTime/v.duration):'';})()")


def escape_js(js: str) -> str:
    return js.replace("\\", "\\\\").replace('"', '\\"')


def js_action(js: str) -> str:
    """Run `js` in the found tab (variable `t`) and return its result."""
    return f'return execute t javascript "{escape_js(js)}"'


def find_netflix_tab_script(action: str) -> str:
    """AppleScript: run `action` (it may use `t`, the tab) for the first window
    whose active tab is a Netflix watch page; returns "" when there is none."""
    return f'''if application "Google Chrome" is running then
    tell application "Google Chrome"
        repeat with w in windows
            try
                set t to active tab of w
                if (URL of t) contains "{NETFLIX_URL_FRAGMENT}" then
                    {action}
                end if
            end try
        end repeat
    end tell
end if
return ""'''


TAB_INFO_ACTION = 'return (URL of t) & "|||" & (title of t)'


def parse_progress(raw: str) -> Optional[float]:
    try:
        v = float((raw or "").strip())
    except ValueError:
        return None
    return v if 0.0 <= v <= 1.0 else None
