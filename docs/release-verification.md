# Ledger release verification

## 1.1.0 — Free and open source

Release preparation: 2026-10-15. Ledger is now MIT licensed, including commercial use. A source audit confirmed that the latest running application behavior is present in this public release, with public safe defaults and synthetic test fixtures preserved. No financial behavior or database schema changed for this release.

| Check | Result |
| --- | --- |
| Full backend regression and warning baseline on locked dependencies | PASSED — 608 tests, zero warnings |
| Reserve/chart regression | PASSED — 11 tests |
| Ruff and production frontend build | PASSED |
| npm audit | PASSED — zero reported vulnerabilities at preparation |
| Fresh packaged Docker auth/signup, paid spending, unchanged checking and future expenses | PASSED |
| SPA root, Money and login deep links | PASSED |
| Docker/source/UI/notice/template/requirements identity | PASSED — 290 files |
| Actual desktop/Money/mobile interface screenshots | PASSED — isolated fictional household; no real financial records |
| Gitleaks 8.30.1 source scan | PASSED — zero findings; official tool archive checksum verified |

Screenshots were captured on 2026-10-15 from the actual release application. The mobile viewport check found no horizontal content overflow. Optional AI was not invoked and no AI answer was fabricated for marketing. Existing v1.0.x archives retain their original published notices. GitHub Actions qualifies the exact published source revision; archive checksums and the ZIP build manifest identify the downloadable bytes.

## 1.0.1 — Paid or unpaid purchases

Release preparation: 2026-10-10. The patch removes purchase pending states and duplicate reservations against owner-entered checking, including older recorded purchases. Future unpaid essentials and discretionary allowance usage remain protected. No database migration or purchase re-entry is required.

| Check | Result |
| --- | --- |
| Complete backend regression with locked dependencies and built frontend | PASSED — 608 tests |
| Existing warning baseline gate | PASSED — 0 warnings |
| Reserve/chart regression | PASSED — 11 tests |
| Ruff and production TypeScript/Vite build | PASSED |
| npm dependency audit | PASSED — 0 reported vulnerabilities |
| Fresh Docker signup, authentication boundary and web shell | PASSED |
| Fresh Docker paid daily/grocery purchases, unchanged checking and future essentials | PASSED |
| Docker application, assets, templates, notices and requirements match release source/build | PASSED — 290 files |
| Gitleaks 8.30.1 on release source | PASSED — 0 findings |

Validation note: the initial backend run overlapped the frontend build, so the app loaded before assets were available and two routing checks failed. It also encountered two read-only pytest-cache warnings. The final complete run started after the build, used an external temporary cache, and passed all 608 tests with zero warnings. Desktop/mobile rendered review and backup/restore were not repeated for this patch; the earlier 1.0.0 evidence below remains historical.

## 1.0.0

Release preparation: 2026-10-10. All UI screenshots show an isolated fictional household.

| Check | Result |
| --- | --- |
| Complete backend regression in a fresh locked-dependency container | PASSED — 603 tests |
| Existing warning baseline gate | PASSED — 0 warnings |
| Reserve/chart regression | PASSED — 11 tests |
| Ruff and production TypeScript/Vite build | PASSED |
| npm dependency audit | PASSED — 0 reported vulnerabilities at release preparation |
| Fresh Docker account creation and guided setup API | PASSED |
| Unauthenticated household API request denied | PASSED |
| Desktop and 390px mobile render | PASSED; no horizontal mobile overflow |
| Gitleaks 8.30.1 on fresh public Git history | PASSED — 0 findings |
| Personal-identifier, database, and operational-report exclusion | PASSED |

The release is an independently prepared source snapshot with fresh Git history. No user databases, private environment files, operational reports, or private history are distributed. Dependency audits and tests are point-in-time checks, not guarantees that all defects or vulnerabilities have been found.

Python direct and transitive versions are constrained in requirements.txt and requirements.lock. Frontend versions are recorded in frontend/package-lock.json. GitHub Actions repeats the build, lint, chart tests, full backend suite, and warning gate. The default deployment keeps login enabled and binds only to loopback. Docker's background research scheduler is optional and is not automatically started.
