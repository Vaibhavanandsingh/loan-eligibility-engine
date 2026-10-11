"""Run a local CSV through the same ingestion code the Lambda uses.

    python scripts/local_ingest.py data/users.csv            # ingest only
    python scripts/local_ingest.py data/users.csv --trigger  # then call the n8n webhook

Reads DB settings from .env (see .env.example). Skips S3 entirely: the file is
streamed from disk instead of from an S3 object body.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_env_file(path: Path) -> None:
    """Minimal .env reader: KEY=value lines, '#' comments. Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--trigger", action="store_true", help="call the n8n webhook afterwards")
    parser.add_argument("--chunk-size", type=int, default=2000)
    args = parser.parse_args()

    load_env_file(ROOT / ".env")
    os.environ.setdefault("DB_SSLMODE", "disable")

    from src.lib import ingest  # imported after env is set
    from src.lib.n8n_client import trigger_matching

    batch_id = str(uuid.uuid4())
    ingest.start_batch(batch_id, f"local/{args.csv_path.name}")
    rejects: list[str] = []

    started = time.perf_counter()
    with args.csv_path.open(encoding="utf-8-sig", newline="") as fh:
        try:
            result = ingest.ingest_csv(
                fh,
                batch_id,
                chunk_size=args.chunk_size,
                on_reject=lambda line, _values, reason: rejects.append(f"line {line}: {reason}"),
            )
        except ingest.BatchFormatError as exc:
            ingest.set_batch_status(batch_id, "FAILED", str(exc))
            print(f"File rejected: {exc}")
            return 1
    ingest.finish_batch(batch_id, result)
    elapsed = time.perf_counter() - started

    print(f"batch_id     {batch_id}")
    print(f"rows read    {result.total_rows:,}")
    print(f"saved        {result.valid_rows:,}")
    print(f"rejected     {result.invalid_rows:,}")
    print(f"time         {elapsed:.2f}s  ({result.total_rows / max(elapsed, 1e-9):,.0f} rows/s)")
    for line in rejects[:10]:
        print(f"  {line}")
    if len(rejects) > 10:
        print(f"  ... and {len(rejects) - 10} more")

    if args.trigger:
        ok = trigger_matching(batch_id, result.valid_rows)
        print("n8n webhook  " + ("accepted" if ok else "FAILED (batch marked WEBHOOK_PENDING)"))
        if not ok:
            ingest.set_batch_status(batch_id, "WEBHOOK_PENDING")
    return 0


if __name__ == "__main__":
    sys.exit(main())
