"""Timestamp helpers shared by the file storage backends.

The backends do not agree on the type they report for ``last_modified``:

* boto3 parses ``LastModified`` into a ``datetime`` for every S3-compatible
  service (``head_object``, ``get_object`` and ``list_objects_v2`` all do it),
* some services hand back a pre-formatted string,
* a backend with nothing to report leaves the value unset.

The API contract for ``last_modified`` is a string, so every backend funnels
its value through :func:`iso_timestamp` before the value leaves the storage
layer. Keeping the conversion in one place is what stops a ``datetime`` from
reaching a Pydantic model that expects a string (which answers HTTP 500).
"""

from datetime import date, datetime
from typing import Any, Optional


def iso_timestamp(value: Any) -> Optional[str]:
    """Return ``value`` as an ISO-8601 string, or ``None`` when it is missing.

    Strings are passed through unchanged (they are already in the API's
    format), ``date``/``datetime`` values are formatted with ``isoformat()``,
    and anything else falls back to ``str()`` so a surprise value from a
    storage client cannot fail the request that carries it.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)
