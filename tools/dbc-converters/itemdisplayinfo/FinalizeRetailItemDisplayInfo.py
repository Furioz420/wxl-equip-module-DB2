#!/usr/bin/env python3
"""
Finalize converted retail ItemDisplayInfo without merging WotLK/TLK rows.

This mirrors the schema-normalization/final-write part of the step-3 merge
folder, but deliberately does not replace matching IDs and does not append
legacy-only rows. Use this when the converted retail set should remain the
source of truth.
"""

from __future__ import annotations

import argparse
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Set

import pandas as pd


TARGET_ORDER: List[str] = [
    "ID",
    "ModelName_1",
    "ModelName_2",
    "ModelTexture_1",
    "ModelTexture_2",
    "InventoryIcon_1",
    "InventoryIcon_2",
    "GeosetGroup_1",
    "GeosetGroup_2",
    "GeosetGroup_3",
    "Flags",
    "SpellVisualID",
    "GroupSoundIndex",
    "HelmetGeosetVis_1",
    "HelmetGeosetVis_2",
    "Texture_1",
    "Texture_2",
    "Texture_3",
    "Texture_4",
    "Texture_5",
    "Texture_6",
    "Texture_7",
    "Texture_8",
    "ItemVisual",
    "ParticleColorId",
]

NUMERIC_COLS: Set[str] = {
    "ID",
    "GeosetGroup_1",
    "GeosetGroup_2",
    "GeosetGroup_3",
    "SpellVisualID",
    "GroupSoundIndex",
    "HelmetGeosetVis_1",
    "HelmetGeosetVis_2",
    "ItemVisual",
    "ParticleColorId",
}
HEX_COLS: Set[str] = {"Flags"}
TEXT_COLS: Set[str] = set(TARGET_ORDER) - NUMERIC_COLS - HEX_COLS

INT_RE = re.compile(r"^\s*\d+\s*$")
HEX_RE = re.compile(r"^\s*0x[0-9A-Fa-f]+\s*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Finalize converted retail ItemDisplayInfo without merging WotLK/TLK rows."
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=Path("ItemDisplayInfo_WotLK_Converted.csv"),
        help="Converted retail ItemDisplayInfo CSV.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("Final_ItemDisplayInfo_RetailOnly.csv"),
        help="Final retail-only ItemDisplayInfo CSV.",
    )
    parser.add_argument(
        "--sort-by-id",
        action="store_true",
        help="Sort numerically by ID before saving.",
    )
    parser.add_argument(
        "--legacy-file",
        type=Path,
        default=Path("..") / "3 ItemDisplayInfo dbc Merge Wotlk" / "ItemDisplayInfoTLK.csv",
        help="Optional legacy TLK/WotLK CSV used only for a no-merge diagnostic report.",
    )
    parser.add_argument(
        "--diagnostic-report",
        type=Path,
        default=Path("RetailOnly_NoMerge_Diagnostic.csv"),
        help="Report of legacy IDs that would have overlapped or been appended by the old merge.",
    )
    return parser.parse_args()


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[]).applymap(
        lambda value: value.strip() if isinstance(value, str) else ""
    )
    df.columns = [column.strip() for column in df.columns]
    if "ID" not in df.columns:
        raise SystemExit(f"Input is missing required 'ID' column: {path}")
    return df


def keep_numeric_ids(df: pd.DataFrame, label: str) -> pd.DataFrame:
    mask = df["ID"].astype(str).str.fullmatch(r"\d+")
    dropped = int((~mask).sum())
    if dropped:
        print(f"[warn] {label}: dropping {dropped} rows with non-numeric ID")
    return df.loc[mask].copy()


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    for column in TARGET_ORDER:
        if column not in df.columns:
            df[column] = ""
    extras = [column for column in df.columns if column not in TARGET_ORDER]
    if extras:
        print(f"[warn] Dropping unexpected columns: {extras}")
        df = df.drop(columns=extras)
    return df


def normalize_numeric(series: pd.Series) -> pd.Series:
    def normalize(value: str) -> str:
        if not value or not INT_RE.match(value):
            return "0"
        return str(int(value))

    return series.astype(str).map(normalize)


