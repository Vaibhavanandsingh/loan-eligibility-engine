"""Write the emails Workflow C built to HTML files, to open in a browser.

    python scripts/preview_email.py                 # latest batch, local database
    python scripts/preview_email.py --aws           # latest batch, RDS (RDS_HOST in .env)
    python scripts/preview_email.py --batch <uuid>

Files go to previews/ (git-ignored), one per email, named after the recipient.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from local_ingest import load_env_file  # noqa: E402  (same folder)

LATEST_BATCH_SQL = "SELECT batch_id FROM email_outbox ORDER BY created_at DESC LIMIT 1"
EMAILS_SQL = """
SELECT recipient, original_email, mode, status, subject, html, created_at
  FROM email_outbox
 WHERE batch_id = %s
 ORDER BY recipient
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", help="batch id (default: the most recent batch with emails)")
    parser.add_argument("--aws", action="store_true", help="read from RDS instead of the local database")
    args = parser.parse_args()

    load_env_file(ROOT / ".env")
    if args.aws:
        os.environ.update({
            "DB_HOST": os.environ["RDS_HOST"], "DB_PORT": "5432",
            "DB_PASSWORD": os.environ["RDS_MASTER_PASSWORD"], "DB_SSLMODE": "require",
        })
    from src.lib import db  # imported after the connection settings are final

    with db.transaction() as cur:
        batch_id = args.batch
        if not batch_id:
            cur.execute(LATEST_BATCH_SQL)
            row = cur.fetchone()
            if not row:
                print("No emails in email_outbox yet.")
                return 1
            batch_id = row[0]
        cur.execute(EMAILS_SQL, (str(batch_id),))
        emails = cur.fetchall()

    out_dir = ROOT / "previews"
    out_dir.mkdir(exist_ok=True)
    print(f"batch {batch_id}: {len(emails)} email(s)")
    for recipient, original, mode, status, subject, html, created in emails:
        path = out_dir / f"{re.sub(r'[^a-z0-9+]+', '_', recipient.lower())}.html"
        path.write_text(html, encoding="utf-8")
        print(f"  {status:<8} to {recipient}  (for {original})  \"{subject}\"\n           {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
