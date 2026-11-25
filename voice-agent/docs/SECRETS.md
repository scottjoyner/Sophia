# Secret handling

## Mandatory secrets

`voice-agent/docker-compose.yml` refuses to render — so `docker compose up`
never starts — unless these three are set and non-empty, because each one has an
insecure fallback in the code that the compose guard is there to prevent:

| Variable | Protects | Fallback if unset |
|----------|----------|-------------------|
| `SOPHIA_ENROLLMENT_TOKEN` | `POST /voiceprints/enroll` | endpoint is anonymous: anything that can reach port 8765 (any tailnet device) can append clips to a voiceprint |
| `SOPHIA_SESSION_SECRET` | HMAC key for the `sophia_session` cookie (`server/auth_session.py`) | `insecure-dev-secret-change-me`, which is in git history, so those cookies are forgeable |
| `SOPHIA_APP_PASSWORD` | `POST /auth/login` console passphrase | `sophia` |

Compose expresses this with `${VAR:?message}`, which fires on *unset and empty*
alike, so an empty line in `.env` is just as fatal as a missing one:

```bash
cd voice-agent
cp .env.example .env
printf 'SOPHIA_ENROLLMENT_TOKEN=%s\nSOPHIA_SESSION_SECRET=%s\nSOPHIA_APP_PASSWORD=%s\n' \
  "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" >> .env
docker compose config   # proves the three interpolate; fails loudly if any is missing
docker compose up --build
```

A failure looks like:

```
error while interpolating services.sophia-voice.environment.SOPHIA_ENROLLMENT_TOKEN:
required variable SOPHIA_ENROLLMENT_TOKEN is missing a value: set in .env - see .env.example
```

Nothing else in the compose env anchor needs a secret. The webhook HMAC secrets,
`NEO4J_PASSWORD`, and the auto-router API key all keep their `${VAR:-}` defaults,
and `NEO4J_PASSWORD_FILE` / `SOPHIA_ENROLLMENT_TOKEN_FILE` /
`SOPHIA_OWNER_OVERRIDE_TOKEN_FILE` remain available for deployments that mount
secrets from files instead of `.env`. `tests/test_compose_secrets_contract.py`
locks the whole contract, including the rule that no *other* variable may gain a
`:?`.

## Owner override: optional and fail-closed

`SOPHIA_OWNER_OVERRIDE_ENABLED` now defaults to `false`. It is deliberately *not*
in the mandatory set: owner override is an optional admin capability (appending
reviewed owner clips), and requiring an admin key from every deployment — most
of which never use it — buys no security. Requiring the *token* instead of
auto-disabling the feature keeps an explicit `SOPHIA_OWNER_OVERRIDE_ENABLED=true`
with no token a visible configuration mistake instead of a silent downgrade.
Compose interpolation cannot express the pairing, so it is enforced where the
request lands, consistently:

- `POST /voiceprints/owner-override-enroll` → `503` while the token is empty.
- `voice-agent owner-override-enroll` → hard error, exit 1.
- `/readyz` and `/status` → `checks.owner_override` reports `degraded` with
  `blocking: false`. A missing admin key must not stop capture or verification.

## TLS keys and other rules

The TLS private key and certificate previously committed under
`voice-agent/` (`x1-370.tailcb8954.ts.net.key` / `.crt`) have been moved to
`archive/` and are excluded from git via `.gitignore` (`archive/*.key`,
`archive/*.crt`, `archive/*.pem`).

**Rules:**
- Never commit real private keys or certificates. They must be mounted as
  Docker secrets / read from `/run/secrets/*` (see `configs/container.yaml`
  `neo4j.password_file`).
- `.env` files are gitignored. Provide `.env.example` with placeholders only.
- Real secret values never appear in compose, in `configs/*.yaml`, or in docs.

This repo currently contains no live private key material.
