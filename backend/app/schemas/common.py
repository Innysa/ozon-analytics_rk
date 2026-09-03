from pydantic import BaseModel


class ImportSummary(BaseModel):
    """Generic result of a CSV/XLSX import — used for reviews and for
    advertising statistics alike."""

    fetched: int
    created: int
    skipped_duplicate: int
    errors: list[str]
