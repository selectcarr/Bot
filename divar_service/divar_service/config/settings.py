import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


DIVAR_SERVICE_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = DIVAR_SERVICE_DIR / "runtime"


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable {name} must be an integer."
        ) from exc


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable {name} must be a number."
        ) from exc


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass(frozen=True)
class Settings:
    timezone: str

    runs_per_day: int
    schedule_start: str
    schedule_end: str
    minimum_gap_minutes: int

    max_pages: int
    max_ads_per_run: int
    retention_days: int

    min_sample_count: int
    min_deal_percent: float
    max_deal_percent: float

    first_page_delay_min: int
    first_page_delay_max: int
    next_page_delay_min: int
    next_page_delay_max: int

    request_timeout_seconds: int

    telegram_bot_token: str
    telegram_chat_id: str

    dry_run: bool

    database_path: Path
    cookies_path: Path
    log_path: Path


def load_settings() -> Settings:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        timezone=os.getenv(
            "DIVAR_TIMEZONE",
            "Asia/Tehran",
        ),

        runs_per_day=_get_int(
            "DIVAR_RUNS_PER_DAY",
            4,
        ),
        schedule_start=os.getenv(
            "DIVAR_SCHEDULE_START",
            "10:30",
        ),
        schedule_end=os.getenv(
            "DIVAR_SCHEDULE_END",
            "00:30",
        ),
        minimum_gap_minutes=_get_int(
            "DIVAR_MINIMUM_GAP_MINUTES",
            90,
        ),

        max_pages=_get_int(
            "DIVAR_MAX_PAGES",
            3,
        ),
        max_ads_per_run=_get_int(
            "DIVAR_MAX_ADS_PER_RUN",
            60,
        ),
        retention_days=_get_int(
            "DIVAR_RETENTION_DAYS",
            7,
        ),

        min_sample_count=_get_int(
            "DIVAR_MIN_SAMPLE_COUNT",
            3,
        ),
        min_deal_percent=_get_float(
            "DIVAR_MIN_DEAL_PERCENT",
            2.0,
        ),
        max_deal_percent=_get_float(
            "DIVAR_MAX_DEAL_PERCENT",
            10.0,
        ),

        first_page_delay_min=_get_int(
            "DIVAR_FIRST_PAGE_DELAY_MIN",
            2,
        ),
        first_page_delay_max=_get_int(
            "DIVAR_FIRST_PAGE_DELAY_MAX",
            6,
        ),
        next_page_delay_min=_get_int(
            "DIVAR_NEXT_PAGE_DELAY_MIN",
            3,
        ),
        next_page_delay_max=_get_int(
            "DIVAR_NEXT_PAGE_DELAY_MAX",
            7,
        ),

        request_timeout_seconds=_get_int(
            "DIVAR_REQUEST_TIMEOUT_SECONDS",
            30,
        ),

        telegram_bot_token=os.getenv(
            "DIVAR_TELEGRAM_BOT_TOKEN",
            "",
        ),
        telegram_chat_id=os.getenv(
            "DIVAR_TELEGRAM_CHAT_ID",
            "",
        ),

        dry_run=_get_bool(
            "DIVAR_DRY_RUN",
            True,
        ),

        database_path=Path(
            os.getenv(
                "DIVAR_DATABASE_PATH",
                str(RUNTIME_DIR / "divar_state.db"),
            )
        ),
        cookies_path=Path(
            os.getenv(
                "DIVAR_COOKIES_PATH",
                str(RUNTIME_DIR / "cookies.json"),
            )
        ),
        log_path=Path(
            os.getenv(
                "DIVAR_LOG_PATH",
                str(RUNTIME_DIR / "divar_service.log"),
            )
        ),
    )

    _validate_settings(settings)

    return settings


def _validate_settings(settings: Settings) -> None:
    try:
        ZoneInfo(settings.timezone)
    except Exception as exc:
        raise ValueError(
            f"Invalid timezone: {settings.timezone}"
        ) from exc

    if settings.runs_per_day != 4:
        raise ValueError(
            "DIVAR_RUNS_PER_DAY must be exactly 4."
        )

    if not 1 <= settings.max_pages <= 3:
        raise ValueError(
            "DIVAR_MAX_PAGES must be between 1 and 3."
        )

    if not 1 <= settings.max_ads_per_run <= 60:
        raise ValueError(
            "DIVAR_MAX_ADS_PER_RUN must be between 1 and 60."
        )

    if settings.retention_days != 7:
        raise ValueError(
            "DIVAR_RETENTION_DAYS must be exactly 7."
        )

    if settings.min_sample_count < 3:
        raise ValueError(
            "DIVAR_MIN_SAMPLE_COUNT cannot be less than 3."
        )

    if not (
        0
        <= settings.min_deal_percent
        < settings.max_deal_percent
        <= 100
    ):
        raise ValueError(
            "Deal percentage settings are invalid."
        )

    if (
        settings.first_page_delay_min
        > settings.first_page_delay_max
    ):
        raise ValueError(
            "First-page delay range is invalid."
        )

    if (
        settings.next_page_delay_min
        > settings.next_page_delay_max
    ):
        raise ValueError(
            "Next-page delay range is invalid."
        )

    if settings.request_timeout_seconds < 5:
        raise ValueError(
            "Request timeout cannot be less than 5 seconds."
        )
