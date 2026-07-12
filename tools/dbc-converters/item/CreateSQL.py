import csv
import logging
import re
from pathlib import Path

import pandas as pd

# Setup logging
log_file = "script_debug.log"
logging.basicConfig(
    filename=log_file,
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logging.getLogger().addHandler(console_handler)

# File paths
input_csv_path = "Final_Converted_Item.csv"
itemsparse_csv_path = "itemsparse.csv"
eunoia_item_template_path = "EunoiaItemTemplate.sql"
output_sql_path = "Generated_Items.sql"
output_item_info_path = "Item_Info.csv"
output_enriched_item_path = "Final_Converted_Item_EunoiaFilled.csv"
unknown_icon_name = "inv_misc_questionmark"
generated_variants_path = "Generated_Item_Variants.csv"
itemdisplayinfo_icon_path = Path("..") / "ItemDisplayInfo" / "2 ItemDisplayInfo dbc" / "ItemDisplayInfo_WotLK_Converted.csv"
itemappearance_csv_path = Path("itemappearance.csv")
listfile_csv_path = Path("..") / "ItemDisplayInfo" / "2 ItemDisplayInfo dbc" / "listfile.csv"

required_columns = [
    "ID",
    "ClassID",
    "SubClassID",
    "Sound_override_subclassID",
    "Material",
    "DisplayInfoID",
    "InventoryType",
    "SheatheType",
]


def sql_string(value):
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def load_item_names():
    logging.info("Loading item names from itemsparse.csv...")
    item_names = pd.read_csv(
        itemsparse_csv_path,
        usecols=["ID", "Display_lang"],
        dtype=str,
        keep_default_na=False,
        na_values=[],
    )
    item_names["ID"] = item_names["ID"].str.strip()
    item_names["Display_lang"] = item_names["Display_lang"].str.strip()
    return item_names.set_index("ID")["Display_lang"].to_dict()


def load_generated_variants():
    path = Path(generated_variants_path)
    if not path.exists():
        logging.info("No generated item variant report found.")
        return {}

    logging.info("Loading generated item variant report...")
    variants = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    required = {"GeneratedItemID", "SourceItemID", "ItemDisplayInfoID"}
    if not required.issubset(variants.columns):
        logging.warning("Generated item variant report is missing required columns.")
        return {}

    for column in variants.columns:
        variants[column] = variants[column].astype(str).str.strip()
    return variants.set_index("GeneratedItemID").to_dict("index")


def load_display_icons():
    path = itemdisplayinfo_icon_path
    if not path.exists():
        logging.info("ItemDisplayInfo icon CSV not found; item info will use the question mark fallback.")
        return {}

    logging.info("Loading display icons from ItemDisplayInfo conversion output...")
    icons = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    if "ID" not in icons.columns or "InventoryIcon_1" not in icons.columns:
        logging.warning("ItemDisplayInfo icon CSV does not have ID/InventoryIcon_1 columns.")
        return {}

    icons["ID"] = icons["ID"].astype(str).str.strip()
    icons["InventoryIcon_1"] = icons["InventoryIcon_1"].astype(str).str.strip()
    icons = icons[icons["InventoryIcon_1"] != ""]
    return icons.set_index("ID")["InventoryIcon_1"].to_dict()


def icon_name_from_resource_path(resource_path):
    text = str(resource_path).strip().replace("/", "\\")
    if not text:
        return ""
    base = text.rsplit("\\", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base


def load_default_appearance_icons():
    if not itemappearance_csv_path.exists() or not listfile_csv_path.exists():
        logging.info("ItemAppearance/listfile icon fallback unavailable.")
        return {}

    logging.info("Loading default ItemAppearance icons from listfile...")
    appearances = pd.read_csv(itemappearance_csv_path, dtype=str, keep_default_na=False, na_values=[])
    listfile = pd.read_csv(
        listfile_csv_path,
        sep=";",
        names=["FileDataID", "ResourcePath"],
        dtype=str,
        keep_default_na=False,
        na_values=[],
    )
    if "ItemDisplayInfoID" not in appearances.columns or "DefaultIconFileDataID" not in appearances.columns:
        return {}

    file_paths = {
        str(file_data_id).strip(): str(resource_path).strip()
        for file_data_id, resource_path in zip(listfile["FileDataID"], listfile["ResourcePath"])
        if str(file_data_id).strip() and str(resource_path).strip()
    }

    icons = {}
    for _, row in appearances.iterrows():
        display_id = str(row.get("ItemDisplayInfoID", "")).strip()
        file_data_id = str(row.get("DefaultIconFileDataID", "")).strip()
        if not display_id or display_id == "0" or not file_data_id or file_data_id == "0":
            continue
        icon_name = icon_name_from_resource_path(file_paths.get(file_data_id, ""))
        if icon_name:
            icons.setdefault(display_id, icon_name)
    return icons


def split_sql_tuple_values(tuple_text):
    reader = csv.reader(
        [tuple_text],
        delimiter=",",
        quotechar="'",
        escapechar="\\",
        skipinitialspace=True,
    )
    return next(reader)


def iter_sql_tuples(values_text):
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


def load_eunoia_items():
    logging.info("Loading fallback names/display IDs from EunoiaItemTemplate.sql...")
    eunoia_items = {}

    with open(eunoia_item_template_path, "r", encoding="utf-8", errors="replace") as sql_file:
        sql_text = sql_file.read()

    insert_re = re.compile(
        r"INSERT\s+INTO\s+`item_template`\s*\((?P<columns>.*?)\)\s*VALUES\s*(?P<values>.*?);",
        re.IGNORECASE | re.DOTALL,
    )

    for match in insert_re.finditer(sql_text):
        columns = [column.strip().strip("`") for column in match.group("columns").split(",")]
        required = ["entry", "name", "displayid"]
        if not all(column in columns for column in required):
            continue

        entry_index = columns.index("entry")
        name_index = columns.index("name")
        displayid_index = columns.index("displayid")

        for tuple_text in iter_sql_tuples(match.group("values")):
            values = split_sql_tuple_values(tuple_text)
            if len(values) <= max(entry_index, name_index, displayid_index):
                continue

            item_id = values[entry_index].strip()
            item_name = values[name_index].strip()
            display_id = values[displayid_index].strip()
            if item_id:
                eunoia_items[item_id] = {
                    "Name": item_name,
                    "DisplayInfoID": display_id,
                }

    logging.info(f"Loaded {len(eunoia_items)} fallback items from EunoiaItemTemplate.sql")
    return eunoia_items


def load_items():
    logging.info("Loading converted item CSV...")
    item_data = pd.read_csv(input_csv_path, dtype=str, keep_default_na=False, na_values=[])
    missing_columns = [col for col in required_columns if col not in item_data.columns]
    if missing_columns:
        raise ValueError(f"CSV file is missing required columns: {missing_columns}")

    for column in required_columns:
        item_data[column] = item_data[column].astype(str).str.strip().replace({"": "0"})

    return item_data


def write_outputs(item_data, item_names):
    eunoia_items = load_eunoia_items()
    generated_variants = load_generated_variants()
    display_icons = load_display_icons()
    default_appearance_icons = load_default_appearance_icons()
    for display_id, icon_name in default_appearance_icons.items():
        display_icons.setdefault(display_id, icon_name)
    unresolved_names = []
    displayid_fallbacks = []
    item_info_rows = []
    enriched_item_data = item_data.copy()

    with open(output_sql_path, "w", encoding="utf-8", newline="") as sql_file:
        for row_index, row in item_data.iterrows():
            item_id = row["ID"]
            item_name = item_names.get(item_id, "").strip()
            eunoia_item = eunoia_items.get(item_id, {})
            generated_variant = generated_variants.get(item_id, {})

            if not item_name:
                item_name = eunoia_item.get("Name", "").strip()
            if not item_name and generated_variant:
                source_item_id = generated_variant.get("SourceItemID", "").strip()
                source_name = item_names.get(source_item_id, "").strip()
                if not source_name:
                    source_name = eunoia_items.get(source_item_id, {}).get("Name", "").strip()
                display_label = generated_variant.get("ItemDisplayInfoID", "").strip()
                if source_name and display_label:
                    item_name = f"{source_name} (Display {display_label})"
                elif source_name:
                    item_name = source_name
                elif display_label:
                    item_name = f"Generated Item Display {display_label}"
            if not item_name:
                item_name = "Unknown Item"
                unresolved_names.append(item_id)

            display_id = row["DisplayInfoID"]
            eunoia_display_id = eunoia_item.get("DisplayInfoID", "").strip()
            if display_id in ("", "0") and eunoia_display_id and eunoia_display_id != "0":
                display_id = eunoia_display_id
                displayid_fallbacks.append(item_id)
                enriched_item_data.at[row_index, "DisplayInfoID"] = display_id

            icon_name = display_icons.get(str(display_id).strip(), "").strip()
            if not icon_name:
                icon_name = unknown_icon_name

            sql = (
                "REPLACE INTO `item_template` "
                "(`entry`, `class`, `subclass`, `SoundOverrideSubclass`, `name`, "
                "`displayid`, `InventoryType`, `sheath`, `Material`) "
                f"VALUES ({row['ID']}, {row['ClassID']}, {row['SubClassID']}, "
                f"{row['Sound_override_subclassID']}, '{sql_string(item_name)}', "
                f"{display_id}, {row['InventoryType']}, "
                f"{row['SheatheType']}, {row['Material']});\n"
            )
            sql_file.write(sql)

            item_info_rows.append({
                "ID": item_id,
                "Name": item_name,
                "IconName": icon_name,
            })

    with open(output_item_info_path, "w", encoding="utf-8", newline="") as info_file:
        writer = csv.DictWriter(info_file, fieldnames=["ID", "Name", "IconName"])
        writer.writeheader()
        writer.writerows(item_info_rows)

    enriched_item_data.to_csv(output_enriched_item_path, index=False)

    if unresolved_names:
        with open("unresolved_item_names.csv", "w", encoding="utf-8", newline="") as unresolved_file:
            writer = csv.writer(unresolved_file)
            writer.writerow(["ID", "Reason"])
            for item_id in unresolved_names:
                writer.writerow([item_id, "No Display_lang in itemsparse.csv and no name in EunoiaItemTemplate.sql"])

    if displayid_fallbacks:
        with open("eunoia_displayid_fallbacks.csv", "w", encoding="utf-8", newline="") as fallback_file:
            writer = csv.writer(fallback_file)
            writer.writerow(["ID", "Reason"])
            for item_id in displayid_fallbacks:
                writer.writerow([item_id, "DisplayInfoID was 0 locally and was filled from EunoiaItemTemplate.sql"])

    logging.info(f"SQL file generation complete: {output_sql_path}")
    logging.info(f"Item info saved to {output_item_info_path}")
    logging.info(f"Enriched item DBC CSV saved to {output_enriched_item_path}")
    logging.info(f"Unresolved item names: {len(unresolved_names)}")
    logging.info(f"DisplayInfoID values filled from EunoiaItemTemplate.sql: {len(displayid_fallbacks)}")
    print(f"SQL file generation complete: {output_sql_path}")
    print(f"Item info saved to {output_item_info_path}")
    print(f"Enriched item DBC CSV saved to {output_enriched_item_path}")
    print(f"Unresolved item names: {len(unresolved_names)}")
    print(f"DisplayInfoID values filled from EunoiaItemTemplate.sql: {len(displayid_fallbacks)}")


def main():
    item_data = load_items()
    item_names = load_item_names()
    write_outputs(item_data, item_names)


if __name__ == "__main__":
    main()
