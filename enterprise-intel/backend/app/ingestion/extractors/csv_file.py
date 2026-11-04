import io

import pandas as pd

from app.ingestion.blocks import Block, ExtractResult, IngestionError
from app.ingestion.extractors.txt import decode_bytes


def _schema_text(df: pd.DataFrame) -> str:
    lines = [f"CSV schema: {len(df)} rows, {len(df.columns)} columns"]
    for col in df.columns:
        series = df[col]
        desc = f"- {col} ({series.dtype}): {series.nunique()} unique, {int(series.isna().sum())} missing"
        if pd.api.types.is_numeric_dtype(series) and series.notna().any():
            desc += f", min {series.min()}, max {series.max()}"
        lines.append(desc)
    return "\n".join(lines)


def extract(path: str) -> ExtractResult:
    with open(path, "rb") as f:
        text = decode_bytes(f.read())
    try:
        df = pd.read_csv(io.StringIO(text), sep=None, engine="python", dtype_backend="numpy_nullable")
    except Exception as exc:
        raise IngestionError(f"Could not parse CSV: {exc}")
    if df.empty:
        raise IngestionError("CSV has no data rows")

    df.columns = [str(c).strip() for c in df.columns]
    blocks = [Block("csv_schema", _schema_text(df), meta={"columns": list(df.columns), "rows": len(df)})]
    for n, row in enumerate(df.itertuples(index=False), start=1):
        body = "\n".join(f"{col}: {val}" for col, val in zip(df.columns, row) if not pd.isna(val))
        blocks.append(Block("csv_row", f"Row {n}\n{body}", meta={"row": n}))
    return ExtractResult(blocks=blocks, pages=1)
