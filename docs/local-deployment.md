# Deployment and backups

Follow the README Docker or Python setup. Docker serves the frontend/API on one port with login enabled and persistent data in a named volume. Native installs use ./data unless LEDGERMIND_DATA_DIR is set. Shipped import templates include fictional example rows; replace them with your own records.

## Docker backup

Stop writes and archive the volume:

```bash
docker compose stop ledger
docker compose run --rm -T --no-deps --user root --entrypoint tar ledger -C /var/lib/ledger -czf - . > ledger-backup.tar.gz
docker compose start ledger
```

Store this archive privately; it includes household and authentication records. Restore to an empty dedicated volume or matching installation to avoid mixing snapshots:

```bash
docker compose stop ledger
cat ledger-backup.tar.gz | docker compose run --rm -T --no-deps --user root --entrypoint sh ledger -c 'tar -xzf - -C /var/lib/ledger && chown -R ledger:ledger /var/lib/ledger'
docker compose start ledger
```

Verify login, balances, bills, and payment history after restoring. Never delete the only backup.

## Native operation

Run from the project root with the virtual environment active. Back up the complete configured state directory with the app stopped, including master/per-user databases, evidence, events, and reports.

Optional systemd templates in deploy/systemd use REPLACE_ROOT and REPLACE_USER placeholders. Inspect scripts/install_service.sh before use. The optional scheduler needs an authenticated SCHEDULER_API_TOKEN (or LM_TOKEN) and is not started by Compose. Deterministic dashboard planning is calculated when read; background research/notifications are separate optional features.

## Phone and reverse proxy

Use your own private network and HTTPS to proxy the local port. Standalone builds use /. For a mount such as /ledger/, set LEDGERMIND_PUBLIC_BASE=/ledger for the frontend build and backend. Docker accepts --build-arg LEDGERMIND_PUBLIC_BASE=/ledger and needs the matching runtime environment. Keep login enabled.
