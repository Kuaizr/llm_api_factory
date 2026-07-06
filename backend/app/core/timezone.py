from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import Settings, get_settings


def app_zoneinfo(settings: Settings | None = None) -> ZoneInfo:
    timezone_name = (settings or get_settings()).app_timezone.strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def app_now(settings: Settings | None = None) -> datetime:
    return datetime.now(app_zoneinfo(settings))


def app_today(settings: Settings | None = None) -> date:
    return app_now(settings).date()


def app_day_start_utc(day: date | None = None, settings: Settings | None = None) -> datetime:
    local_day = day or app_today(settings)
    local_start = datetime.combine(local_day, time.min, tzinfo=app_zoneinfo(settings))
    return local_start.astimezone(timezone.utc)
