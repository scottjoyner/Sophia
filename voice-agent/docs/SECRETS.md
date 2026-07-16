# Secret handling note (W-13)

The TLS private key and certificate previously committed under
`voice-agent/` (`x1-370.tailcb8954.ts.net.key` / `.crt`) have been moved to
`archive/` and are excluded from git via `.gitignore` (`archive/*.key`,
`archive/*.crt`, `archive/*.pem`).

**Rules:**
- Never commit real private keys or certificates. They must be mounted as
  Docker secrets / read from `/run/secrets/*` (see `configs/container.yaml`
  `neo4j.password_file`).
- `.env` files are gitignored. Provide `.env.example` with placeholders only.

This repo currently contains no live private key material.
