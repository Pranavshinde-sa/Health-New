import json
import logging
import os
import time

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    logger.info("Lambda demo started")
    logger.info("Request ID: %s", context.aws_request_id)

    fail = False
    if isinstance(event, dict):
        fail = bool(event.get("fail", False))

    if os.getenv("FORCE_FAILURE", "false").lower() == "true":
        fail = True

    time.sleep(1)

    if fail:
        logger.error("Deliberate test failure requested")
        raise RuntimeError("Deliberate Health Agent test failure")

    logger.info("Lambda demo completed successfully")

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": "Lambda execution successful",
                "request_id": context.aws_request_id,
            }
        ),
    }
