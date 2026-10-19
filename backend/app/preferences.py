"""Settings saved from the settings dialog, layered over config.yaml.

Each `settings` row overrides one config value; the file itself is never
rewritten (it is hand-edited and commented). Rows that no longer apply, such as
a model since removed from config.yaml, are ignored rather than deleted.
"""

import json
from dataclasses import replace
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Setting
from app.schemas import SettingsUpdate


def effective_settings(session: Session, base: Settings) -> tuple[Settings, list[str]]:
    """config.yaml with the saved overrides applied, and the names of those that applied."""
    overrides = {s.key: json.loads(s.value) for s in session.scalars(select(Setting))}
    models = base.models
    changes: dict[str, Any] = {}
    applied = []
    for key, value in overrides.items():
        if (
            key == "default_model"
            and value in models
            or key == "summarizer_model"
            and (value is None or value in models)
            or key == "token_budget"
            and type(value) is int
            and value > 0
            or key == "send_with_enter"
            and type(value) is bool
        ):
            changes[key] = value
        elif key == "context_windows" and isinstance(value, dict):
            windows = {m: w for m, w in value.items() if m in models and type(w) is int and w > 0}
            if not windows:
                continue
            changes["models"] = {
                m: replace(c, context_window=windows.get(m, c.context_window))
                for m, c in models.items()
            }
        else:
            continue
        applied.append(key)
    return replace(base, **changes), sorted(applied)


def save_settings(session: Session, base: Settings, update: SettingsUpdate) -> None:
    """Store the sent fields. ValueError for a model id that is not in config.yaml."""
    for key in ("default_model", "summarizer_model"):
        value = getattr(update, key)
        if key in update.model_fields_set and value is not None and value not in base.models:
            raise ValueError(f"unknown model {value!r}")
    unknown = set(update.context_windows or {}) - set(base.models)
    if unknown:
        raise ValueError(f"unknown model {sorted(unknown)[0]!r}")

    for key in update.model_fields_set:
        value = getattr(update, key)
        if key == "context_windows":
            row = session.get(Setting, key)
            windows = {**(json.loads(row.value) if row else {}), **value}
            value = {m: w for m, w in windows.items() if w is not None}
            if not value:
                session.execute(delete(Setting).where(Setting.key == key))
                continue
        session.merge(Setting(key=key, value=json.dumps(value)))


def clear_settings(session: Session) -> None:
    session.execute(delete(Setting))
