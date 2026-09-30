import os


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


AWS_REGION = env("AWS_REGION", "ap-south-1")

ECS_CLUSTER = env("ECS_CLUSTER", required=True)
ECS_TASK_DEFINITION = env("ECS_TASK_DEFINITION", required=True)
ECS_LOG_GROUP = env("ECS_LOG_GROUP", required=True)

LAMBDA_FUNCTION_NAME = env("LAMBDA_FUNCTION_NAME", required=True)
LAMBDA_LOG_GROUP = env(
    "LAMBDA_LOG_GROUP",
    f"/aws/lambda/{LAMBDA_FUNCTION_NAME}",
)

POLL_INTERVAL_SECONDS = int(env("POLL_INTERVAL_SECONDS", "60"))
ECS_LOOKBACK_MINUTES = int(env("ECS_LOOKBACK_MINUTES", "15"))
LAMBDA_LOOKBACK_MINUTES = int(env("LAMBDA_LOOKBACK_MINUTES", "15"))

SMTP_HOST = env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_USERNAME = env("SMTP_USERNAME", required=True)
SMTP_PASSWORD = env("SMTP_PASSWORD", required=True)
ALERT_FROM = env("ALERT_FROM", SMTP_USERNAME)
ALERT_TO = env("ALERT_TO", required=True)

STATE_FILE = env("STATE_FILE", "/tmp/health-agent-state.json")
