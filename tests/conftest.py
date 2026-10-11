import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Defaults match docker-compose.yml, so `docker compose up -d && pytest` just works.
for key, value in {
    "DB_HOST": "localhost",
    "DB_PORT": "5433",
    "DB_NAME": "loans",
    "DB_USER": "loans",
    "DB_PASSWORD": "loans_local_pw",
    "DB_SSLMODE": "disable",
    "AWS_DEFAULT_REGION": "ap-south-1",
}.items():
    os.environ.setdefault(key, value)
