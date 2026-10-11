"""Run the upload pipeline locally, using the real Lambda handlers.

    python scripts/dev_server.py            # then open http://localhost:8000

Stands in for the two AWS pieces that cannot run on a laptop:
  * API Gateway -> this HTTP server, which turns requests into the same event
    dicts API Gateway sends and calls the handlers in src/handlers/.
  * S3 -> the .dev-s3/ folder. The "presigned URL" points back at this server;
    a PUT there saves the file and fires process_csv with an S3-shaped event,
    exactly like an S3 ObjectCreated notification.
Everything else is real: the handler code, validation, Postgres (Docker), and
the n8n webhook call that starts Workflow B.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from local_ingest import load_env_file  # noqa: E402  (same folder)

STORE = ROOT / ".dev-s3"
BUCKET = "local-dev"
UPLOAD_KEY = re.compile(r"^uploads/[0-9a-f-]{36}\.csv$")

load_env_file(ROOT / ".env")
os.environ.pop("AWS_PROFILE", None)  # no AWS account needed; boto3 must not look for a profile
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
os.environ["UPLOAD_BUCKET"] = BUCKET
os.environ.setdefault("DB_SSLMODE", "disable")
if not os.environ.get("N8N_WEBHOOK_URL"):
    os.environ["N8N_WEBHOOK_URL"] = "http://localhost:5678/webhook/loan-match"

from src.handlers import get_status, get_upload_url, process_csv  # noqa: E402

log = logging.getLogger("dev_server")


class LocalS3:
    """The two S3 calls process_csv makes, backed by a local folder."""

    def __init__(self, root: Path):
        self.root = root

    def get_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803 (boto3 names)
        return {"Body": (self.root / Key).open("rb")}

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(filename, target)


def install_fakes(base_url: str) -> None:
    process_csv._s3 = LocalS3(STORE)
    get_upload_url._s3.generate_presigned_url = (
        lambda _op, Params, ExpiresIn, HttpMethod: f"{base_url}/dev-s3/{Params['Key']}"  # noqa: N803
    )


def run_process_csv(key: str) -> None:
    event = {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": quote_plus(key)}}}]}
    try:
        process_csv.handler(event, None)
    except Exception:  # the handler already logged and marked the batch FAILED
        log.exception("process_csv failed for %s", key)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_lambda(self, response: dict) -> None:
        self._send(response["statusCode"], response["body"].encode(),
                   response.get("headers", {}).get("Content-Type", "application/json"))

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            self._send(200, (ROOT / "ui" / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/upload-url":
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            self._send_lambda(get_upload_url.handler({"queryStringParameters": params}, None))
        elif url.path.startswith("/status/"):
            batch_id = url.path.removeprefix("/status/")
            self._send_lambda(get_status.handler({"pathParameters": {"batch_id": batch_id}}, None))
        else:
            self._send(404, b'{"error": "not found"}')

    def do_PUT(self) -> None:  # noqa: N802
        key = urlparse(self.path).path.removeprefix("/dev-s3/")
        if not UPLOAD_KEY.match(key):
            self._send(403, b'{"error": "not an upload key"}')
            return
        # A real presigned URL is signed with Content-Type text/csv; S3 rejects anything else.
        if self.headers.get("Content-Type") != "text/csv":
            self._send(403, b'{"error": "Content-Type must be text/csv"}')
            return
        target = STORE / key
        target.parent.mkdir(parents=True, exist_ok=True)
        remaining = int(self.headers.get("Content-Length", 0))
        with target.open("wb") as fh:
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        self._send(200, b"")
        log.info("stored %s (%d KB); firing process_csv", key, target.stat().st_size // 1024)
        threading.Thread(target=run_process_csv, args=(key,), daemon=True).start()

    def log_message(self, fmt: str, *args) -> None:
        if "/status/" not in self.path:  # polls every 2 s would drown the log
            log.info("%s %s", self.command, self.path.split("?")[0])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")

    base_url = f"http://localhost:{args.port}"
    install_fakes(base_url)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    log.info("upload page: %s  (n8n webhook: %s)", base_url, os.environ["N8N_WEBHOOK_URL"])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
