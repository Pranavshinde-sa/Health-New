import os
import sys
import time
from datetime import datetime, timezone


def log(message):
    print(
        f"{datetime.now(timezone.utc).isoformat()} | {message}",
        flush=True,
    )


log("ECS demo task started")

time.sleep(5)

if os.getenv("FAIL_TASK", "false").lower() == "true":
    log("ERROR | Deliberate test failure")
    sys.exit(1)

log("Processing demo workload...")
time.sleep(5)
log("Demo workload completed successfully")
sys.exit(0)
