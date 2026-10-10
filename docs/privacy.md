# Privacy and security

SQLite household records, authentication, event logs, reports, and uploaded evidence stay on your machine or Docker volume by default. Each account has a separate household database and shares the master authentication database. Ledger does not encrypt files on disk. Protect your computer, filesystem permissions, and backups.

No bank connection or automatic money movement is included. Keep balances and payment records current. Passwords protect app access, not on-disk files.

Deterministic planning needs no AI provider. Optional AI/research features may send financial context or research queries to configured local or remote providers with their own policies. Keep keys in private environment configuration; never publish them or household exports in issues.

**Ask Ledger uses Codex CLI separately from the general AI provider.** Each question sends a snapshot of the signed-in household's recorded financial and household data, its dated plan, and recent conversation turns to Codex. Choosing a local provider for other AI features does not make this chat local-only. Ledger does not save chat transcripts or snapshots in its database; page-session history clears with New or a refresh, and the CLI is invoked in ephemeral mode. The AI provider's own processing and retention policies still apply. The assistant has no Ledger write or payment interface.

Use loopback for a single-machine install. For phone access, use your own private network and HTTPS, keep login enabled, and restrict access. Public internet deployment needs a separate review of authentication, registration, transport security, rate limits, and hardening. Anyone who can reach the app can register.

Public source and releases exclude user databases and operational reports. Screenshots use fictional records. Contributions must exclude .env, databases, uploads, backups, generated reports, tokens, and real household screenshots.
