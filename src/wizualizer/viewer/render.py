"""Turn a trace into one self-contained HTML file (inlined JS/CSS, no network)."""

from __future__ import annotations

import json
from importlib import resources


def render_html(trace: dict) -> str:
    pkg = resources.files("wizualizer.viewer")
    template = (pkg / "viewer.html").read_text(encoding="utf-8")
    css = (pkg / "viewer.css").read_text(encoding="utf-8")
    js = (pkg / "viewer.js").read_text(encoding="utf-8")
    data = json.dumps(trace, separators=(",", ":"))
    # Keep the JSON inert inside <script>: no "</script>" or "<!--" can end the block.
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    title = _escape(f"wiz · {trace['meta']['test']}")
    return (
        template.replace("/*__CSS__*/", css)
        .replace("/*__JS__*/", js.replace("</script", "<\\/script"))
        .replace("__TRACE_JSON__", data)
        .replace("__TITLE__", title)
    )


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
