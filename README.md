# AWS Health Agent POC

This project is a development POC for monitoring two AWS workload types:

1. **Scheduled ECS Fargate task** launched by EventBridge Scheduler.
2. **Lambda function** invoked manually/API Gateway style.

The Health Agent runs on EC2/ECS and uses:

- ECS APIs for task lifecycle + container exit codes.
- CloudWatch Logs for ECS/Lambda diagnostic logs.
- CloudWatch Metrics for Lambda invocation errors.
- SMTP for email alerts.

## Architecture

```text
                         AWS
                          |
             +------------+------------+
             |                         |
       EventBridge                  API Gateway /
       Scheduler                    manual invoke
             |                         |
             v                         v
       ECS Fargate                   Lambda
             |                         |
             +------------+------------+
                          |
                     CloudWatch
                    /           \
                  Logs         Metrics
                    \           /
                     \         /
                      v       v
                    Health Agent
                    (EC2 / ECS)
                         |
                         v
                       SMTP
                         |
                         v
                       Email
```

## Health model

### ECS scheduled task

The agent does NOT consider `STOPPED` unhealthy because scheduled tasks are expected to stop.

It checks:

- Did the task start?
- Did the task stop?
- Container exit code.
- Stop/container reason.
- Recent CloudWatch Logs for diagnostic evidence.

`exit_code == 0` => successful execution.

`exit_code != 0` => failed execution.

### Lambda

The agent checks:

- CloudWatch `Errors` metric.
- CloudWatch logs for the Lambda.
- Recent `ERROR`, `Exception`, `Traceback`, and similar failure evidence.
- Duration from the Lambda `REPORT` record when present.

A log line containing `[ERROR]` alone is NOT automatically treated as a failed Lambda invocation. The CloudWatch `Errors` metric is the primary execution-failure signal.

## Project structure

```text
health-agent-poc/
├── health-agent/
│   ├── main.py
│   ├── config.py
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── ecs-app/
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
├── lambda-app/
│   └── lambda_function.py
├── infra/
│   ├── ecs-task-definition.json
│   ├── ecs-iam-policy.json
│   ├── health-agent-iam-policy.json
│   └── eventbridge-schedule.json
└── README.md
```

## 1. Deploy the ECS sample

Build and push `ecs-app` to ECR.

Example:

```bash
aws ecr create-repository --repository-name health-agent-ecs-demo

docker build -t health-agent-ecs-demo ./ecs-app

aws ecr get-login-password --region ap-south-1 | \
docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com

docker tag health-agent-ecs-demo:latest \
<ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/health-agent-ecs-demo:latest

docker push \
<ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/health-agent-ecs-demo:latest
```

Create the ECS task definition using `infra/ecs-task-definition.json`.

Update:

- `<ACCOUNT_ID>`
- ECR image URI
- ECS execution role ARN
- ECS task role ARN
- CloudWatch log group

The sample task writes to CloudWatch Logs.

## 2. Schedule the ECS task

Create an EventBridge Scheduler schedule that starts the ECS task.

For the POC, use something frequent such as every 5 minutes.

The exact Scheduler configuration depends on your VPC/subnets/security groups and IAM role. The important target is:

```text
EventBridge Scheduler -> ECS RunTask -> Fargate
```

The ECS task is a **run-to-completion** workload. It starts, does work, and exits.

## 3. Deploy the Lambda sample

The Lambda code is in:

```text
lambda-app/lambda_function.py
```

Create it with:

```bash
aws lambda create-function \
  --function-name health-agent-lambda-demo \
  --runtime python3.12 \
  --handler lambda_function.lambda_handler \
  --role <LAMBDA_EXECUTION_ROLE_ARN> \
  --zip-file fileb://lambda.zip
```

Package it:

```bash
cd lambda-app
zip lambda.zip lambda_function.py
```

The function accepts a test event such as:

```json
{
  "fail": false
}
```

To deliberately fail it:

```json
{
  "fail": true
}
```

CloudWatch Logs are automatically created under:

```text
/aws/lambda/health-agent-lambda-demo
```

## 4. Run the Health Agent

The agent requires AWS credentials with permission to read:

- ECS task information.
- CloudWatch Logs.
- CloudWatch Metrics.

For an EC2 deployment, prefer an **IAM instance profile** rather than access keys.

For an ECS deployment, prefer an **IAM task role**.

Set:

```bash
AWS_REGION=ap-south-1

ECS_CLUSTER=your-cluster
ECS_TASK_DEFINITION=stock-ingestion:1
ECS_LOG_GROUP=/ecs/health-agent-ecs-demo

LAMBDA_FUNCTION_NAME=health-agent-lambda-demo
LAMBDA_LOG_GROUP=/aws/lambda/health-agent-lambda-demo

POLL_INTERVAL_SECONDS=60
ECS_LOOKBACK_MINUTES=15
LAMBDA_LOOKBACK_MINUTES=15

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@example.com
SMTP_PASSWORD=your-smtp-password
ALERT_FROM=your-email@example.com
ALERT_TO=your-alert-recipient@example.com
```

Run locally:

```bash
cd health-agent
pip install -r requirements.txt
python main.py
```

Or build the agent container:

```bash
docker build -t health-agent ./health-agent
```

## 5. Required IAM permissions

The Health Agent needs read-only access for the POC.

See:

```text
infra/health-agent-iam-policy.json
```

Do not put long-lived AWS access keys into the image.

For AWS deployment, attach an IAM role to the EC2 instance or ECS task.

## 6. Testing

### Lambda success

Invoke:

```json
{
  "fail": false
}
```

Expected:

```text
Lambda execution successful
No Lambda error metric increase
```

### Lambda failure

Invoke:

```json
{
  "fail": true
}
```

Expected:

```text
Lambda execution failed
CloudWatch Errors increases
Health Agent sends email
```

### ECS success

The sample ECS application exits with code `0`.

Expected:

```text
Task -> STOPPED
Container exit code -> 0
Health -> HEALTHY
```

### ECS failure

Set:

```text
FAIL_TASK=true
```

in the ECS task environment.

The application exits with code `1`.

Expected:

```text
Task -> STOPPED
Container exit code -> 1
CloudWatch logs -> failure evidence
Health -> UNHEALTHY
Email alert
```

## Important limitation of this POC

The current agent evaluates **observed executions**. It does not yet fully implement cron-expression reconciliation such as:

> "A task was expected at 15:00 but EventBridge never launched it."

That should be a second phase after the basic ECS/Lambda health signals are working.

Also, SMTP credentials should be moved to AWS Secrets Manager or another secret store before production.
