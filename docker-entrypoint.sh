#!/bin/sh
set -e
# Apply pending migrations before starting (idempotent).
alembic upgrade head
exec "$@"
