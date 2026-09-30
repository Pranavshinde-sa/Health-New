import json
import logging
import os
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import boto3
from botocore.exceptions import ClientError

import config


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("health-agent")


class HealthAgent:
    def __init__(self):
        self.ecs = boto3.client("ecs", region_name=config.AWS_REGION)
        self.logs = boto3.client("logs", region_name=config.AWS_REGION)
        self.cloudwatch = boto3.client(
            "cloudwatch", region_name=config.AWS_REGION
        )
        self.state = self._load_state()

    def _load_state(self):
        try:
            with open(config.STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"ecs_tasks": {}, "lambda_last_error_count": 0}

    def _save_state(self):
        directory = os.path.dirname(config.STATE_FILE)
        if directory:
            os.makedirs(directory, exist_ok=True)

        temp = f"{config.STATE_FILE}.tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)
        os.replace(temp, config.STATE_FILE)

    def _send_email(self, subject, body):
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = config.ALERT_FROM
        message["To"] = config.ALERT_TO
        message.set_content(body)

        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            smtp.send_message(message)

        log.info("Alert email sent: %s", subject)

    def _alert_once(self, key, subject, body):
        alerts = self.state.setdefault("alerts", {})
        if alerts.get(key):
            return

        try:
            self._send_email(subject, body)
            alerts[key] = True
            self._save_state()
        except Exception:
            log.exception("Could not send alert email")

    def _get_recent_stopped_ecs_tasks(self):
        response = self.ecs.list_tasks(
            cluster=config.ECS_CLUSTER,
            desiredStatus="STOPPED",
            family=config.ECS_TASK_DEFINITION.split(":")[0],
        )
        arns = response.get("taskArns", [])
        if not arns:
            return []

        tasks = self.ecs.describe_tasks(
            cluster=config.ECS_CLUSTER,
            tasks=arns,
            include=["TAGS"],
        ).get("tasks", [])

        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=config.ECS_LOOKBACK_MINUTES
        )

        recent = []
        for task in tasks:
            stopped_at = task.get("stoppedAt")
            if not stopped_at:
                continue
            if stopped_at >= cutoff:
                recent.append(task)

        return recent

    def _get_ecs_logs(self, task, start_ms):
        try:
            response = self.logs.filter_log_events(
                logGroupName=config.ECS_LOG_GROUP,
                startTime=start_ms,
                limit=100,
            )
            return [
                event.get("message", "")
                for event in response.get("events", [])
            ]
        except ClientError as exc:
            log.warning("ECS CloudWatch log read failed: %s", exc)
            return []

    @staticmethod
    def _container_result(task):
        containers = task.get("containers", [])
        if not containers:
            return None

        container = containers[0]
        return {
            "name": container.get("name"),
            "status": container.get("lastStatus"),
            "exit_code": container.get("exitCode"),
            "reason": container.get("reason"),
        }

    def check_ecs(self):
        tasks = self._get_recent_stopped_ecs_tasks()

        for task in tasks:
            task_arn = task["taskArn"]
            if task_arn in self.state["ecs_tasks"]:
                continue

            result = self._container_result(task)
            if not result:
                continue

            stopped_at = task.get("stoppedAt")
            start_ms = int(
                (stopped_at - timedelta(minutes=5)).timestamp() * 1000
            )
            logs = self._get_ecs_logs(task, start_ms)

            exit_code = result["exit_code"]
            status = "HEALTHY" if exit_code == 0 else "UNHEALTHY"

            record = {
                "task_arn": task_arn,
                "status": status,
                "exit_code": exit_code,
                "stopped_at": stopped_at.isoformat(),
            }
            self.state["ecs_tasks"][task_arn] = record
            self._save_state()

            log.info(
                "ECS task=%s status=%s exit_code=%s",
                task_arn,
                status,
                exit_code,
            )

            if status == "UNHEALTHY":
                evidence = "\n".join(logs[-20:]) or "No CloudWatch log evidence found."
                body = (
                    f"ECS task failed.\n\n"
                    f"Cluster: {config.ECS_CLUSTER}\n"
                    f"Task: {task_arn}\n"
                    f"Container: {result['name']}\n"
                    f"Exit code: {exit_code}\n"
                    f"Reason: {result['reason']}\n\n"
                    f"Recent CloudWatch logs:\n{evidence}"
                )
                self._alert_once(
                    f"ecs:{task_arn}",
                    f"[Health Agent] ECS task failed: {result['name']}",
                    body,
                )

    def _lambda_error_count(self):
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=config.LAMBDA_LOOKBACK_MINUTES)

        response = self.cloudwatch.get_metric_statistics(
            Namespace="AWS/Lambda",
            MetricName="Errors",
            Dimensions=[
                {
                    "Name": "FunctionName",
                    "Value": config.LAMBDA_FUNCTION_NAME,
                }
            ],
            StartTime=start,
            EndTime=end,
            Period=60,
            Statistics=["Sum"],
        )

        return int(sum(point.get("Sum", 0) for point in response.get("Datapoints", [])))

    def _get_lambda_logs(self):
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int(
            (
                datetime.now(timezone.utc)
                - timedelta(minutes=config.LAMBDA_LOOKBACK_MINUTES)
            ).timestamp()
            * 1000
        )

        try:
            response = self.logs.filter_log_events(
                logGroupName=config.LAMBDA_LOG_GROUP,
                startTime=start_ms,
                endTime=end_ms,
                limit=200,
            )
            return [
                event.get("message", "")
                for event in response.get("events", [])
            ]
        except ClientError as exc:
            log.warning("Lambda CloudWatch log read failed: %s", exc)
            return []

    def check_lambda(self):
        error_count = self._lambda_error_count()
        previous = int(self.state.get("lambda_last_error_count", 0))

        if error_count > previous:
            logs = self._get_lambda_logs()
            relevant = [
                line
                for line in logs
                if any(
                    token in line.lower()
                    for token in ("error", "exception", "traceback", "timeout")
                )
            ]

            body = (
                f"Lambda execution errors detected.\n\n"
                f"Function: {config.LAMBDA_FUNCTION_NAME}\n"
                f"Errors in lookback window: {error_count}\n\n"
                f"Relevant CloudWatch logs:\n"
                f"{chr(10).join(relevant[-30:]) or 'No matching log lines found.'}"
            )

            alert_key = (
                f"lambda:{config.LAMBDA_FUNCTION_NAME}:"
                f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M')}"
            )
            self._alert_once(
                alert_key,
                f"[Health Agent] Lambda execution error: {config.LAMBDA_FUNCTION_NAME}",
                body,
            )

        self.state["lambda_last_error_count"] = error_count
        self._save_state()

        log.info(
            "Lambda=%s errors_last_%sm=%s",
            config.LAMBDA_FUNCTION_NAME,
            config.LAMBDA_LOOKBACK_MINUTES,
            error_count,
        )

    def run_once(self):
        self.check_ecs()
        self.check_lambda()

    def run_forever(self):
        log.info("Health Agent started")
        while True:
            try:
                self.run_once()
            except Exception:
                log.exception("Health Agent cycle failed")

            time.sleep(config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    HealthAgent().run_forever()
