# Online Cinema FLEX

FastAPI backend for six FLEX features: accounts/authentication, movie catalog, favorites, cart, orders, and Stripe test-mode payments. Full-time-only workflow requirements and profile/avatar storage, MinIO, comments, reactions, ratings, recommendations, refunds, streaming, and EC2 are out of scope.

## Stack and architecture

- Python 3.12, Poetry, FastAPI, Pydantic v2
- SQLAlchemy 2 async, Alembic, PostgreSQL 16
- JWT access/refresh tokens, Argon2 passwords, opaque activation/reset tokens
- Celery + Redis for email and expired activation-token cleanup; MailHog for local SMTP
- Stripe Checkout in test mode; signed webhook processing
- `app/core`, `models`, `schemas`, `services`, `api/routes`, and `tasks`; PostgreSQL-backed integration tests

## Run locally

1. Copy `.env.example` to `.env`. Replace all placeholder values locally. Use a strong `JWT_SECRET_KEY`, a Stripe **test** secret and webhook signing secret; never commit `.env`.
2. Start the stack:

   ```sh
   docker compose up -d --build
   ```

   The API container applies Alembic migrations before starting Uvicorn. PostgreSQL, Redis, Celery worker/beat, and MailHog are also started.
3. Check readiness:

   ```sh
   curl http://localhost:8000/health
   ```

   Expected: `{"status":"ok","database":"ok"}`. API: `http://localhost:8000`; MailHog UI: `http://localhost:8025`.
4. Apply migrations manually when needed: `docker compose exec api poetry run alembic upgrade head`.

5. Create an administrator and demo catalog data:

   ```sh
   docker compose exec api poetry run python -m app.cli create-user --email admin@example.com --password "ChangeMe-Admin1!" --role ADMIN
   docker compose exec api poetry run python -m app.cli seed-catalog
   ```

   `create-user` creates an already active account (roles `USER`, `MODERATOR`, `ADMIN`) and enforces the password policy. `seed-catalog` is idempotent: it adds genres, stars, directors, certifications and 7 movies, skipping movies that already exist.

The app and integration tests use PostgreSQL; SQLite is not supported. Compose's `db` hostname is for container-to-container access.

## Authentication and API documentation

All business routes are under `/api/v1`. Register with `POST /api/v1/auth/register`, activate using the link delivered to MailHog, then use `POST /api/v1/auth/login` to receive access and refresh JWTs. Send protected requests with `Authorization: Bearer <access_token>`. `POST /refresh` replaces an access token; `POST /logout` revokes the refresh token. Password reset mail contains a one-time token for `POST /api/v1/auth/reset-password`.

`/docs` and `/openapi.json` are not public. Open `http://localhost:8000/docs` in a browser: it shows a login prompt (HTTP Basic) where you enter the **email and password of an active account**. API clients may instead send `Authorization: Bearer <access_token>`. Inside Swagger, click **Authorize** and log in with the same email (as `username`) and password to call protected endpoints; every endpoint has a summary and description. `/health` and the explicitly public authentication routes are unauthenticated. Moderator/admin routes are under `/api/v1/catalog-admin` and `/api/v1/admin`; `ADMIN` inherits moderator permissions. The first administrator is created with `python -m app.cli create-user ... --role ADMIN` (see *Run locally*); further roles can then be granted with `PATCH /api/v1/admin/users/{user_id}/group`.

### Main route groups

| Route group | Behavior |
| --- | --- |
| `/api/v1/auth` | Register, activate/resend, login, refresh/logout, change/forgot/reset password |
| `/api/v1/movies` | Public movie search, filters, sort, pagination, detail, genres |
| `/api/v1/catalog-admin` | Moderator/admin movie and reference-data CRUD |
| `/api/v1/admin` | ADMIN: list users (`email` filter), change a user's group, activate a user. MODERATOR+: all orders and payments (filters `user_id`, `status`, `date_from`, `date_to`, pagination) and any user's cart |
| `/api/v1/favorites` | Add/remove/list the caller's favorite movies |
| `/api/v1/cart` | Read, add/remove items, clear cart |
| `/api/v1/orders` | Create/list/read/cancel pending orders; prices are snapshotted |
| `/api/v1/payments` | Payment history, Stripe Checkout session, signed webhook, success/cancel redirect pages |

Movie deletion returns `409` when a cart or order references the movie. Duplicate favorite/cart entries and already purchased/pending items return `409`. Creating an order rechecks cart contents and prices; unavailable items are returned with reasons, and an all-excluded cart returns a structured `409` detail.

### Example requests

