#!/bin/sh
set -eu

# 以 root 启动时修正数据目录属主，再降权为 dashboard（UID 10001）运行。
if [ "$(id -u)" = 0 ]; then
  data="${DATA_DIR:-/data}"
  find "$data" -xdev \( ! -user 10001 -o ! -group 10001 \) -exec chown -h 10001:10001 {} +
  chmod 700 "$data"
  export HOME=/home/dashboard
  exec setpriv --reuid=10001 --regid=10001 --clear-groups \
    --inh-caps=-all -- "$0" "$@"
fi

# No credentials or live SQLite connections have been initialized yet.
# Journaled restore is performed offline, before Uvicorn imports app.security.
python -m app.restore_boot
exec "$@"
