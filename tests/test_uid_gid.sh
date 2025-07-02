#!/bin/bash
set -e

expected_uid=${PUID:-1000}
expected_gid=${PGID:-1000}
actual_uid=$(id -u)
actual_gid=$(id -g)

echo "UID: $actual_uid"
echo "GID: $actual_gid"

if [ "$actual_uid" != "$expected_uid" ] || [ "$actual_gid" != "$expected_gid" ]; then
  echo "UID/GID mismatch" >&2
  exit 1
fi

touch /app/state_data/test_write
rm /app/state_data/test_write

echo "Permission check passed"
