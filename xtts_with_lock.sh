#!/bin/bash
LOCK_FILE="/tmp/xtts.lock"
TIMEOUT=180  # 3 minutes

# Wait for lock
for i in $(seq 1 $TIMEOUT); do
    if mkdir "$LOCK_FILE" 2>/dev/null; then
        break
    fi
    sleep 1
done

if [[ ! -d "$LOCK_FILE" ]]; then
    echo "Could not acquire XTTS lock after $TIMEOUT seconds"
    exit 1
fi

# Run XTTS with cleanup
trap 'rmdir "$LOCK_FILE" 2>/dev/null' EXIT
exec /opt/ai-radio/dj_enqueue_xtts.sh "$@"
