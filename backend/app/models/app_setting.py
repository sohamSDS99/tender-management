"""Operator-editable settings that outlive a container restart.

Environment variables are the wrong home for anything a human changes from the
UI: editing one means recreating the container, and the value a reader sees would
be whatever the image was started with. This table holds the few settings that
are genuinely operational, with the env var kept as the fallback default.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.tender import utcnow

#: Local hours of day, comma separated, in SCHEDULER_TIMEZONE. e.g. "0,12"
KEY_RUN_HOURS = "scheduler.run_hours_local"

#: Whether the sweep is triggered at all, "true" or "false". The env var
#: ENABLE_SCHEDULER is the default; this row is an operator overriding it.
KEY_SCHEDULER_ENABLED = "scheduler.enabled"

#: ISO instant of the last operator-initiated re-score, for the cooldown (D23).
#: A re-score leaves no fetch_runs row, so unlike a sweep it has nothing else to
#: derive "when did this last happen" from.
KEY_LAST_RESCORE_AT = "operator.last_rescore_at"


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
