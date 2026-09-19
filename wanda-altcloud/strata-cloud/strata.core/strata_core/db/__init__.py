"""Database plumbing: the one declarative Base and the shared model mixins."""

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id

__all__ = ["Base", "TimestampMixin", "new_id"]
