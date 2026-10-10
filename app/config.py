"""Application configuration and persistent-state path policy.

``ROOT_DIR`` is the immutable checkout containing code and shipped assets.
``settings.data_dir`` is the mutable application state root.  Keeping those
concepts separate allows a release checkout to be replaced without selecting
an empty database by accident.  When the environment setting is absent, the
historic ``ROOT_DIR/data`` layout remains the default for compatibility.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # This is intentionally the only configurable root for mutable household
    # state.  The explicit validation alias keeps the production setting
    # stable while leaving ordinary settings names backward compatible.
    data_dir: Path = Field(default=ROOT_DIR / "data", validation_alias="LEDGERMIND_DATA_DIR")
    database_url: str | None = None
    event_log_dir: Path | None = None
    reports_dir: Path | None = None
    evidence_dir: Path | None = None
    api_host: str = "0.0.0.0"
    api_port: int = 9543
    frontend_port: int = 9544
    starting_cash: float = 0.0
    minimum_buffer: float = 500.0
    evidence_ocr_enabled: bool = False

    ai_provider: str = "mock"
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_timeout_seconds: int = 60
    ai_structured_output_required: bool = True
    ai_allow_remote_openai: bool = False

    # Web Push (VAPID) — generate with scripts/generate_vapid_keys.py
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:ledgermind@localhost"

    def model_post_init(self, __context: object) -> None:
        # Resolve relative configuration against the checkout, never the
        # caller's current working directory.  Absolute paths are preserved.
        state_root_was_explicit = "data_dir" in self.model_fields_set
        configured = Path(self.data_dir).expanduser()
        self.data_dir = (configured if configured.is_absolute() else ROOT_DIR / configured).resolve()
        # Once the new root is explicitly selected it owns all default mutable
        # paths, including legacy DATABASE_URL/EVENT_LOG_DIR/REPORTS_DIR
        # values that may still be present in an external production env file.
        if state_root_was_explicit:
            self.database_url = f"sqlite:///{self.data_dir / 'ledgermind.db'}"
            self.event_log_dir = self.data_dir / "events"
            self.reports_dir = self.data_dir / "reports"
            self.evidence_dir = self.data_dir / "evidence"
        elif self.database_url is None:
            self.database_url = f"sqlite:///{self.data_dir / 'ledgermind.db'}"
        if self.event_log_dir is None:
            self.event_log_dir = self.data_dir / "events"
        if self.reports_dir is None:
            self.reports_dir = self.data_dir / "reports"
        if self.evidence_dir is None:
            self.evidence_dir = self.data_dir / "evidence"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


settings = Settings()


def ensure_data_dirs() -> None:
    """Create runtime data directories."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.event_log_dir.mkdir(parents=True, exist_ok=True)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    settings.evidence_dir.mkdir(parents=True, exist_ok=True)


def resolve_state_path(raw_path: str | Path) -> Path:
    """Resolve a persisted relative path under the configured state root.

    Older records sometimes stored paths relative to the checkout (for
    example ``data/evidence/...``).  New records are state-root-relative, and
    this resolver keeps the old representation readable during the cutover.
    Absolute paths remain explicit and are not rewritten.
    """
    path = Path(raw_path)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "data":
        state_candidate = settings.data_dir.joinpath(*path.parts[1:])
        legacy_candidate = ROOT_DIR / path
        if legacy_candidate.exists() and not state_candidate.exists():
            return legacy_candidate
        return state_candidate
    return settings.data_dir / path
