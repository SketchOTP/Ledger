"""Deployment asset smoke tests (no systemd required)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

DEPLOY_FILES = (
    "scripts/install_service.sh",
    "scripts/uninstall_service.sh",
    "scripts/run_scheduler_loop.py",
    "deploy/systemd/ledgermind-api.service",
    "deploy/systemd/ledgermind-scheduler.service",
    "docs/local-deployment.md",
)

SECRET_PATTERNS = (
    "api_key=",
    "password=",
    "secret=",
    "token=sk-",
)


def test_deployment_files_exist():
    for rel in DEPLOY_FILES:
        assert (ROOT / rel).is_file(), rel


def test_service_templates_use_placeholders_not_secrets():
    for name in ("ledgermind-api.service", "ledgermind-scheduler.service"):
        text = (ROOT / "deploy/systemd" / name).read_text(encoding="utf-8").lower()
        assert "replace_root" in text
        assert "replace_user" in text
        for bad in SECRET_PATTERNS:
            assert bad not in text


def test_service_templates_reference_env_file():
    api = (ROOT / "deploy/systemd/ledgermind-api.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=-REPLACE_ROOT/.env" in api
    assert "python -m app.main" in api


def test_scheduler_service_runs_loop_script():
    sched = (ROOT / "deploy/systemd/ledgermind-scheduler.service").read_text(encoding="utf-8")
    assert "run_scheduler_loop.py" in sched


def test_scheduler_loop_calls_run_due():
    body = (ROOT / "scripts/run_scheduler_loop.py").read_text(encoding="utf-8")
    assert "/api/scheduler/run-due" in body
    assert "900" in body or "SCHEDULER_LOOP_INTERVAL_SEC" in body


def test_install_script_shows_targets_and_uses_deploy_templates():
    install = (ROOT / "scripts/install_service.sh").read_text(encoding="utf-8")
    assert "deploy/systemd/ledgermind-api.service" in install
    assert "/etc/systemd/system/ledgermind-api.service" in install
    assert "REPLACE_ROOT" in install or "sed" in install


def test_uninstall_does_not_delete_data():
    uninstall = (ROOT / "scripts/uninstall_service.sh").read_text(encoding="utf-8")
    assert "NOT deleted" in uninstall or "not delete" in uninstall.lower()
    assert "systemctl disable" in uninstall
