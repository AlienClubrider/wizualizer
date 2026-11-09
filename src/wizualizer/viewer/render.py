"""Turn a trace into one self-contained HTML file (inlined JS/CSS, no network)."""

from __future__ import annotations

import json
from importlib import resources


def asset(name: str) -> str:
    return (resources.files("wizualizer.viewer") / name).read_text(encoding="utf-8")


def render_html(trace: dict, back_href: str | None = None) -> str:
    """back_href: when served by `wiz`, a link back to the flows list."""
    template = asset("viewer.html")
    js = asset("viewer.js")
    data = json.dumps(trace, separators=(",", ":"))
    # Keep the JSON inert inside <script>: no "</script>" or "<!--" can end the block.
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    title = escape(f"wiz · {trace['meta']['test']}")
    back = f'<a class="back" href="{escape(back_href)}" aria-label="All flows">← flows</a>' if back_href else ""
    return (
        template.replace("/*__THEME_CSS__*/", asset("theme.css"))
        .replace("/*__CSS__*/", asset("viewer.css"))
        .replace("/*__THEME_JS__*/", asset("theme.js").replace("</script", "<\\/script"))
        .replace("<!--__BACK__-->", back)
        .replace("/*__JS__*/", js.replace("</script", "<\\/script"))
        .replace("__TRACE_JSON__", data)
        .replace("__TITLE__", title)
    )


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
