"""GET /: serve the upload page from the API itself.

Serving the page from the same origin as /upload-url and /status means the
browser makes same-origin requests: no CORS involved and no ?api= parameter,
and reviewers get a single link. The page is packaged with the function (see
package.patterns in serverless.yml) and read once per container.
"""

from __future__ import annotations

from pathlib import Path

PAGE = Path(__file__).resolve().parents[2] / "ui" / "index.html"
_html = PAGE.read_text(encoding="utf-8")


def handler(event: dict, context: object) -> dict:
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            # Always fetch the latest page after a redeploy.
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
        "body": _html,
    }
