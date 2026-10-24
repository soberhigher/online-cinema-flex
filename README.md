# Online Cinema FLEX

Backend for an online cinema (Mate Academy FLEX track): accounts, movie catalog, favorites,
cart, orders and Stripe payments in test mode.

Not included (full-time track only): user profiles and avatars, MinIO, comments, likes,
ratings, recommendations, refunds, streaming, EC2 deployment.

**Stack:** Python 3.12, FastAPI, SQLAlchemy 2 (async) + Alembic, PostgreSQL 16, Celery + Redis,
MailHog, Stripe, Poetry.

```
app/
  api/routes/   HTTP layer
  services/     business logic
  models/       SQLAlchemy models
  schemas/      Pydantic schemas
  tasks/        Celery: emails, cleanup of expired activation tokens
  core/         settings, database, JWT/passwords, rate limiting
migrations/     Alembic
tests/          unit, integration (real PostgreSQL), functional
```

## Running locally

```sh
cp .env.example .env    # then edit it, see below
docker compose up -d --build
curl localhost:8000/health    # {"status":"ok","database":"ok"}
```

In `.env` set at least:

- `JWT_SECRET_KEY` — 32+ random characters. The API refuses to start with the placeholder.
- `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` — **test mode** keys.

Compose starts the API (it runs `alembic upgrade head` first), PostgreSQL, Redis, a Celery
worker and beat, and MailHog. Activation and password reset emails show up in MailHog at
http://localhost:8025.

Create an admin and some demo movies:

```sh
docker compose exec api poetry run python -m app.cli create-user --email admin@example.com --password "ChangeMe-Admin1!" --role ADMIN
docker compose exec api poetry run python -m app.cli seed-catalog
```

`seed-catalog` can be run again safely, it skips movies that already exist.

## API

Everything lives under `/api/v1`. Swagger is at http://localhost:8000/docs, but it is not
public: the browser asks for the email and password of an active account. Inside Swagger use
**Authorize** with the same credentials.

| Prefix | What it does | Access |
| --- | --- | --- |
| `/auth` | register, activate, login, refresh, logout, change/forgot/reset password | public |
| `/movies` | list with search, filters, sorting, pagination; movie details; genres | public |
| `/favorites` | add, remove, list (same filters as `/movies`) | user |
| `/cart` | view, add, remove, clear | user |
| `/orders` | create from cart, list, details, cancel | user |
| `/payments` | history, start Stripe Checkout, webhook, success/cancel pages | user |
| `/catalog-admin` | CRUD for movies, genres, stars, directors, certifications | moderator |
| `/admin/orders`, `/admin/payments`, `/admin/carts/{id}` | all orders and payments with filters, any user's cart | moderator |
| `/admin/users` | list users, change group, activate manually | admin |

Admins have all moderator permissions.

### Auth details

- Passwords: 12–128 characters, upper- and lowercase letters, a digit and a special character.
  Stored with Argon2.
- Access token lives 30 minutes, refresh token 14 days. Every `/refresh` returns a new refresh
  token and revokes the old one. If a revoked refresh token is used again, all sessions of the
  account are closed.
- Changing or resetting the password revokes refresh tokens and makes older access tokens invalid.
- Activation and reset tokens are random strings; only their SHA-256 hashes are stored.
- Login, registration, password reset, resending activation emails and the `/docs` password
  prompt are rate limited per IP (`429` + `Retry-After`). The counter is kept in memory, which
  is fine for the single Uvicorn process Compose runs.

### Orders and payments

- An order stores the price of every movie at the moment it was created.
- When an order is created, movies that were already bought or are in another pending order
  are skipped and returned in `excluded`. If nothing is left, the response is `409`.
- Checkout fails with `409` if prices changed since the order was made — create a new order.
  Calling checkout again while the previous Stripe session is still open returns that session.
- The webhook checks the Stripe signature, the order owner and the amount. Events are processed
  once (by Stripe event id). An expired or failed session cancels the order; an expired *older*
  session is ignored if a newer one exists.
- If the customer pays for an order that was canceled in the meantime, the payment is still
  recorded and the order becomes paid.
- If the paid amount does not match the order, the event is logged and acknowledged without
  touching the order, so it can be checked by hand.

### Example

```sh
curl -X POST localhost:8000/api/v1/auth/register -H "Content-Type: application/json" \
  -d '{"email": "me@example.com", "password": "StrongPassword9!"}'
# open the activation link from MailHog, then:
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login -H "Content-Type: application/json" \
  -d '{"email": "me@example.com", "password": "StrongPassword9!"}' | jq -r .access_token)

curl "localhost:8000/api/v1/movies/?search=nolan&sort=imdb&descending=true&per_page=5"
curl -X POST localhost:8000/api/v1/cart/items/<movie_id> -H "Authorization: Bearer $TOKEN"
curl -X POST localhost:8000/api/v1/orders -H "Authorization: Bearer $TOKEN"
curl -X POST localhost:8000/api/v1/payments/checkout/<order_id> -H "Authorization: Bearer $TOKEN"
```

## Stripe webhooks locally

```sh
stripe listen --forward-to localhost:8000/api/v1/payments/webhook
```

Put the signing secret it prints into `STRIPE_WEBHOOK_SECRET`. Pay with Stripe's test cards.

## Tests

Tests need PostgreSQL. With Compose running, create the test database once:

```sh
docker compose exec db createdb -U cinema cinema_test
```

Then:

```sh
poetry install --with dev
poetry run ruff check . && poetry run ruff format --check .
poetry run mypy app
poetry run pytest --cov=app
```

The database is taken from `TEST_DATABASE_URL`
(default `postgresql+asyncpg://cinema:changeme@localhost:5432/cinema_test`). **Its `public`
schema is dropped before every test**, so never point it at a real database. Stripe calls are
mocked; webhooks are signed the same way Stripe does it. CI runs the same checks and requires
80% coverage.

## Differences from the reference schema

- UUID primary keys for users, movies, carts, orders and payments, so ids in URLs can't be
  guessed. Reference tables (genres etc.) keep integer ids.
- Activation/reset tokens are stored as hashes; refresh tokens are tracked by `jti`.
- `PaymentItem` points to the movie, not to `OrderItem`.
- `Movie.certification_id` is nullable.
- No `UserProfile` table and no `updated_at` columns: profiles are out of FLEX scope.
- `User.password_changed_at` is used to invalidate access tokens.
