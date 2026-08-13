from sqlalchemy.exc import IntegrityError


def violates_constraint(
    error: IntegrityError,
    constraint_name: str,
    *,
    sqlite_columns: tuple[str, ...] = (),
) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == constraint_name:
        return True
    if not sqlite_columns:
        return False
    columns = ", ".join(sqlite_columns)
    return str(error.orig) == f"UNIQUE constraint failed: {columns}"
