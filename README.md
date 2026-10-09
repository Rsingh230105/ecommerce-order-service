# Order Service

Order Service owns order creation and order persistence. It gets product prices from Product Service and records an event in a transactional outbox so an order can be committed even when SNS or Inventory is temporarily unavailable.

## Responsibilities

- Create, list, retrieve, update, and delete orders under `/orders`.
- Verify products and retrieve their current prices from Product Service.
- Calculate and store an order's price and total at creation time.
- Persist the order and `ORDER_CREATED` outbox event in one PostgreSQL transaction.
- Run a separate worker process that publishes pending outbox events to SNS.
- Listen on port `8001` for the API.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/orders` | Create an order |
| `GET` | `/orders` | List orders |
| `GET` | `/orders/{order_id}` | Retrieve an order |
| `PUT` | `/orders/{order_id}` | Update an order |
| `DELETE` | `/orders/{order_id}` | Delete an order |
| `GET` | `/health` | Process health check |

Open `/docs` on the service URL for interactive API documentation.

## Order event reliability

The API does not call SNS while handling `POST /orders`. It inserts both the order and outbox row in a single transaction. The publisher (`python -m app.outbox_publisher`) claims rows using row locks and lease fields, sends the stored JSON payload to the configured SNS topic, and marks successful rows as published. Failed sends are returned to `PENDING` with error details for a later retry.

This is at-least-once delivery. If the publisher sends to SNS and stops before it can mark the outbox row `PUBLISHED`, the event may be sent again after its lease expires. Inventory's event-ID idempotency makes that duplicate safe for stock updates.

## Run locally with Docker Compose

1. Copy `.env.example` to `.env` and set a local-only database password.
2. From this directory, run:

   ```powershell
   docker compose up --build
   ```

3. Open `http://localhost:8001/docs`.

The local PostgreSQL container maps to host port 5434. The API uses `postgres:5432` inside Docker. Order-to-Product calls require Product Service to be reachable at the configured `PRODUCT_SERVICE_URL`; the separate Compose project does not automatically provide an all-services network/service alias. Configure a shared Docker network and a matching Product Service alias for local integration testing.

## Configuration

The API uses:

- `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME`
- `PRODUCT_SERVICE_URL` (defaults in the application to `http://product-service:8000`)

The publisher additionally needs:

- `AWS_REGION` (defaults to `ap-south-1`)
- `SNS_ORDER_EVENTS_TOPIC_ARN`
- AWS credentials supplied using the normal AWS SDK credential chain (in AWS, its ECS task role)
- The same database settings as the API

Do not commit `.env`, AWS credentials, or secret values.

## Migrations and startup order

The API container's [entrypoint](./entrypoint.sh) runs `alembic upgrade head` before starting Uvicorn. The Publisher task overrides the image entrypoint and runs only `python -m app.outbox_publisher`; it does **not** run migrations.

In an ECS deployment, the API and publisher are separate services and can start concurrently. Therefore, do not rely on a particular API task starting first as a robust migration deployment process. The next deployment improvement is a one-off migration task or deployment pipeline step with explicit success gating before API and publisher rollout. Keep migrations backward-compatible with the currently running service versions.

## Tests

From this directory, with the service dependencies installed:

```powershell
python -m pytest
```

Some API CRUD tests require a reachable PostgreSQL database. Outbox-focused tests use controlled test sessions. Run the suite against a disposable test database and check any database-dependent test setup before interpreting the result.

Order API tests mock Product Service responses; they do not require a running Product Service.

## How it connects to the application

- ALB forwards `/orders` and `/orders/*` to the Order API on port 8001.
- The API calls Product Service over ECS Service Connect at `http://product-service:8000`.
- The outbox publisher uses SNS; SNS fans out the event to Inventory's SQS queue.
- In AWS, API and publisher tasks use IAM task roles for their respective permissions.

## Next improvements

1. Introduce a gated, one-off migration task before ECS service rollout.
2. Add outbox retry/backoff policy, operational metrics, and alarms for old pending rows and repeated failures.
3. Test publisher crash/retry behavior and lease-expiry scenarios against PostgreSQL, in addition to unit tests.
4. Add order validation and lifecycle rules (for example, make clear whether updates to order quantity should emit a corresponding inventory event).

## CI and development deployment

The `CI and deploy (dev)` GitHub Actions workflow runs on pull requests and pushes to `dev`, and can be manually dispatched for an initial deployment or redeployment. It starts a disposable PostgreSQL 16 test database, runs the test suite, and verifies the Docker image builds.

On pushes or a manual run on branch `dev`, the AWS deploy job runs only when the repository Actions variable `AWS_ECR_ECS_DEPLOY_ENABLED` is set to the string `true`. Before enabling it:

1. Apply the development infrastructure so the ECR repository, Order ECS service, and outbox publisher service exist.
2. Configure GitHub repository variables `AWS_REGION` (`ap-south-1`) and `AWS_DEPLOY_ROLE_ARN`.
3. Configure a dedicated AWS role to trust `token.actions.githubusercontent.com`, with audience `sts.amazonaws.com` and subject `repo:Rsingh230105/ecommerce-order-service:ref:refs/heads/dev`.
4. Limit that role to ECR push operations for `ecommerce-dev-order-service`, ECS describe/register/update operations for the dev cluster and Order services, and `iam:PassRole` only for the existing ECS task roles (condition `iam:PassedToService=ecs-tasks.amazonaws.com`). `ecr:GetAuthorizationToken` and task-definition registration may require `Resource: "*"`.
5. Set `AWS_ECR_ECS_DEPLOY_ENABLED=true`.

Each deployment publishes an immutable image tagged with the Git commit SHA, applies it to the latest Terraform task-definition family revisions, and waits for both the Order API and outbox publisher ECS services to become stable. Terraform ignores only each deployed task-definition revision so a later apply does not roll back a CI deployment; Terraform still owns the task-definition templates and each service's other settings. If the job is not enabled, CI still runs but no AWS access or deployment is attempted. This workflow targets development only; it does not deploy production.
