# AWS deployment

`template.yaml` creates a Python 3.12 gateway, an HTTP API with the nine contract
routes, one DynamoDB table, and scheduled provider-deletion and ClickHouse delivery
workers. This is
deployment configuration; local validation does not establish live AWS or sponsor
integration, or prove that the deployed IAM policies permit/deny the expected
operations. Emulator storage checks are also local evidence only.

## Configuration

Create two Secrets Manager secrets in the deployment account and region, using
the AWS-managed Secrets Manager encryption key. Supply their ARNs through the
required `GatewayConfigSecretArn` and `ClickHouseSecretArn` stack parameters.
Secret values do not belong in the template, CLI arguments, or repository.

The gateway JSON secret contains:

| Field | Value |
| --- | --- |
| `admin_token_sha256` | 64-character SHA-256 hex digest of the trusted harness's high-entropy admin bearer token |
| `report_base_url` | HTTPS public API base URL, without a trailing slash |
| `senso.api_key` | Senso backend API key |
| `senso.folder_id` | Protected Senso knowledge-base folder node ID |
| `senso.base_url` | Optional official Senso API origin; see the bridge's configuration validation |

The gateway passes the `senso` object to
`gateway.senso_bridge:create_provider`, which wraps Florian's
`adapters/senso_adapter.py`. Missing configuration or adapter availability fails
closed. No mock provider is configured.

The worker JSON secret contains:

| Field | Value |
| --- | --- |
| `url` | ClickHouse HTTPS root URL, with no userinfo, query, or fragment |
| `username` | ClickHouse ingestion user |
| `password` | That user's password |
| `database` | Database name matching `[A-Za-z_][A-Za-z0-9_]{0,63}` |

Apply `analytics/schema.sql` to ClickHouse using the trusted operator account.
Give the worker's ClickHouse user only INSERT access to `gateway_events` in that
database.

| Function | Handler | Environment |
| --- | --- | --- |
| Gateway | `gateway.runtime.lambda_handler` | `GATEWAY_TABLE`, `GATEWAY_CONFIG_SECRET_ARN`, `SENSO_ADAPTER_FACTORY=gateway.senso_bridge:create_provider` |
| Gateway maintenance | `gateway.runtime.maintenance_handler` | Same gateway environment and role; retries deletion of revoked provider documents |
| Analytics | `analytics.worker.lambda_handler` | `GATEWAY_TABLE`, `CLICKHOUSE_SECRET_ARN` |

The first deployment determines `GatewayBaseUrl`. Set `report_base_url` to that
output before using the gateway; the operator can update the secret after stack
creation. Use `GATEWAY_BASE_URL` for agent clients. Only the trusted harness keeps
the admin bearer. Each agent receives its own run token.

## Build and deploy

Install AWS SAM CLI and Docker; the container build targets Linux/Python 3.12.
The root runtime requirements and real adapter must be present before building.
Run from the repository root:

```sh
sam validate --lint --region us-east-1 --template-file infra/template.yaml
.venv/bin/python infra/build.py
sam deploy --template-file build/sam/template.yaml \
  --stack-name agent-memory-gateway --profile default --region us-east-1 --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
  GatewayConfigSecretArn="$GATEWAY_CONFIG_SECRET_ARN" \
  ClickHouseSecretArn="$CLICKHOUSE_SECRET_ARN" \
  EnableScheduledWorkers=false
```

`EnableScheduledWorkers` defaults to `false`, so neither schedule invokes a worker
before sponsor setup. Once both secret values and the ClickHouse schema are
ready, update the same stack with `EnableScheduledWorkers=true`. The gateway
continues to deny reads immediately after local revocation while the maintenance
worker retries provider deletion. `--resolve-s3` creates SAM's deployment bucket
if none exists; the command above changes cloud resources.

`build.py` stages Python gateway/analytics source, the Senso adapter, and runtime
requirements in a temporary directory. It excludes local secrets, agent/demo
code, tests, and fixtures from the package. Deploy the built template; a direct
build or deployment from `CodeUri: ../` would package the working directory.
The trusted bootstrap operation runs separately to register sources and
principals; it is not an agent endpoint.

## State and IAM boundary

DynamoDB items have string `pk` (record kind), string `sk` (record key), and
`record` (the application dictionary, including integer `_rev`). Store reads are
strongly consistent; lists use paginated partition queries. Conditional writes
protect revisions. Candidate and idempotency reservations use an atomic
transaction. The table has encryption and point-in-time recovery enabled and is
retained when the stack is deleted or the table is replaced.

The gateway role can get, put, and query this table and read only the gateway
configuration secret. The worker can perform those same record operations only
with `pk = event` and can read only the ClickHouse secret. It cannot scan the
table or read private memory/source partitions. Neither role grants agent
credentials or assumes an agent role. Agents must run without AWS/backend
credentials or access to gateway files.

The gateway and maintenance Lambda timeouts are 25 seconds; HTTP integrations
allow 29 seconds.
Provider clients must keep their own calls bounded within that budget. Analytics
runs once per minute when enabled, with a 60-second limit. Maintenance also runs
once per minute when enabled. No function reserves concurrency: the current
deployment account's regional quota is 10, below AWS's required unreserved
concurrency pool for
[reservations](https://docs.aws.amazon.com/lambda/latest/api/API_PutFunctionConcurrency.html).
Failed delivery leaves metadata pending for the next run; delivery is
at-least-once, workers may overlap, and consumers deduplicate by `event_id`.

HTTP API access logging and tracing are not enabled. Lambda log groups retain
seven days of logs, with application level ERROR and system level WARN. The
handlers must still avoid logging request bodies, tokens, raw queries, source
text, and provider exceptions; log-level filtering is not redaction.

The DynamoDB partition restriction follows AWS's
[fine-grained access policy guidance](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/specifying-conditions.html).
Routes and timeout properties use the
[SAM HTTP API event contract](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-property-function-httpapi.html).
