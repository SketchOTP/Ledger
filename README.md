<div align="center">
<img src="frontend/public/ledger-icon-192.png" width="88" alt="Ledger icon" />

# Ledger
### Know what you can spend. Keep tomorrow covered.

A local-first personal CFO dashboard that turns your bills, paydays, groceries, and everyday spending into a clear plan—with a conservative allowance for the things you enjoy.

[Get started](#get-started) · [Latest release](https://github.com/SketchOTP/Ledger/releases/latest) · [How it works](docs/planning.md) · [Privacy](docs/privacy.md)

![CI](https://github.com/SketchOTP/Ledger/actions/workflows/ci.yml/badge.svg)
![License: PolyForm Noncommercial](https://img.shields.io/badge/license-PolyForm%20Noncommercial-blue)

</div>

![Ledger Today dashboard with fictional household data](docs/screenshots/today-desktop.jpg)

*Actual Ledger interface. All screenshots use fictional demo data.*

## Your money, at a glance

Checking balance alone doesn't tell you what you can afford. Ledger looks at dated obligations and expected income at least six calendar months ahead, then shows a practical allowance for the current pay period.

- **See the essentials separately.** Bills, daily spending, groceries, discretionary planning, your biweekly allowance, and the actual emergency cushion each have their own place.
- **Protect future pay periods.** Your allowance accounts for future obligations and keeps breathing room. It doesn't invite you to spend the entire six-month surplus today.
- **Build your emergency cushion.** Plan a monthly contribution toward an adjustable goal. A due-soon deposit stops at the goal and caps the final contribution.
- **Keep pending purchases visible.** A recorded purchase remains reserved while it awaits your checking balance. Marking it recorded doesn't pretend it has cleared.
- **See pressure before payday.** A dated cash-flow chart makes approaching bills and projected shortfalls easier to spot.
- **Keep the page calm.** Three tabs—Today, Money, Advanced—with details tucked behind expandable controls.

![Ledger Money page with fictional household data](docs/screenshots/money-desktop.jpg)

<details>
<summary>See the mobile dashboard</summary>

<img src="docs/screenshots/today-mobile.jpg" width="390" alt="Ledger mobile dashboard with fictional data" />

</details>

## Get started

### Docker · recommended

Install Docker Engine with Docker Compose, then:

```bash
git clone https://github.com/SketchOTP/Ledger.git
cd Ledger
docker compose up --build -d
```

Open **http://localhost:9543**, create your account, and follow the guided setup. No AI subscription or API key is needed for deterministic planning. Household data persists in the `ledger-data` Docker volume. The service binds to your computer's loopback interface by default.

Already using port 9543? Run `LEDGER_PORT=19543 docker compose up --build -d` and open http://localhost:19543.

To update, back up first, then run `git pull --ff-only` and `docker compose up --build -d`. `docker compose down` stops the app and preserves the volume; **do not add `-v` unless you intend to delete your data**. See [deployment and backups](docs/local-deployment.md).

### Prebuilt release downloads

The [release page](https://github.com/SketchOTP/Ledger/releases/latest) also includes:

- **Ledger-v1.0.0.zip** — reviewed source plus the compiled web interface. Extract it and follow the Python setup below; you can skip the npm commands.
- **Ledger-v1.0.0-linux-amd64-docker.tar.gz** — a ready-built image for x86-64 Linux containers. Download it, then:

```bash
docker load -i Ledger-v1.0.0-linux-amd64-docker.tar.gz
docker run -d --name ledger --restart unless-stopped \
  -p 127.0.0.1:9543:9543 \
  -v ledger-data:/var/lib/ledger sketchotp/ledger:1.0.0
```

Open http://localhost:9543. The release includes SHA-256 checksums. For other architectures, build from source with Compose; the prebuilt image was verified on Linux amd64.

### Python + Node

Requires Python 3.11+ and Node.js 22.18+.

```bash
git clone https://github.com/SketchOTP/Ledger.git
cd Ledger
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
npm --prefix frontend ci
npm --prefix frontend run build
python -m app.main
```

Open **http://localhost:9543**. Windows users can activate the environment with `.venv\Scripts\activate` instead. Keep your `.env` and `data/` private. You can set `LEDGERMIND_DATA_DIR` to an absolute path outside the checkout before first launch.

## Make the plan yours

1. Enter your current checking balance, income dates, and recurring bills. Record grace dates only when your lender actually allows them.
2. Set your daily and grocery budgets. For example, $30 a day and $150 each Saturday are distinct from discretionary spending.
3. Enter the emergency money you already have, choose a goal, and set the monthly contribution—for example, $100 monthly until $1,000.
4. Enable the conservative biweekly allowance in your cash profile. Check **Today** before discretionary purchases; record spending and keep balances and payment status current.

The allowance releases 90% of the maximum sustainable equal biweekly allocation tested against the dated plan, while retaining 10% within discretionary planning and protecting an additional allocation of breathing room. Future income is a forecast, not cash available today. The separate **six-month discretionary plan** is a planning total, not permission to spend it all. [Read the calculation details](docs/planning.md).

## Local-first by design

Ledger stores account and household records locally in SQLite, with separate household databases for each login. It does not connect to your bank, move money, or pay bills automatically. Optional AI and research integrations may send data to the providers you configure; review [privacy](docs/privacy.md) before enabling them. The default `mock` AI provider returns placeholders.

The plan depends on the balances, dates, budgets, and payment records you supply. It cannot see unrecorded purchases or guarantee that an expected paycheck arrives. Ledger is budgeting software and does not provide licensed financial advice or guarantees about future outcomes.

## Development

```bash
python -m pip install -c requirements.lock -e '.[dev]'
ruff check .
pytest -q
node scripts/test_reserve_projection.mjs
npm --prefix frontend ci
npm --prefix frontend run build
```

Backend: FastAPI + SQLModel + SQLite. Frontend: React + TypeScript + Vite. Deterministic calculations precede optional AI explanations. Internal `LEDGERMIND_*` setting names are retained for compatibility.

## License and commercial permission

Copyright 2026 SketchOTP. Ledger is **source available under [PolyForm Noncommercial 1.0.0](LICENSE)**. You may use, modify, and share it for purposes permitted by that license. Selling Ledger, offering it as a paid service, or other commercial use requires separate written permission from the copyright holder. [Request commercial permission](https://github.com/SketchOTP/Ledger/issues/new?title=Commercial%20permission%20request); please keep financial records and secrets out of public issues. See [NOTICE](NOTICE) and [third-party notices](THIRD_PARTY_NOTICES.md).
