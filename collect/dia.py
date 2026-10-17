"""Minimal driver for Carl's real Dia browser via AppleScript.

Requires Dia launched with --enable-applescript-javascript (otherwise error -10006).
This module never launches, quits or relaunches Dia, and only touches tabs it opened.
JavaScript runs in an isolated world: the DOM is shared with the page, page JS globals are not.
"""
from __future__ import annotations

import json
import subprocess


class DiaError(RuntimeError):
    pass


def _osa(script: str, timeout: float = 60) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout, check=False)
    if r.returncode != 0:
        raise DiaError(f"osascript failed ({r.returncode}): {r.stderr.strip()}")
    return r.stdout.strip()


def _q(s: str) -> str:
    """Quote a Python string as an AppleScript string literal.

    JSON's escapes for quote, backslash, newline, tab and CR are also AppleScript's. ensure_ascii
    must be off: AppleScript has no \\uXXXX escape, so "…" has to pass through as itself.
    """
    return json.dumps(s, ensure_ascii=False)


def _with_tab(tab_id: str, body: str) -> str:
    return f'''tell application "Dia"
repeat with w in windows
repeat with t in tabs of w
if (id of t as text) is {_q(tab_id)} then
{body}
end if
end repeat
end repeat
error "tab not found: {tab_id}"
end tell'''


def open_tab(url: str) -> str:
    """Open url in a new tab at the end of the front window; return the tab id."""
    return _osa(f'''tell application "Dia"
set t to make new tab at end of tabs of window 1 with properties {{URL:{_q(url)}}}
return id of t
end tell''')


def set_url(tab_id: str, url: str) -> None:
    _osa(_with_tab(tab_id, f"set URL of t to {_q(url)}\nreturn \"ok\""))


def close_tab(tab_id: str) -> None:
    _osa(_with_tab(tab_id, 'close t\nreturn "ok"'))


def js(tab_id: str, code: str) -> str:
    """Run JavaScript in the tab (isolated world) and return its string result.

    Dia hands back the JS value JSON-encoded (a string arrives as "\\"...\\""), so decode once.
    """
    out = _osa(_with_tab(tab_id, f"return execute t javascript {_q(code)}"), timeout=90)
    if out.startswith('"'):
        out = json.loads(out)
    return out


def js_json(tab_id: str, code: str):
    """Run JS that returns JSON.stringify(...) and decode it."""
    out = js(tab_id, code)
    if not out or out == "missing value":
        raise DiaError("JS returned nothing")
    return json.loads(out)
