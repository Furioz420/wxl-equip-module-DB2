#!/usr/bin/env python3
"""
Convert retail Item data to the WotLK Item.dbc shape.

Retail can expose several appearances for one ItemID through
ItemModifiedAppearance. WotLK has only one DisplayInfoID on Item.dbc, so this
converter keeps one default row for the original item and appends generated
clone rows so every retail ItemAppearance.ItemDisplayInfoID has at least one
equipable item.
"""

from __future__ import annotations

import csv
import re
from bisect import bisect_left
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd


CUSTOM_ITEM_ID_BASE = 9_000_000
GENERATED_VARIANTS_FILE = "Generated_Item_Variants.csv"
ORPHAN_APPEARANCES_FILE = "Orphan_Item_Appearances.csv"
COVERAGE_REPORT_FILE = "ItemDisplay_Coverage_Report.csv"
DBC_READY_ITEM_FILE = "Item.dbc.csv"
EUNOIA_ITEM_TEMPLATE_FILE = Path("EunoiaItemTemplate.sql")
EUNOIA_ITEM_MERGE_REPORT_FILE = "Eunoia_Item_DBC_Merge_Report.csv"
BLIZZLIKE_ITEM_FILE = Path("BlizzlikeItem.dbc.csv")
BLIZZLIKE_ITEM_MERGE_REPORT_FILE = "Blizzlike_Item_DBC_Merge_Report.csv"
ITEMDISPLAYINFO_TARGET_FILES = [
    Path("..") / "ItemDisplayInfo" / "3 ItemDisplayInfo dbc Merge Wotlk" / "Final_ItemDisplayInfo_WotLK_Converted.csv",
    Path("..") / "ItemDisplayInfo" / "3 ItemDisplayInfo dbc Merge Wotlk" / "Updated_ItemDisplayInfo_WotLK_Converted.csv",
    Path("..") / "ItemDisplayInfo" / "2 ItemDisplayInfo dbc" / "ItemDisplayInfo_WotLK_Converted.csv",
]
EXTRA_ITEMDISPLAYINFO_FILES = [
    Path("ItemDisplayInfoEunoia.csv"),
]

DISPLAY_TYPE_TO_INVENTORY_TYPE = {
    0: 1,   # Head
    1: 3,   # Shoulder
    2: 2,   # Shirt-ish legacy display bucket
    3: 5,   # Chest
    4: 6,   # Waist
    5: 7,   # Legs
    6: 8,   # Feet
    7: 9,   # Wrist
    8: 10,  # Hands
    9: 16,  # Cloak
    10: 19, # Tabard
    11: 13, # Weapon / one-hand fallback
    12: 20, # Robe
    13: 21, # Main/off-hand bucket
}

TEXTURE_COMPONENT_DISPLAY_TYPES = {
    "au": 3,
    "al": 7,
    "ha": 8,
    "tu": 3,
    "tl": 3,
    "lu": 5,
    "ll": 5,
    "ft": 6,
    "fo": 6,
}

DISPLAY_TYPE_TOKEN_HINTS = [
    (1, ("lshoulder", "rshoulder", "shoulder", "spauld", "pauldron", "mantle")),
    (0, ("helm", "helmet", "hood", "crown", "mask", "cowl", "headpiece")),
    (10, ("tabard",)),
    (9, ("cape", "cloak")),
    (6, ("boot", "boots", "shoe", "shoes", "sandal", "foot", "feet")),
    (8, ("glove", "gloves", "gauntlet", "hand", "hands")),
    (7, ("bracer", "bracers", "wrist", "cuff")),
    (4, ("belt", "waist", "girdle", "buckle", "sash", "cinch", "cord")),
    (5, ("pant", "pants", "leg", "legs", "legging", "leggings", "breeches", "greaves")),
    (12, ("robe", "robes")),
    (3, ("chest", "tunic", "vest", "breastplate", "cuirass", "hauberk", "jerkin", "jacket", "torso", "shirt")),
]

DISPLAY_TYPE_PRIORITY = [1, 0, 10, 9, 6, 8, 7, 4, 5, 12, 3, 2]

ITEM_NAME_INVENTORY_HINTS = [
    (3, ("shoulder", "spauld", "pauldron", "mantle", "epaulet")),
    (1, ("helm", "helmet", "hood", "crown", "mask", "cowl", "headpiece")),
    (16, ("cloak", "cape")),
    (8, ("boot", "boots", "shoe", "shoes", "sandal", "foot", "feet")),
    (10, ("glove", "gloves", "gauntlet", "handwrap", "handguards")),
    (9, ("bracer", "bracers", "wrist", "cuff")),
    (6, ("belt", "waist", "girdle", "buckle", "sash", "cinch", "cord", "wrap")),
    (7, ("pant", "pants", "leg", "legs", "leggings", "breeches", "greaves")),
    (20, ("robe", "robes")),
    (5, ("chest", "tunic", "vest", "breastplate", "cuirass", "hauberk", "jerkin", "jacket", "raiment", "vestment")),
]

eunoia_item_name_by_id: Dict[str, str] = {}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = (
        df.columns.astype(str)
        .str.replace("\ufeff", "", regex=False)
        .str.strip()
    )
    return df