def normalize_hex_flags(series: pd.Series) -> pd.Series:
    def normalize(value: str) -> str:
        if not value or not HEX_RE.match(value):
            return "0x0"
        return value.lower()

    return series.astype(str).map(normalize)


def normalize_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).map(lambda value: value.strip())


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = ensure_columns(df)
    for column in NUMERIC_COLS:
        df[column] = normalize_numeric(df[column])
    for column in HEX_COLS:
        df[column] = normalize_hex_flags(df[column])
    for column in TEXT_COLS:
        df[column] = normalize_text(df[column])
    return df[TARGET_ORDER]


def write_atomic(df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        newline="",
        suffix=".csv",
        dir=output_path.parent,
        encoding="utf-8",
    ) as temp_file:
        temp_path = Path(temp_file.name)
        df.to_csv(temp_file, index=False)
    temp_path.replace(output_path)


def write_no_merge_diagnostic(retail_df: pd.DataFrame, legacy_file: Path, report_file: Path) -> None:
    if not legacy_file.exists():
        print(f"[info] Legacy diagnostic skipped; file not found: {legacy_file}")
        return

    legacy_df = keep_numeric_ids(load_csv(legacy_file), "Legacy")
    retail_ids = set(retail_df["ID"].astype(str))
    legacy_ids = set(legacy_df["ID"].astype(str))
    overlap_ids = sorted(retail_ids & legacy_ids, key=int)
    legacy_only_ids = sorted(legacy_ids - retail_ids, key=int)

    legacy_by_id: Dict[str, dict] = legacy_df.set_index("ID", drop=False).to_dict("index")
    rows = []
    for display_id in overlap_ids:
        legacy_row = legacy_by_id.get(display_id, {})
        retail_row = retail_df.loc[retail_df["ID"] == display_id].iloc[0].to_dict()
        changed_fields = [
            column
            for column in TARGET_ORDER
            if str(retail_row.get(column, "")) != str(legacy_row.get(column, ""))
        ]
        rows.append({
            "ID": display_id,
            "Status": "legacy-overlap-not-merged",
            "ChangedFieldCount": len(changed_fields),
            "ChangedFields": ";".join(changed_fields[:12]),
            "LegacyModelName_1": legacy_row.get("ModelName_1", ""),
            "RetailModelName_1": retail_row.get("ModelName_1", ""),
            "LegacyInventoryIcon_1": legacy_row.get("InventoryIcon_1", ""),
            "RetailInventoryIcon_1": retail_row.get("InventoryIcon_1", ""),
        })

    for display_id in legacy_only_ids:
        legacy_row = legacy_by_id.get(display_id, {})
        rows.append({
            "ID": display_id,
            "Status": "legacy-only-not-appended",
            "ChangedFieldCount": "",
            "ChangedFields": "",
            "LegacyModelName_1": legacy_row.get("ModelName_1", ""),
            "RetailModelName_1": "",
            "LegacyInventoryIcon_1": legacy_row.get("InventoryIcon_1", ""),
            "RetailInventoryIcon_1": "",
        })

    report = pd.DataFrame(rows, columns=[
        "ID",
        "Status",
        "ChangedFieldCount",
        "ChangedFields",
        "LegacyModelName_1",
        "RetailModelName_1",
        "LegacyInventoryIcon_1",
        "RetailInventoryIcon_1",
    ])
    write_atomic(report, report_file)
    print(
        f"[info] No-merge diagnostic: {len(overlap_ids)} legacy overlaps and "
        f"{len(legacy_only_ids)} legacy-only rows were left out."
    )
    print(f"[ok] Wrote diagnostic report: {report_file}")


def main() -> None:
    args = parse_args()
    retail_df = keep_numeric_ids(load_csv(args.input), "Retail")
    retail_df = normalize_df(retail_df)

    if args.sort_by_id:
        retail_df["__sort_id__"] = retail_df["ID"].map(int)
        retail_df = (
            retail_df.sort_values("__sort_id__", kind="stable")
            .drop(columns=["__sort_id__"])
            .reset_index(drop=True)
        )

    write_atomic(retail_df, args.output)
    print(f"[ok] Wrote retail-only ItemDisplayInfo: {args.output} rows={len(retail_df)}")
    write_no_merge_diagnostic(retail_df, args.legacy_file, args.diagnostic_report)


if __name__ == "__main__":
    main()
