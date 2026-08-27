"""The autogenerate object filter, importable from both alembic and the tests.

`alembic/env.py` is executed as a script by alembic, not imported as a module, so
the filter it uses cannot be imported from there. Keeping the single copy here
means the drift test compares with exactly the same rules alembic applies --
otherwise the test could pass while `alembic revision --autogenerate` still
proposed dropping half the database.
"""

from __future__ import annotations

from typing import Any


def include_object(
    # Alembic calls this positionally with its full signature. The first and
    # last are part of that contract but nothing here needs them, so they are
    # underscore-prefixed rather than silenced with a lint directive.
    _obj: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    _compare_to: Any,
    *,
    metadata_tables: Any = None,
) -> bool:
    """Decide whether autogenerate should consider a database object.

    Three classes are excluded:

    1. **Any reflected table the models do not declare.** PostGIS installs its
       own catalogue tables plus the entire TIGER geocoder schema -- `county`,
       `edges`, `featnames`, `zip_lookup` and about thirty more -- and
       autogenerate, seeing tables no model declares, proposes dropping every
       one. The trade-off is deliberate: autogenerate can no longer notice a
       table *deleted* from the models, which is a far safer thing to miss than
       a DROP nobody asked for.
    2. **GeoAlchemy2's spatial indexes**, created as a side effect of the column
       type, so they exist in the database but never in the metadata.
    3. **The trigram indexes**, created with raw SQL because `gin_trgm_ops` has
       no SQLAlchemy expression.
    """
    if metadata_tables is None:
        from app.db.base import Base

        metadata_tables = Base.metadata.tables

    if type_ == "table" and reflected and name not in metadata_tables:
        return False
    if type_ == "index" and name:
        if name.startswith("idx_") and "geom" in name:
            return False
        if name.endswith("_trgm"):
            return False
    return True