def resolve_col(df: pd.DataFrame, candidates: List[str]) -> str:
    lut = {c.lower(): c for c in df.columns}
    for name in candidates:
        hit = lut.get(name.lower())
        if hit:
            return hit
    raise KeyError(f"Missing required column; tried {candidates}. Available: {list(df.columns)}")


def normalize_id(value, default: str = "0") -> str:
    text = str(value).strip()
    if text in ("", "nan", "NaN", "<NA>", "None"):
        return default
    try:
        return str(int(float(text)))
    except ValueError:
        return text


def as_int(value, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def read_csv_text(path: str) -> pd.DataFrame:
    return normalize_columns(pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[]))


def load_type_row_dbc_csv(path: Path, header_order: List[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=header_order)

    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as dbc_file:
        reader = csv.reader(dbc_file)
        for row_index, row in enumerate(reader):
            if row_index == 0:
                continue
            if not row or not str(row[0]).strip():
                continue
            normalized_row = {}
            for column_index, column in enumerate(header_order):
                normalized_row[column] = normalize_id(row[column_index] if column_index < len(row) else "0")
            rows.append(normalized_row)

    if not rows:
        return pd.DataFrame(columns=header_order)
    return pd.DataFrame(rows).reindex(columns=header_order, fill_value="0")


def clean_item_csv(item_file: str) -> str:
    cleaned_path = "cleaned_" + item_file
    with open(item_file, "r", encoding="utf-8") as f:
        lines = [line.rstrip(",\n") for line in f]
    with open(cleaned_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return cleaned_path


def split_sql_tuple_values(tuple_text: str) -> List[str]:
    reader = csv.reader(
        [tuple_text],
        delimiter=",",
        quotechar="'",
        escapechar="\\",
        skipinitialspace=True,
    )
    return next(reader)


def iter_sql_tuples(values_text: str):
    depth = 0
    in_string = False
    escape = False
    start = None

    for index, char in enumerate(values_text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == "'":
                in_string = False
            continue

        if char == "'":
            in_string = True
        elif char == "(":
            if depth == 0:
                start = index + 1
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and start is not None:
                yield values_text[start:index]
                start = None


def normalize_sql_numeric(value, default: str = "0") -> str:
    text = str(value).strip()
    if text.upper() == "NULL":
        return default
    return normalize_id(text, default)


def normalize_numeric_columns(df: pd.DataFrame, columns: Iterable[str]) -> None:
    for column in columns:
        if column in df.columns:
            df[column] = df[column].map(normalize_id)


def normalize_armor_subclasses(df: pd.DataFrame) -> None:
    if "ClassID" not in df.columns or "SubClassID" not in df.columns:
        return
    armor_buckler_mask = (df["ClassID"].map(normalize_id) == "4") & (df["SubClassID"].map(normalize_id) == "5")
    df.loc[armor_buckler_mask, "SubClassID"] = "1"


def load_eunoia_item_template_rows(header_order: List[str]) -> pd.DataFrame:
    global eunoia_item_name_by_id
    eunoia_item_name_by_id = {}
    if not EUNOIA_ITEM_TEMPLATE_FILE.exists():
        print(f"Eunoia item template SQL not found, skipping: {EUNOIA_ITEM_TEMPLATE_FILE}")
        return pd.DataFrame(columns=header_order)

    print(f"Loading Eunoia item_template rows from {EUNOIA_ITEM_TEMPLATE_FILE}...")
    with open(EUNOIA_ITEM_TEMPLATE_FILE, "r", encoding="utf-8", errors="replace") as sql_file:
        sql_text = sql_file.read()

    insert_re = re.compile(
        r"INSERT\s+INTO\s+`item_template`\s*\((?P<columns>.*?)\)\s*VALUES\s*(?P<values>.*?);",
        re.IGNORECASE | re.DOTALL,
    )
    sql_to_dbc = {
        "ID": ("entry", "0"),
        "ClassID": ("class", "0"),
        "SubClassID": ("subclass", "0"),
        "Sound_override_subclassID": ("SoundOverrideSubclass", "-1"),
        "Material": ("Material", "0"),
        "DisplayInfoID": ("displayid", "0"),
        "InventoryType": ("InventoryType", "0"),
        "SheatheType": ("sheath", "0"),
    }

    rows_by_id: Dict[str, dict] = {}
    for match in insert_re.finditer(sql_text):
        columns = [column.strip().strip("`") for column in match.group("columns").split(",")]
        column_lookup = {column.lower(): index for index, column in enumerate(columns)}
        if not all(sql_name.lower() in column_lookup for sql_name, _ in sql_to_dbc.values()):
            continue

        for tuple_text in iter_sql_tuples(match.group("values")):
            values = split_sql_tuple_values(tuple_text)
            row = {}
            for dbc_column, (sql_column, default) in sql_to_dbc.items():
                value_index = column_lookup[sql_column.lower()]
                value = values[value_index] if value_index < len(values) else default
                row[dbc_column] = normalize_sql_numeric(value, default)

            item_id = normalize_id(row.get("ID"))
            if not item_id or item_id == "0":
                continue
            row["ID"] = item_id
            name_index = column_lookup.get("name")
            if name_index is not None and name_index < len(values):
                eunoia_item_name_by_id[item_id] = str(values[name_index]).strip()
            rows_by_id[item_id] = row

    if not rows_by_id:
        return pd.DataFrame(columns=header_order)

    df_eunoia = pd.DataFrame(rows_by_id.values()).reindex(columns=header_order, fill_value="0")
    for column in header_order:
        df_eunoia[column] = df_eunoia[column].map(normalize_id)
    normalize_armor_subclasses(df_eunoia)
    print(f"Loaded {len(df_eunoia)} Eunoia item_template rows for Item.dbc merge.")
    return df_eunoia


def merge_eunoia_item_template_rows(df_items: pd.DataFrame, header_order: List[str]) -> pd.DataFrame:
    df_eunoia = load_eunoia_item_template_rows(header_order)
    if df_eunoia.empty:
        return df_items

    df_items = df_items.reindex(columns=header_order, fill_value="0").copy()
    for column in header_order:
        df_items[column] = df_items[column].map(normalize_id)

    existing_ids = set(df_items["ID"].map(normalize_id))
    eunoia_ids = set(df_eunoia["ID"].map(normalize_id))
    overridden_ids = sorted(existing_ids & eunoia_ids, key=as_int)
    added_ids = sorted(eunoia_ids - existing_ids, key=as_int)

    df_items = df_items[~df_items["ID"].isin(eunoia_ids)]
    df_items = pd.concat([df_items, df_eunoia], ignore_index=True)

    report_rows = []
    eunoia_by_id = df_eunoia.set_index("ID").to_dict("index")
    for item_id in overridden_ids:
        row = eunoia_by_id.get(item_id, {})
        report_rows.append({
            "ItemID": item_id,
            "DisplayInfoID": row.get("DisplayInfoID", "0"),
            "InventoryType": row.get("InventoryType", "0"),
            "Action": "overrode-retail-row",
        })
    for item_id in added_ids:
        row = eunoia_by_id.get(item_id, {})
        report_rows.append({
            "ItemID": item_id,
            "DisplayInfoID": row.get("DisplayInfoID", "0"),
            "InventoryType": row.get("InventoryType", "0"),
            "Action": "added-eunoia-row",
        })
    pd.DataFrame(
        report_rows,
        columns=["ItemID", "DisplayInfoID", "InventoryType", "Action"],
    ).to_csv(EUNOIA_ITEM_MERGE_REPORT_FILE, index=False)

    print(
        "Merged Eunoia item_template into Item.dbc rows: "
        f"{len(added_ids)} added, {len(overridden_ids)} retail rows overridden."
    )
    return df_items


def merge_blizzlike_item_rows(df_items: pd.DataFrame, header_order: List[str]) -> pd.DataFrame:
    df_blizzlike = load_type_row_dbc_csv(BLIZZLIKE_ITEM_FILE, header_order)
    if df_blizzlike.empty:
        print(f"Blizzlike Item.dbc baseline not found or empty, skipping: {BLIZZLIKE_ITEM_FILE}")
        return df_items

    df_items = df_items.reindex(columns=header_order, fill_value="0").copy()
    for column in header_order:
        df_items[column] = df_items[column].map(normalize_id)
        df_blizzlike[column] = df_blizzlike[column].map(normalize_id)

    existing_ids = set(df_items["ID"].map(normalize_id))
    blizzlike_ids = set(df_blizzlike["ID"].map(normalize_id))
    overridden_ids = sorted(existing_ids & blizzlike_ids, key=as_int)
    added_ids = sorted(blizzlike_ids - existing_ids, key=as_int)

    df_items = df_items[~df_items["ID"].isin(blizzlike_ids)]
    df_items = pd.concat([df_items, df_blizzlike], ignore_index=True)

    blizzlike_by_id = df_blizzlike.set_index("ID").to_dict("index")
    report_rows = []
    for item_id in overridden_ids:
        row = blizzlike_by_id.get(item_id, {})
        report_rows.append({
            "ItemID": item_id,
            "DisplayInfoID": row.get("DisplayInfoID", "0"),
            "InventoryType": row.get("InventoryType", "0"),
            "Action": "overrode-generated-row",
        })
    for item_id in added_ids:
        row = blizzlike_by_id.get(item_id, {})
        report_rows.append({
            "ItemID": item_id,
            "DisplayInfoID": row.get("DisplayInfoID", "0"),
            "InventoryType": row.get("InventoryType", "0"),
            "Action": "added-blizzlike-row",
        })

    pd.DataFrame(
        report_rows,
        columns=["ItemID", "DisplayInfoID", "InventoryType", "Action"],
    ).to_csv(BLIZZLIKE_ITEM_MERGE_REPORT_FILE, index=False)

    print(
        "Merged Blizzlike Item.dbc rows: "
        f"{len(added_ids)} added, {len(overridden_ids)} generated rows overridden."
    )
    return df_items


def build_converted_items(df_tww: pd.DataFrame) -> pd.DataFrame:
    tww_aliases: Dict[str, List[str]] = {
        "ID": ["ID"],
        "ClassID": ["ClassID", "Class", "ItemClassID"],
        "SubClassID": ["SubClassID", "SubclassID", "SubClass", "Subclass", "ItemSubClassID"],
        "Material": ["Material", "ItemMaterial", "itemdisplayinfomaterialres", "MaterialID"],
        "InventoryType": ["InventoryType", "InvType", "InventorySlot"],
        "SheatheType": ["SheatheType", "SheathType", "Sheathing"],
        "Sound_override_subclassID": ["Sound_override_subclassID", "SoundOverrideSubClassID", "SoundSubClassID"],
    }

    def col(name: str) -> str:
        return resolve_col(df_tww, tww_aliases[name])

    df_converted = df_tww[[
        col("ID"),
        col("ClassID"),
        col("SubClassID"),
        col("Material"),
        col("InventoryType"),
        col("SheatheType"),
        col("Sound_override_subclassID"),
    ]].copy()
    df_converted.columns = [
        "ID",
        "ClassID",
        "SubClassID",
        "Material",
        "InventoryType",
        "SheatheType",
        "Sound_override_subclassID",
    ]

    normalize_numeric_columns(df_converted, df_converted.columns)

    # WotLK still has a deprecated buckler armor subclass 5. Treat incoming
    # armor subclass 5 rows as cloth so generated custom armor remains equipable.
    normalize_armor_subclasses(df_converted)
    df_converted["DisplayInfoID"] = "0"
    return df_converted


def prepare_appearance_tables(
    df_item_modified_appearance: pd.DataFrame,
    df_item_appearance: pd.DataFrame,
    known_item_ids: set[str],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    normalize_numeric_columns(
        df_item_modified_appearance,
        ["ID", "ItemID", "ItemAppearanceModifierID", "ItemAppearanceID", "OrderIndex", "Flags"],
    )
    normalize_numeric_columns(
        df_item_appearance,
        ["ID", "DisplayType", "ItemDisplayInfoID", "DefaultIconFileDataID", "UiOrder"],
    )

    appearance_cols = [
        "ID",
        "DisplayType",
        "ItemDisplayInfoID",
        "DefaultIconFileDataID",
        "UiOrder",
    ]
    joined = df_item_modified_appearance.merge(
        df_item_appearance[appearance_cols],
        left_on="ItemAppearanceID",
        right_on="ID",
        suffixes=("_mod", "_appearance"),
        how="left",
    )
    joined["ItemID"] = joined["ItemID"].map(normalize_id)
    joined["ItemDisplayInfoID"] = joined["ItemDisplayInfoID"].map(normalize_id)
    joined = joined[
        joined["ItemID"].isin(known_item_ids)
        & ~joined["ItemDisplayInfoID"].isin(["", "0"])
    ].copy()

    for column in ["ItemAppearanceModifierID", "OrderIndex", "UiOrder", "DisplayType", "ID_mod"]:
        if column in joined.columns:
            joined[f"_{column}_int"] = joined[column].map(as_int)

    joined["_default_rank"] = joined["_ItemAppearanceModifierID_int"].map(lambda value: 0 if value == 0 else 1)
    joined = joined.sort_values(
        by=["ItemID", "_default_rank", "_OrderIndex_int", "_ItemAppearanceModifierID_int", "_ID_mod_int"],
        kind="stable",
    )

    df_item_appearance = df_item_appearance.copy()
    df_item_appearance["ItemDisplayInfoID"] = df_item_appearance["ItemDisplayInfoID"].map(normalize_id)
    df_item_appearance = df_item_appearance[~df_item_appearance["ItemDisplayInfoID"].isin(["", "0"])]
    for column in ["ID", "DisplayType", "DefaultIconFileDataID", "UiOrder"]:
        df_item_appearance[column] = df_item_appearance[column].map(normalize_id)
        df_item_appearance[f"_{column}_int"] = df_item_appearance[column].map(as_int)
    df_item_appearance = df_item_appearance.sort_values(
        by=["ItemDisplayInfoID", "_UiOrder_int", "_ID_int"],
        kind="stable",
    )

    return joined, df_item_appearance


def apply_default_displays(df_items: pd.DataFrame, joined_appearances: pd.DataFrame) -> None:
    default_choice = joined_appearances.drop_duplicates("ItemID", keep="first")
    item_to_display = dict(zip(default_choice["ItemID"], default_choice["ItemDisplayInfoID"]))
    df_items["DisplayInfoID"] = df_items["ID"].map(item_to_display).fillna("0").map(normalize_id)


def backfill_display_from_legacy_item(df_items: pd.DataFrame, df_legacy_item: pd.DataFrame) -> None:
    disp_col_item = None
    try:
        disp_col_item = resolve_col(df_legacy_item, ["DisplayInfoID", "ItemDisplayInfoID", "DisplayInfoId"])
    except KeyError:
        return

    df_legacy_item = df_legacy_item.copy()
    df_legacy_item["ID"] = df_legacy_item["ID"].map(normalize_id)
    df_legacy_item[disp_col_item] = df_legacy_item[disp_col_item].map(normalize_id)
    item_displayinfo_map = df_legacy_item.set_index("ID")[disp_col_item].to_dict()

    missing_mask = df_items["DisplayInfoID"].isin(["", "0"])
    df_items.loc[missing_mask, "DisplayInfoID"] = (
        df_items.loc[missing_mask, "ID"].map(item_displayinfo_map).fillna("0").map(normalize_id)
    )


def next_custom_id(existing_ids: Iterable[str]) -> int:
    max_existing = max((as_int(item_id) for item_id in existing_ids), default=0)
    return max(CUSTOM_ITEM_ID_BASE, max_existing + 1)


def choose_direct_sources(joined_appearances: pd.DataFrame) -> Dict[str, dict]:
    source_rows = joined_appearances.sort_values(
        by=["ItemDisplayInfoID", "_default_rank", "_OrderIndex_int", "_ItemAppearanceModifierID_int", "ItemID"],
        kind="stable",
    ).drop_duplicates("ItemDisplayInfoID", keep="first")
    return {
        row["ItemDisplayInfoID"]: row.to_dict()
        for _, row in source_rows.iterrows()
    }


def build_fallback_index(
    direct_source_rows: List[dict],
    df_items_by_id: Dict[str, dict],
) -> Dict[object, List[Tuple[int, int, str]]]:
    index: Dict[object, List[Tuple[int, int, str]]] = {"all": []}
    for row in direct_source_rows:
        source_item_id = normalize_id(row.get("ItemID"))
        if source_item_id not in df_items_by_id:
            continue
        display_type = as_int(row.get("DisplayType"))
        ui_order = as_int(row.get("UiOrder"))
        entry = (ui_order, as_int(source_item_id), source_item_id)
        index.setdefault(display_type, []).append(entry)
        index["all"].append(entry)
    for candidates in index.values():
        candidates.sort()
    return index


def nearest_indexed_source(
    candidates: List[Tuple[int, int, str]],
    target_ui_order: int,
) -> Optional[str]:
    best = None
    if candidates:
        pos = bisect_left(candidates, (target_ui_order, -1, ""))
        for check_pos in (pos - 2, pos - 1, pos, pos + 1, pos + 2):
            if 0 <= check_pos < len(candidates):
                ui_order, item_id_int, item_id = candidates[check_pos]
                rank = (abs(ui_order - target_ui_order), item_id_int)
                if best is None or rank < best[0]:
                    best = (rank, item_id)
    if best:
        return best[1]
    return None


def text_contains_any(text: str, needles: Iterable[str]) -> bool:
    return any(needle in text for needle in needles)


def add_display_type_score(scores: Dict[int, int], display_type: int, amount: int) -> None:
    scores[display_type] = scores.get(display_type, 0) + amount


def score_display_type_tokens(scores: Dict[int, int], text: str, amount: int) -> None:
    normalized = str(text or "").lower().replace("\\", "_").replace("/", "_")
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    for display_type, tokens in DISPLAY_TYPE_TOKEN_HINTS:
        for token in tokens:
            if token in normalized:
                add_display_type_score(scores, display_type, amount)
                break


def score_texture_component_code(scores: Dict[int, int], text: str, amount: int) -> None:
    normalized = str(text or "").lower()
    for match in re.finditer(r"(?:^|_)(au|al|ha|tu|tl|lu|ll|ft|fo)(?:_|$)", normalized):
        display_type = TEXTURE_COMPONENT_DISPLAY_TYPES.get(match.group(1))
        if display_type is not None:
            add_display_type_score(scores, display_type, amount)


def infer_display_type_from_itemdisplay(row: dict) -> int:
    scores: Dict[int, int] = {}

    for field in ("ModelName_1", "ModelName_2"):
        score_display_type_tokens(scores, row.get(field, ""), 1)
    for field in ("ModelTexture_1", "ModelTexture_2"):
        score_display_type_tokens(scores, row.get(field, ""), 2)
    score_display_type_tokens(scores, row.get("InventoryIcon_1", ""), 8)

    for field in ("Texture_1", "Texture_2", "Texture_3", "Texture_4", "Texture_5", "Texture_6", "Texture_7", "Texture_8"):
        value = row.get(field, "")
        score_display_type_tokens(scores, value, 1)
        score_texture_component_code(scores, value, 5)

    if not scores:
        return -1

    return max(
        scores,
        key=lambda display_type: (
            scores[display_type],
            -DISPLAY_TYPE_PRIORITY.index(display_type) if display_type in DISPLAY_TYPE_PRIORITY else -999,
        ),
    )


def load_itemdisplayinfo_targets(itemdisplayinfo_file: Path, required: bool = False) -> pd.DataFrame:
    columns = [
        "ID",
        "ItemDisplayInfoID",
        "DisplayType",
        "DefaultIconFileDataID",
        "UiOrder",
        "_ID_int",
        "_DisplayType_int",
        "_UiOrder_int",
    ]
    if not itemdisplayinfo_file.exists():
        if required:
            print(f"ItemDisplayInfo target file not found, falling back to ItemAppearance coverage: {itemdisplayinfo_file}")
        return pd.DataFrame(columns=columns)

    df_itemdisplay = read_csv_text(str(itemdisplayinfo_file))
    id_col = resolve_col(df_itemdisplay, ["ID"])
    df_itemdisplay["ItemDisplayInfoID"] = df_itemdisplay[id_col].map(normalize_id)
    df_itemdisplay = df_itemdisplay[~df_itemdisplay["ItemDisplayInfoID"].isin(["", "0"])].copy()
    df_itemdisplay["DisplayType"] = df_itemdisplay.apply(lambda row: str(infer_display_type_from_itemdisplay(row.to_dict())), axis=1)
    df_itemdisplay["DefaultIconFileDataID"] = "0"
    df_itemdisplay["UiOrder"] = df_itemdisplay["ItemDisplayInfoID"]
    df_itemdisplay["ID"] = df_itemdisplay["ItemDisplayInfoID"]
    for column in ["ID", "DisplayType", "UiOrder"]:
        df_itemdisplay[column] = df_itemdisplay[column].map(normalize_id)
        df_itemdisplay[f"_{column}_int"] = df_itemdisplay[column].map(as_int)

    return df_itemdisplay[columns].drop_duplicates("ItemDisplayInfoID", keep="first")


def load_all_itemdisplayinfo_targets() -> pd.DataFrame:
    target_frames = []
    for itemdisplayinfo_file in ITEMDISPLAYINFO_TARGET_FILES:
        target = load_itemdisplayinfo_targets(itemdisplayinfo_file)
        if not target.empty:
            print(f"Loaded ItemDisplayInfo coverage target: {itemdisplayinfo_file}")
            target_frames.append(target)
            break

    if not target_frames:
        print(
            "ItemDisplayInfo target files not found, falling back to ItemAppearance coverage: "
            f"{ITEMDISPLAYINFO_TARGET_FILES}"
        )
    for itemdisplayinfo_file in EXTRA_ITEMDISPLAYINFO_FILES:
        target = load_itemdisplayinfo_targets(itemdisplayinfo_file)
        if not target.empty:
            print(f"Loaded extra ItemDisplayInfo coverage target: {itemdisplayinfo_file}")
            target_frames.append(target)

    target_frames = [frame for frame in target_frames if not frame.empty]
    if not target_frames:
        return load_itemdisplayinfo_targets(Path("__missing_optional_itemdisplayinfo__"))

    return pd.concat(target_frames, ignore_index=True).drop_duplicates("ItemDisplayInfoID", keep="first")


def infer_inventory_type_from_item_name(name: str) -> int:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower())
    if not normalized:
        return 0
    for inventory_type, tokens in ITEM_NAME_INVENTORY_HINTS:
        if any(token in normalized for token in tokens):
            return inventory_type
    return 0


def build_itemdisplay_inventory_hints(df_itemdisplay_targets: pd.DataFrame) -> Dict[str, str]:
    hints: Dict[str, str] = {}
    for _, row in df_itemdisplay_targets.iterrows():
        display_id = normalize_id(row.get("ItemDisplayInfoID"))
        display_type = as_int(row.get("DisplayType"), -1)
        inventory_type = DISPLAY_TYPE_TO_INVENTORY_TYPE.get(display_type, 0)
        if display_id and display_id != "0" and inventory_type:
            hints[display_id] = str(inventory_type)
    return hints


def normalize_item_slots_from_hints(df_items: pd.DataFrame, df_itemdisplay_targets: pd.DataFrame) -> None:
    normalize_armor_subclasses(df_items)
    if "InventoryType" not in df_items.columns or "DisplayInfoID" not in df_items.columns:
        return

    display_inventory_hints = build_itemdisplay_inventory_hints(df_itemdisplay_targets)
    for index, row in df_items.iterrows():
        display_id = normalize_id(row.get("DisplayInfoID"))
        display_hint = display_inventory_hints.get(display_id, "")
        name_hint = infer_inventory_type_from_item_name(eunoia_item_name_by_id.get(normalize_id(row.get("ID")), ""))
        inventory_type = display_hint or (str(name_hint) if name_hint else "")
        if inventory_type:
            df_items.at[index, "InventoryType"] = inventory_type


def build_display_targets(
    df_item_appearance: pd.DataFrame,
    df_itemdisplay_targets: pd.DataFrame,
) -> pd.DataFrame:
    rows_by_display: Dict[str, dict] = {
        row["ItemDisplayInfoID"]: row.to_dict()
        for _, row in df_item_appearance.drop_duplicates("ItemDisplayInfoID", keep="first").iterrows()
    }
    for _, row in df_itemdisplay_targets.iterrows():
        display_id = normalize_id(row.get("ItemDisplayInfoID"))
        if display_id and display_id != "0":
            inferred_display_type = as_int(row.get("DisplayType"), -1)
            if display_id not in rows_by_display or inferred_display_type >= 0:
                rows_by_display[display_id] = row.to_dict()

    if not rows_by_display:
        return pd.DataFrame(columns=[
            "ID",
            "ItemDisplayInfoID",
            "DisplayType",
            "DefaultIconFileDataID",
            "UiOrder",
            "_ID_int",
            "_DisplayType_int",
            "_UiOrder_int",
        ])

    targets = pd.DataFrame(rows_by_display.values())
    for column in ["ID", "ItemDisplayInfoID", "DisplayType", "DefaultIconFileDataID", "UiOrder"]:
        if column not in targets.columns:
            targets[column] = "0"
        targets[column] = targets[column].map(normalize_id)
    for column in ["ID", "DisplayType", "UiOrder"]:
        targets[f"_{column}_int"] = targets[column].map(as_int)
    return targets.sort_values(by=["ItemDisplayInfoID"], key=lambda series: series.map(as_int), kind="stable")


def choose_fallback_source(
    target_appearance: dict,
    fallback_index: Dict[object, List[Tuple[int, int, str]]],
    df_items_by_id: Dict[str, dict],
) -> Tuple[Optional[str], str]:
    target_display_type = as_int(target_appearance.get("DisplayType"))
    target_ui_order = as_int(target_appearance.get("UiOrder"))

    same_type_source = nearest_indexed_source(
        fallback_index.get(target_display_type, []),
        target_ui_order,
    )
    if same_type_source:
        return same_type_source, "nearest-display-type-appearance"

    any_source = nearest_indexed_source(fallback_index.get("all", []), target_ui_order)
    if any_source:
        return any_source, "nearest-appearance"

    target_inventory_type = DISPLAY_TYPE_TO_INVENTORY_TYPE.get(target_display_type)
    if target_inventory_type is not None:
        inventory_candidates = [
            (as_int(item_id), item_id)
            for item_id, item_row in df_items_by_id.items()
            if as_int(item_row.get("InventoryType")) == target_inventory_type
        ]
        if inventory_candidates:
            inventory_candidates.sort()
            return inventory_candidates[0][1], "inventory-type-fallback"

    if df_items_by_id:
        first_item_id = min(df_items_by_id.keys(), key=as_int)
        return first_item_id, "first-item-fallback"
    return None, "no-source"


def append_missing_display_items(
    df_items: pd.DataFrame,
    joined_appearances: pd.DataFrame,
    df_item_appearance: pd.DataFrame,
    df_itemdisplay_targets: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df_items = df_items.copy()
    df_items_by_id = {
        row["ID"]: row.to_dict()
        for _, row in df_items.iterrows()
    }

    covered_display_ids = {
        normalize_id(display_id)
        for display_id in df_items["DisplayInfoID"]
        if normalize_id(display_id) not in ("", "0")
    }

    direct_sources = choose_direct_sources(joined_appearances)
    direct_source_rows = list(direct_sources.values())
    fallback_index = build_fallback_index(direct_source_rows, df_items_by_id)
    appearance_by_display = {
        row["ItemDisplayInfoID"]: row.to_dict()
        for _, row in build_display_targets(df_item_appearance, df_itemdisplay_targets).iterrows()
    }

    generated_rows = []
    generated_report = []
    orphan_report = []
    next_id = next_custom_id(df_items["ID"])

    for display_id in sorted(appearance_by_display.keys(), key=as_int):
        if display_id in covered_display_ids:
            continue

        appearance_row = appearance_by_display[display_id]
        direct_source = direct_sources.get(display_id)
        if direct_source:
            source_item_id = normalize_id(direct_source.get("ItemID"))
            reason = "modifier-variant"
            source_appearance_id = normalize_id(direct_source.get("ItemAppearanceID"))
            source_modifier = normalize_id(direct_source.get("ItemAppearanceModifierID"))
        else:
            source_item_id, reason = choose_fallback_source(appearance_row, fallback_index, df_items_by_id)
            source_appearance_id = normalize_id(appearance_row.get("ID"))
            source_modifier = ""
            orphan_report.append({
                "ItemAppearanceID": source_appearance_id,
                "ItemDisplayInfoID": display_id,
                "DisplayType": normalize_id(appearance_row.get("DisplayType")),
                "UiOrder": normalize_id(appearance_row.get("UiOrder")),
                "DefaultIconFileDataID": normalize_id(appearance_row.get("DefaultIconFileDataID")),
                "CloneSourceItemID": source_item_id or "",
                "Reason": reason,
            })

        if not source_item_id or source_item_id not in df_items_by_id:
            continue

        generated_id = str(next_id)
        next_id += 1

        clone = dict(df_items_by_id[source_item_id])
        clone["ID"] = generated_id
        clone["DisplayInfoID"] = display_id
        generated_rows.append(clone)
        covered_display_ids.add(display_id)

        generated_report.append({
            "GeneratedItemID": generated_id,
            "SourceItemID": source_item_id,
            "ItemDisplayInfoID": display_id,
            "ItemAppearanceID": source_appearance_id,
            "ItemAppearanceModifierID": source_modifier,
            "DisplayType": normalize_id(appearance_row.get("DisplayType")),
            "UiOrder": normalize_id(appearance_row.get("UiOrder")),
            "DefaultIconFileDataID": normalize_id(appearance_row.get("DefaultIconFileDataID")),
            "Reason": reason,
        })

    if generated_rows:
        df_items = pd.concat([df_items, pd.DataFrame(generated_rows)], ignore_index=True)

    generated_report_df = pd.DataFrame(generated_report)
    orphan_report_df = pd.DataFrame(orphan_report)
    return df_items, generated_report_df, orphan_report_df


def write_coverage_report(df_items: pd.DataFrame, display_targets: pd.DataFrame) -> None:
    wanted = {
        normalize_id(display_id)
        for display_id in display_targets["ItemDisplayInfoID"]
        if normalize_id(display_id) not in ("", "0")
    }
    covered = {
        normalize_id(display_id)
        for display_id in df_items["DisplayInfoID"]
        if normalize_id(display_id) not in ("", "0")
    }
    missing = sorted(wanted - covered, key=as_int)
    rows = [{"ItemDisplayInfoID": display_id, "Reason": "No item source could be selected"} for display_id in missing]
    pd.DataFrame(rows, columns=["ItemDisplayInfoID", "Reason"]).to_csv(COVERAGE_REPORT_FILE, index=False)


def write_dbc_ready_item_csv(df_items: pd.DataFrame, header_order: List[str]) -> None:
    dbc_rows = df_items.reindex(columns=header_order, fill_value="0").copy()
    for column in header_order:
        dbc_rows[column] = dbc_rows[column].map(normalize_id)
    dbc_rows = dbc_rows.sort_values(by="ID", key=lambda series: series.map(as_int), kind="stable")

    with open(DBC_READY_ITEM_FILE, "w", encoding="utf-8", newline="") as out:
        out.write("long,long,long,long,long,long,long,long,\n")
        dbc_rows.to_csv(out, index=False, header=False)


def convert_tww_to_wotlk(
    tww_file: str,
    item_file: str,
    item_modified_appearance_file: str,
    item_appearance_file: str,
    output_file: str,
    final_output_file: str,
) -> None:
    print("Loading files...")
    cleaned_item_file = clean_item_csv(item_file)
    df_legacy_item = read_csv_text(cleaned_item_file)
    df_tww = read_csv_text(tww_file)
    df_item_modified_appearance = read_csv_text(item_modified_appearance_file)
    df_item_appearance = read_csv_text(item_appearance_file)
    df_itemdisplay_targets = load_all_itemdisplayinfo_targets()
    print("Files loaded successfully.")

    header_order = [
        "ID",
        "ClassID",
        "SubClassID",
        "Sound_override_subclassID",
        "Material",
        "DisplayInfoID",
        "InventoryType",
        "SheatheType",
    ]

    print("Extracting Item.dbc columns...")
    df_items = build_converted_items(df_tww)
    df_items = df_items.reindex(columns=header_order, fill_value="0")

    known_item_ids = set(df_items["ID"].map(normalize_id))
    joined_appearances, df_item_appearance = prepare_appearance_tables(
        df_item_modified_appearance,
        df_item_appearance,
        known_item_ids,
    )

    print("Assigning default ItemModifiedAppearance displays...")
    apply_default_displays(df_items, joined_appearances)
    backfill_display_from_legacy_item(df_items, df_legacy_item)
    df_items = merge_eunoia_item_template_rows(df_items, header_order)
    df_items = merge_blizzlike_item_rows(df_items, header_order)
    normalize_item_slots_from_hints(df_items, df_itemdisplay_targets)

    print(f"Saving base converted file to {output_file}...")
    base_output = df_items.reindex(columns=header_order, fill_value="0").copy()
    base_output = base_output.sort_values(by="ID", key=lambda series: series.map(as_int), kind="stable")
    base_output.to_csv(output_file, index=False)

    print("Generating one item for every ItemAppearance.ItemDisplayInfoID...")
    df_items, generated_report, orphan_report = append_missing_display_items(
        df_items,
        joined_appearances,
        df_item_appearance,
        df_itemdisplay_targets,
    )

    normalize_item_slots_from_hints(df_items, df_itemdisplay_targets)
    df_items = df_items.reindex(columns=header_order, fill_value="0")
    for column in header_order:
        df_items[column] = df_items[column].map(normalize_id)
    df_items = df_items.sort_values(by="ID", key=lambda series: series.map(as_int), kind="stable")

    print(f"Saving final converted file to {final_output_file}...")
    df_items.to_csv(final_output_file, index=False)
    write_dbc_ready_item_csv(df_items, header_order)

    generated_report.to_csv(GENERATED_VARIANTS_FILE, index=False)
    orphan_report.to_csv(ORPHAN_APPEARANCES_FILE, index=False)
    display_targets = build_display_targets(df_item_appearance, df_itemdisplay_targets)
    write_coverage_report(df_items, display_targets)

    unique_display_count = display_targets["ItemDisplayInfoID"].nunique()
    covered_display_count = df_items.loc[~df_items["DisplayInfoID"].isin(["", "0"]), "DisplayInfoID"].nunique()
    print(f"Final rows: {len(df_items)}")
    print(f"Generated item variants: {len(generated_report)}")
    print(f"Orphan ItemAppearance rows cloned from nearest source: {len(orphan_report)}")
    print(f"Unique ItemDisplayInfo IDs covered: {covered_display_count}/{unique_display_count}")
    print(f"Outputs: {final_output_file}, {DBC_READY_ITEM_FILE}")
    print(f"Reports: {GENERATED_VARIANTS_FILE}, {ORPHAN_APPEARANCES_FILE}, {COVERAGE_REPORT_FILE}")


if __name__ == "__main__":
    convert_tww_to_wotlk(
        "ItemTWW.csv",
        "Item.csv",
        "itemmodifiedappearance.csv",
        "itemappearance.csv",
        "Converted_Item.csv",
        "Final_Converted_Item.csv",
    )
