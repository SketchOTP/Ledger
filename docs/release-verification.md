# Ledger 1.0.0 verification

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