```sh
# Register, then open the activation link from MailHog (http://localhost:8025)
curl -X POST localhost:8000/api/v1/auth/register -H "Content-Type: application/json"   -d '{"email": "me@example.com", "password": "StrongPassword9!"}'

# Log in and keep the access token
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login -H "Content-Type: application/json"   -d '{"email": "me@example.com", "password": "StrongPassword9!"}' | jq -r .access_token)

# Search the catalog and list genres with movie counts
curl "localhost:8000/api/v1/movies/?search=nolan&sort=imdb&descending=true&per_page=5"
curl localhost:8000/api/v1/movies/genres

# Cart -> order -> Stripe checkout -> payment history
curl -X POST localhost:8000/api/v1/cart/items/<movie_id> -H "Authorization: Bearer $TOKEN"
curl -X POST localhost:8000/api/v1/orders -H "Authorization: Bearer $TOKEN"
curl -X POST localhost:8000/api/v1/payments/checkout/<order_id> -H "Authorization: Bearer $TOKEN"
curl localhost:8000/api/v1/payments -H "Authorization: Bearer $TOKEN"

# Moderator: paid orders of one user in October 2026
curl "localhost:8000/api/v1/admin/orders?user_id=<id>&status=paid&date_from=2026-10-01T00:00:00Z&date_to=2026-10-31T23:59:59Z"   -H "Authorization: Bearer $MODERATOR_TOKEN"
```

## Differences from the reference database schema

| Difference | Reason |
| --- | --- |
| UUID primary keys for users, movies, carts, orders and payments (integers for reference tables) | IDs appear in URLs and webhooks; UUIDs are not enumerable and can be generated before insert. |
| Activation and password-reset tokens are stored as SHA-256 hashes; refresh tokens are tracked by `jti` | A leaked database does not expose usable tokens. |
| `PaymentItem.movie_id` instead of a link to `OrderItem` | A payment item stays meaningful on its own, and the order/movie pair is still unique per order. |
| `Movie.certification_id` is nullable | Movies can be created before their certification is known; deletion of a used certification is still blocked. |
| No `UserProfile` table and no `updated_at` columns | The FLEX scope has no profile endpoints; audit history is covered by `created_at` and payment records. |

## Stripe test mode

Configure only Stripe test-mode credentials. Start a local forwarding listener, for example:

```sh
stripe listen --forward-to localhost:8000/api/v1/payments/webhook
```

Set `STRIPE_WEBHOOK_SECRET` to the listener's signing secret. `STRIPE_SUCCESS_URL` and `STRIPE_CANCEL_URL` default to `/api/v1/payments/success` and `/cancel`, which show the result to the customer (the cancel page suggests another payment method). A successful payment for an order that was canceled meanwhile is still recorded and the order marked paid, so no captured money is lost. Checkout uses Stripe's card payment method. Create an order, call `POST /api/v1/payments/checkout/{order_id}`, then complete/cancel Checkout in Stripe's test environment. The webhook verifies the raw-body signature, checks order owner/amount, and processes duplicate events idempotently. If Stripe reports `checkout.session.completed` while `payment_status` is still `unpaid`, the order remains pending until an `async_payment_succeeded` event arrives. Automated tests mock Checkout session creation; signed webhook transitions and failure boundaries run against PostgreSQL. No real card charges are used.

## Tests and quality checks

Start Compose first, then run from the project directory:

```sh
poetry install --with dev
poetry run ruff check .
poetry run ruff format --check .
poetry run mypy app
poetry run pytest --cov=app --cov-report=term-missing
```

Integration tests use the dedicated `cinema_test` database and `TEST_DATABASE_URL` (default `postgresql+asyncpg://cinema:changeme@localhost:5432/cinema_test`). They reset only that database's `public` schema before applying migrations; do not point this variable at a database containing user data. Scenarios cover auth expiry/reuse and password revocation; catalog multi-field search, filters, sorting, pagination, role access and reference deletion; favorite isolation; cart/order ownership, stale prices and concurrent order creation; and Stripe signatures, wrong owner/amount, cancellation, delayed success and event idempotency. The CI PostgreSQL service provides the dedicated test database.

Before running integration tests locally against the Compose database, create the dedicated test database once:

```sh
docker compose exec db createdb -U cinema cinema_test
```

## Branches and commits

Feature branches: `feat/project-foundation`, `feat/accounts-auth`, `feat/catalog`, `feat/favorites`, `feat/cart`, `feat/orders`, `feat/stripe-payments`, and `chore/docs-ci`. Use small conventional commits (`feat: ...`, `test: ...`, `docs: ...`, `ci: ...`). Do not commit local secrets, `.env`, coverage output, or database dumps.

