"""GET /upload-url: hand the browser a short-lived link to PUT one CSV into S3.

The browser never holds AWS credentials. This function holds them, creates the
batch record, and returns a presigned URL that allows exactly one action
(PUT) on exactly one key (uploads/<batch_id>.csv) for UPLOAD_URL_TTL seconds.
The file bytes then go straight to S3 and never pass through API Gateway.
"""

from __future__ import annotations

import logging
import os
import uuid

import boto3
from botocore.config import Config

from src.lib import db
from src.lib.http import configure_logging, json_response

configure_logging()
log = logging.getLogger(__name__)

REGION = os.environ.get("AWS_REGION", "ap-south-1")
UPLOAD_URL_TTL = int(os.environ.get("UPLOAD_URL_TTL", "300"))
CONTENT_TYPE = "text/csv"

# Sign against the regional endpoint. A presigned URL for the global endpoint
# can get a 307 redirect for a new bucket, and browsers fail that redirect on CORS.
_s3 = boto3.client(
    "s3",
    region_name=REGION,
    endpoint_url=f"https://s3.{REGION}.amazonaws.com",
    config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)


def handler(event: dict, context: object) -> dict:
    params = event.get("queryStringParameters") or {}
    original_name = (params.get("filename") or "upload.csv")[:255]
    if not original_name.lower().endswith(".csv"):
        return json_response(400, {"error": "Only .csv files can be uploaded."})

    batch_id = str(uuid.uuid4())
    key = f"uploads/{batch_id}.csv"

    # Record the batch first, so the status endpoint knows it even before the upload lands.
    with db.transaction() as cur:
        cur.execute(
            """INSERT INTO upload_batches (batch_id, s3_key, original_name, status)
               VALUES (%s, %s, %s, 'PENDING_UPLOAD')""",
            (batch_id, key, original_name),
        )

    upload_url = _s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": os.environ["UPLOAD_BUCKET"],
            "Key": key,
            # Signed into the URL: the browser must send this exact Content-Type.
            "ContentType": CONTENT_TYPE,
        },
        ExpiresIn=UPLOAD_URL_TTL,
        HttpMethod="PUT",
    )

    log.info("batch %s: issued upload URL for %s (%s)", batch_id, key, original_name)
    return json_response(
        200,
        {
            "batch_id": batch_id,
            "upload_url": upload_url,
            "method": "PUT",
            "headers": {"Content-Type": CONTENT_TYPE},
            "expires_in": UPLOAD_URL_TTL,
        },
    )
