"""Browser-side JavaScript lives in agent_access/js/*.js; this loads it.

A file whose first line is `// @body` holds a function body. It is wrapped as `(arg) => { <helpers> <body> }`
so it can use the shared helpers in agent_access/js/helpers.js (cssPath, label, isVisible, effectiveBg, idOf, ...).
Any other file holds a complete JS expression, passed to Playwright as is.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

JS_DIR = Path(__file__).parent / "js"
BODY_MARKER = "// @body"


@lru_cache(maxsize=None)
def js(name: str) -> str:
    source = (JS_DIR / f"{name}.js").read_text(encoding="utf-8")
    if source.startswith(BODY_MARKER):
        helpers = (JS_DIR / "helpers.js").read_text(encoding="utf-8")
        return f"(arg) => {{ {helpers}\n{source[len(BODY_MARKER):]} }}"
    return source.strip()
