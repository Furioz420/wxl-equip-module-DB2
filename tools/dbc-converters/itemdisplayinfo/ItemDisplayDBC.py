import pandas as pd
import os
import re
import csv
import struct

# Define file paths
file_tww = "ItemDisplayInfo.csv"  # Input file (TWW format)
file_wotlk_converted = "ItemDisplayInfo_WotLK_Converted.csv"  # Output file
file_wxl_item_display_models = "WXLItemDisplayModels.csv"  # WXL sidecar model attachment data
file_wxl_item_display_model_materials = "WXLItemDisplayModelMaterials.csv"  # WXL sidecar model texture layers
file_model_data = "ModelFileData.csv"  # Model resource lookup file
file_listfile = "listfile.csv"  # FileDataID to path mapping file
file_log = "conversion_log.txt"  # Log file
file_texture_data = "TextureFileData.csv"  # Texture lookup file
file_material_res = "ItemDisplayInfoMaterialRes.csv"  # Material resources file
file_model_mat_res = "ItemDisplayInfoModelMatRes.csv"  # Model material resources file
file_unresolved_icons = "unresolved_inventory_icons.csv"  # Missing icon report for this conversion step
UNKNOWN_ICON_NAME = "inv_misc_questionmark"
file_item = "item.csv"
file_item_appearance = "itemappearance.csv"
file_item_modified_appearance = "itemmodifiedappearance.csv"

VERBOSE_CONSOLE_LOG = False

OBJECT_COMPONENT_PATH_MARKER = "item/objectcomponents/"
COLLECTION_PATH_MARKER = "item/objectcomponents/collections/"
COLLECTION_ATTACHMENT = 19
COLLECTION_HIDE_BUILT_IN_FLAG = 0x1
COLLECTION_NO_GENDER_FLAG = 0x2
COLLECTION_NO_RACE_FLAG = 0x4
COLLECTION_NEW_SUFFIX_FLAG = 0x40
MODEL_FORCE_RACE_GENDER_FLAG = 0x80
COLLECTION_SYNTHESIS_SEARCH_RADIUS = 12
COLLECTION_USE_ALL_SKIN_GEOSETS = False
RACE_GENDER_SUFFIX_CODES = (
    "be", "dr", "dt", "dw", "ed", "gn", "go", "hr", "hu", "kt", "mg",
    "na", "nb", "ni", "or", "pa", "sc", "ta", "tr", "vu", "wo", "za",
)
RACE_GENDER_CODE_PATTERN = r"(?:{})".format("|".join(RACE_GENDER_SUFFIX_CODES))
# Only these inventory types may borrow a nearby collection model when the
# retail row has no collection model of its own. Shoulders use this only as an
# extra semicolon overlay next to the normal left/right shoulder models.
COLLECTION_SYNTHESIZED_INVENTORY_TYPES = {3, 4, 5, 6, 7, 8, 9, 10, 19, 20}
SLOT_ATTACHMENT_OVERRIDES = {
    1: ("11", "55"),  # Head
    3: ("6", "5"),    # Shoulder
    4: ("34", "34"),  # Shirt
    5: ("34", "34"),  # Chest
    6: ("53", "53"),  # Waist
    7: ("9", "10"),   # Legs
    8: ("47", "48"),  # Feet
    9: ("3", "4"),    # Wrist
    10: ("1", "2"),   # Hands
    16: ("12", "12"), # Cloak
    19: ("34", "34"), # Tabard
    20: ("34", "34"), # Robe
}
def env_path_list(name):
    raw = os.environ.get(name, "")
    return [part.strip() for part in raw.split(os.pathsep) if part.strip()]


CLIENT_OBJECT_COMPONENT_ROOTS = env_path_list("WXL_CLIENT_OBJECT_COMPONENT_ROOTS")
CLIENT_COLLECTION_ROOTS = env_path_list("WXL_CLIENT_COLLECTION_ROOTS")
if not CLIENT_COLLECTION_ROOTS:
    CLIENT_COLLECTION_ROOTS = [
        os.path.join(root, "collections")
        for root in CLIENT_OBJECT_COMPONENT_ROOTS
    ]
SKIN_HEADER = struct.Struct("<4s12I")
SKIN_SECTION_SIZE = 48
SKIN_BATCH_SIZE = 24
SKIN_BATCH_SECTION_INDEX_OFFSET = 4
SKIN_SEARCH_COMPONENT_FOLDERS = [
    "collections",
    "head",
    "shoulder",
    "chest",
    "waist",
    "leg",
    "foot",
    "bracer",
    "glove",
    "cape",
    "shield",
    "weapon",
]

# Retail collection M2s are full-body/shared armor models. wxl-equip-extension
# enables them by adding ":geoset[,geoset]" to ModelName_X and attaching at root.
INVENTORY_COLLECTION_GEOSETS = {
    4: [(2200, 0)],              # Shirt
    5: [(2200, 0)],              # Chest
    6: [(1100, 0)],              # Waist
    7: [(1300, 2)],              # Legs
    8: [(2000, 0)],              # Feet
    9: [(2300, 0)],              # Wrist
    10: [(400, 0)],              # Hands
    19: [(2200, 0)],             # Tabard
    20: [(2200, 0), (1300, 2)],  # Robe
}

COLLECTION_SKIN_GEOSET_GROUPS = {
    1: [27],              # Head special helm overlay
    3: [4, 26],           # Shoulder normal + special overlay
    4: [10, 11, 22],      # Shirt / torso overlays
    5: [11, 22, 10],      # Chest / torso
    6: [8],               # Waist / belt
    7: [13, 11],          # Legs / pants
    8: [5, 20],           # Feet / boots / toes
    9: [3, 23],           # Wrist / lower arm
    10: [4, 3],           # Hands / gloves
    19: [9, 22],          # Tabard / torso overlay
    20: [12, 13, 11, 22], # Robe / long chest
}

# ItemAppearance.DisplayType uses retail appearance slots. Convert it to the
# WotLK inventory slot ids used by the collection geoset rules above.
DISPLAY_TYPE_TO_INVENTORY_TYPE = {
    0: 1,   # Head
    1: 3,   # Shoulder
    3: 5,   # Chest
    4: 6,   # Waist
    5: 7,   # Legs
    6: 8,   # Feet
    7: 9,   # Wrist
    8: 10,  # Hands
    9: 16,  # Cloak
}

GEOSET_GROUP_LABELS = {
    0: "standalone / filename inferred",
    3: "gloves / lower arm",
    4: "hands / gloves",
    5: "boots / feet",
    8: "belt / waist",
    9: "tabard / torso overlay",
    10: "shirt / torso",
    11: "chest / torso or collection pants",
    12: "robe / long chest",
    13: "legs / pants",
    14: "cloak / back",
    20: "boots / toes",
    22: "torso",
    23: "wrist / bracer",
    26: "shoulder special overlay",
    27: "helm special overlay",
}

COMPONENT_SLOT_CODES = {
    "au": "chest",
    "al": "bracer",
    "ha": "glove",
    "tu": "chest",
    "tl": "chest",
    "lu": "pant",
    "ll": "pant",
    "ft": "boot",
    "fo": "boot",
}

COLLECTION_FILENAME_GEOSET_HINTS = [
    (("robe",), [(2200, 0), (1300, 2)]),
    (("chest", "shirt", "tabard"), [(2200, 0)]),
    (("pant", "pants", "leg"), [(1300, 2)]),
    (("boot", "feet", "foot"), [(2000, 0)]),
    (("glove", "hand"), [(400, 0)]),
    (("belt", "waist", "buckle"), [(1100, 0)]),
    (("bracer", "wrist"), [(2300, 0)]),
]

COLLECTION_SLOT_TOKENS = [
    ("shoulder", ("shoulder",)),
    ("bracer", ("bracer", "wrist")),
    ("glove", ("glove", "hand")),
    ("chest", ("chest", "robe", "shirt", "tabard")),
    ("belt", ("belt", "waist", "buckle")),
    ("pant", ("pant", "pants", "leg")),
    ("boot", ("boot", "feet", "foot")),
    ("helm", ("helm", "helmet", "head")),
]

COLLECTION_PREFERRED_MODEL_SLOT = {
    3: "shoulder", # Shoulder overlay appended to ModelName_1 with semicolon
    4: "chest",   # Shirt
    5: "chest",   # Chest
    6: "belt",    # Waist
    7: "pant",    # Legs
    8: "boot",    # Feet
    9: "bracer",  # Wrist
    10: "glove",  # Hands
    19: "chest",  # Tabard
    20: "chest",  # Robe
}

# Open log file
log_file = open(file_log, "w")

def log(message):
    if VERBOSE_CONSOLE_LOG:
        print(message)
    log_file.write(message + "\n")
    log_file.flush()  # Ensure log is written immediately

# Define WotLK header structure
wotlk_columns = [
    "ID", "ModelName_1", "ModelName_2", "ModelTexture_1", "ModelTexture_2", 
    "InventoryIcon_1", "InventoryIcon_2", "GeosetGroup_1", "GeosetGroup_2", "GeosetGroup_3", 
    "Flags", "SpellVisualID", "GroupSoundIndex", "HelmetGeosetVis_1", "HelmetGeosetVis_2", 
    "Texture_1", "Texture_2", "Texture_3", "Texture_4", "Texture_5", "Texture_6", "Texture_7", "Texture_8", 
    "ItemVisual", "ParticleColorId"
]

wxl_sidecar_columns = [
    "DisplayID",
    "ItemID",
    "Slot",
    "Layer",
    "Side",
    "Folder",
    "Model",
    "Texture",
    "Geosets",
    "Attach",
    "SuffixPolicy",
    "TexturePolicy",
    "Flags",
]

wxl_model_material_columns = [
    "DisplayID",
    "ModelIndex",
    "ModelColumn",
    "Model",
    "Layer",
    "TextureType",
    "MaterialResourcesID",
    "FileDataID",
    "Folder",
    "Texture",
    "SkinSectionIDs",
    "BatchIndexes",
    "TargetSkinSectionIDs",
    "TargetBatchIndexes",
    "TargetMode",
]

INVENTORY_TYPE_TO_WXL_SLOT = {
    1: "Head",
    3: "Shoulder",
    4: "Shirt",
    5: "Chest",
    6: "Waist",
    7: "Leg",
    8: "Foot",
    9: "Bracer",
    10: "Glove",
    16: "Cape",
    19: "Tabard",
    20: "Chest",
}

INVENTORY_TYPE_TO_OBJECT_FOLDER = {
    1: "Head",
    3: "Shoulder",
    4: "Shirt",
    5: "Chest",
    6: "Waist",
    7: "Leg",
    8: "Foot",
    9: "Bracer",
    10: "Glove",
    16: "Cape",
    19: "Tabard",
    20: "Chest",
}

if os.path.exists(file_wotlk_converted):
    os.remove(file_wotlk_converted)
    log("Deleted existing output file.")
if os.path.exists(file_wxl_item_display_models):
    os.remove(file_wxl_item_display_models)
    log("Deleted existing WXL sidecar file.")
if os.path.exists(file_wxl_item_display_model_materials):
    os.remove(file_wxl_item_display_model_materials)
    log("Deleted existing WXL model material sidecar file.")

log("Loading TWW data...")
df_tww = pd.read_csv(file_tww, dtype={'ID': int})
if 'ID' not in df_tww.columns:
    log("Error: 'ID' column not found in TWW data. Check the input file.")
    exit()
df_model_data = pd.read_csv(file_model_data)
df_listfile = pd.read_csv(file_listfile, sep=';', names=['FileDataID', 'ResourcePath'])
df_texture_data = pd.read_csv(file_texture_data)
df_material_res = pd.read_csv(file_material_res)
df_model_mat_res = pd.read_csv(file_model_mat_res)
df_item = pd.read_csv(file_item)
df_item_appearance = pd.read_csv(file_item_appearance)
df_item_modified_appearance = pd.read_csv(file_item_modified_appearance)
df_model_data.columns = df_model_data.columns.str.strip()
df_listfile['FileDataID'] = pd.to_numeric(df_listfile['FileDataID'], errors='coerce')
listfile_paths = {
    int(file_data_id): resource_path
    for file_data_id, resource_path in zip(df_listfile['FileDataID'], df_listfile['ResourcePath'])
    if pd.notna(file_data_id) and pd.notna(resource_path)
}
model_resource_file_data_rows = {}
for model_resource_id, file_data_id in zip(df_model_data['ModelResourcesID'], df_model_data['FileDataID']):
    if pd.notna(model_resource_id) and pd.notna(file_data_id):
        model_resource_file_data_rows.setdefault(int(model_resource_id), []).append(int(file_data_id))
model_resource_to_file_data = {}
texture_material_to_file_data = dict(zip(df_texture_data.iloc[:, 2], df_texture_data.iloc[:, 0]))
item_display_to_inventory_types = {}
item_display_to_display_types = {}
appearance_to_display = dict(zip(df_item_appearance['ID'], df_item_appearance['ItemDisplayInfoID']))
item_to_inventory_type = dict(zip(df_item['ID'], df_item['InventoryType']))
display_material_rows = {}
display_model_material_rows = {}

for _, appearance_row in df_item_appearance.iterrows():
    item_display_id = appearance_row.get('ItemDisplayInfoID', 0)
    display_type = appearance_row.get('DisplayType', 0)
    if pd.notna(item_display_id) and pd.notna(display_type):
        item_display_to_display_types.setdefault(int(item_display_id), set()).add(int(display_type))

for _, modified_appearance_row in df_item_modified_appearance.iterrows():
    appearance_id = modified_appearance_row.get('ItemAppearanceID', 0)
    item_id = modified_appearance_row.get('ItemID', 0)
    item_display_id = appearance_to_display.get(appearance_id)
    inventory_type = item_to_inventory_type.get(item_id)
    if pd.notna(item_display_id) and pd.notna(inventory_type):
        item_display_to_inventory_types.setdefault(int(item_display_id), set()).add(int(inventory_type))

for _, material_row in df_material_res.iterrows():
    item_display_id = material_row.iloc[3]
    if pd.notna(item_display_id):
        display_material_rows.setdefault(int(item_display_id), []).append((
            int(material_row.iloc[1]),
            material_row.iloc[2],
        ))

for _, material_row in df_model_mat_res.iterrows():
    item_display_id = material_row.get('ItemDisplayInfoID', 0)
    model_index = material_row.get('ModelIndex', 0)
    material_res_id = material_row.get('MaterialResourcesID', 0)
    texture_type = material_row.get('TextureType', 0)
    if (
        pd.notna(item_display_id)
        and pd.notna(model_index)
        and pd.notna(material_res_id)
        and int(material_res_id) != 0
    ):
        display_model_material_rows.setdefault(int(item_display_id), {}).setdefault(int(model_index), []).append({
            "material_res_id": material_res_id,
            "texture_type": int(texture_type) if pd.notna(texture_type) else 0,
            "row_id": int(material_row.get('ID', 0)) if pd.notna(material_row.get('ID', 0)) else 0,
        })

for model_rows_by_index in display_model_material_rows.values():
    for model_rows in model_rows_by_index.values():
        model_rows.sort(key=lambda entry: (entry["texture_type"], entry["row_id"], int(entry["material_res_id"])))

def normalize_resource_path(resource_path):
    return str(resource_path).replace('\\', '/').lower()

def resource_path_after_marker(resource_path, marker):
    normalized = normalize_resource_path(resource_path)
    marker_index = normalized.find(marker)
    if marker_index == -1:
        return os.path.basename(resource_path)
    return resource_path[marker_index + len(marker):].replace('/', '\\')

def object_component_folder_from_resource_path(resource_path, include_collections=False):
    if not resource_path:
        return ""

    normalized = normalize_resource_path(resource_path)
    marker_index = normalized.find(OBJECT_COMPONENT_PATH_MARKER)
    if marker_index == -1:
        return ""

    after_marker = normalized[marker_index + len(OBJECT_COMPONENT_PATH_MARKER):]
    folder = after_marker.split('/', 1)[0]
    if not folder:
        return ""
    if folder == "collections":
        return "Collections" if include_collections else ""
    return folder[:1].upper() + folder[1:]

def object_component_folder_from_file_data(file_data_id, include_collections=False):
    resource_path = listfile_paths.get(int(file_data_id))
    return object_component_folder_from_resource_path(resource_path, include_collections)

def object_component_icon2_override(folder):
    normalized_folder = str(folder).lower()
    if normalized_folder == "cape":
        return "Cape", "12"
    if normalized_folder == "collections":
        return "Collections", ""
    return "", ""

texture_component_stems_by_dir = {}
inventory_icon_stems = {}
for resource_path in listfile_paths.values():
    normalized_path = normalize_resource_path(resource_path)
    if normalized_path.startswith("interface/icons/") and normalized_path.endswith((".blp", ".tga")):
        icon_stem = os.path.splitext(os.path.basename(resource_path))[0]
        inventory_icon_stems.setdefault(icon_stem.lower(), icon_stem)

    if "item/texturecomponents/" not in normalized_path or not normalized_path.endswith((".blp", ".tga")):
        continue

    component_dir = os.path.dirname(normalized_path).lower()
    component_stem = os.path.splitext(os.path.basename(normalized_path))[0].lower()
    texture_component_stems_by_dir.setdefault(component_dir, set()).add(component_stem)

def race_gender_suffix_patterns(extension):
    escaped_extension = re.escape(extension)
    return (
        r"_" + RACE_GENDER_CODE_PATTERN + r"_[mf]" + escaped_extension + r"$",
        r"_" + RACE_GENDER_CODE_PATTERN + r"[mf]" + escaped_extension + r"$",
    )

def strip_race_gender_suffix(name, extension):
    for suffix_pattern in race_gender_suffix_patterns(extension):
        name = re.sub(suffix_pattern, extension, name, flags=re.IGNORECASE)
    return name

def uses_new_race_gender_suffix(name, extension):
    return re.search(
        r"_" + RACE_GENDER_CODE_PATTERN + r"_[mf]" + re.escape(extension) + r"$",
        name,
        re.IGNORECASE,
    ) is not None

def uses_race_gender_suffix(name, extension):
    return any(
        re.search(suffix_pattern, name, re.IGNORECASE) is not None
        for suffix_pattern in race_gender_suffix_patterns(extension)
    )

def normalize_collection_family_name(family):
    family = str(family).lower().strip('_')
    family = re.sub(r'_\d{5,}$', '', family)
    family = re.sub(r'_[lr]$', '', family)
    for prefix in ("collections_", "chest_", "robe_", "shirt_", "tabard_", "belt_", "pant_", "leg_", "boot_", "glove_", "hand_", "bracer_", "wrist_"):
        if family.startswith(prefix):
            return family[len(prefix):]
    return family

def strip_collection_slot_suffix(family):
    family = normalize_collection_family_name(family)
    for _, tokens in COLLECTION_SLOT_TOKENS:
        for token in tokens:
            suffix = f"_{token}"
            if family.endswith(suffix):
                return family[:-len(suffix)]
    return family

def split_collection_family_slot(stem):
    stem = strip_race_gender_suffix(os.path.splitext(os.path.basename(str(stem)))[0], '')
    lower_stem = stem.lower()

    component_match = re.match(r'^(.+)_([a-z]{2})_[mfu]_\d{5,}$', lower_stem, re.IGNORECASE)
    if component_match and component_match.group(2).lower() in COMPONENT_SLOT_CODES:
        return strip_collection_slot_suffix(component_match.group(1)), COMPONENT_SLOT_CODES[component_match.group(2).lower()]

    component_match = re.match(r'^(.+)_([a-z]{2})_\d{5,}$', lower_stem, re.IGNORECASE)
    if component_match and component_match.group(2).lower() in COMPONENT_SLOT_CODES:
        return strip_collection_slot_suffix(component_match.group(1)), COMPONENT_SLOT_CODES[component_match.group(2).lower()]

    for slot, tokens in COLLECTION_SLOT_TOKENS:
        for token in tokens:
            for prefix in (f"{token}_", f"collections_{token}_"):
                if lower_stem.startswith(prefix):
                    return normalize_collection_family_name(stem[len(prefix):]), slot
            if lower_stem.startswith(token + "_"):
                return normalize_collection_family_name(stem[len(token) + 1:]), slot
            marker = f"_{token}"
            index = lower_stem.find(marker + "_")
            if index == -1 and lower_stem.endswith(marker):
                index = len(lower_stem) - len(marker)
            if index > 0:
                return normalize_collection_family_name(stem[:index]), slot
    return normalize_collection_family_name(stem), ""

def collection_model_uses_slot_attachment(inventory_type, model_name):
    _, collection_slot = split_collection_family_slot(model_name)
    return (
        (inventory_type == 1 and collection_slot == "helm")
        or (inventory_type == 3 and collection_slot == "shoulder")
        or (inventory_type == 6 and collection_slot == "belt")
    )

def collection_model_side(model_name):
    stem = strip_race_gender_suffix(os.path.splitext(os.path.basename(str(model_name)))[0], '')
    lower_stem = stem.lower()
    if lower_stem.endswith("_l") or "_shoulder_l" in lower_stem:
        return "l"
    if lower_stem.endswith("_r") or "_shoulder_r" in lower_stem:
        return "r"
    return ""

def slot_attach_override_for_model(inventory_type, model_name, component_folder, model_index):
    if not model_name:
        return ""

    folder = str(component_folder or "").lower()
    _, collection_slot = split_collection_family_slot(model_name)
    lower_model = str(model_name).lower()
    slot_attachments = SLOT_ATTACHMENT_OVERRIDES.get(inventory_type)
    if not slot_attachments:
        return ""

    def attachment_for_index():
        return slot_attachments[0] if model_index == 1 else slot_attachments[1]

    if inventory_type == 3 and (collection_slot == "shoulder" or "_shoulder_" in lower_model):
        return attachment_for_index()

    if inventory_type == 6 and (
        collection_slot == "belt"
        or folder == "waist"
        or "_belt" in lower_model
        or lower_model.startswith("buckle_")
    ):
        return attachment_for_index()

    expected_slot = COLLECTION_PREFERRED_MODEL_SLOT.get(inventory_type, "")
    if expected_slot and collection_slot == expected_slot:
        return attachment_for_index()

    return ""

def preferred_model_variant_rank(resource_path):
    basename = os.path.basename(normalize_resource_path(resource_path))
    preferred_suffixes = (
        "_hu_m.m2",
        "_hu_f.m2",
        "_be_m.m2",
        "_be_f.m2",
        "_or_m.m2",
        "_or_f.m2",
        "_ed_m.m2",
        "_ed_f.m2",
        "_hr_m.m2",
        "_hr_f.m2",
    )
    for rank, suffix in enumerate(preferred_suffixes):
        if basename.endswith(suffix):
            return rank
    if uses_new_race_gender_suffix(basename, '.m2'):
        return len(preferred_suffixes)
    if uses_race_gender_suffix(basename, '.m2'):
        return len(preferred_suffixes) + 1
    return len(preferred_suffixes) + 2

def build_model_resource_to_file_data():
    resolved = {}
    for model_resource_id, file_data_ids in model_resource_file_data_rows.items():
        candidates = []
        base_counts = {}
        for file_data_id in file_data_ids:
            resource_path = listfile_paths.get(int(file_data_id))
            if not resource_path:
                continue

            basename = os.path.basename(resource_path)
            if not basename.lower().endswith('.m2'):
                continue

            base_name = strip_race_gender_suffix(basename, '.m2').lower()
            base_counts[base_name] = base_counts.get(base_name, 0) + 1
            candidates.append((file_data_id, resource_path, base_name))

        if not candidates:
            continue

        def candidate_rank(candidate):
            file_data_id, resource_path, base_name = candidate
            normalized_path = normalize_resource_path(resource_path)
            return (
                -base_counts.get(base_name, 0),
                0 if COLLECTION_PATH_MARKER in normalized_path else 1,
                preferred_model_variant_rank(resource_path),
                int(file_data_id),
            )

        resolved[model_resource_id] = sorted(candidates, key=candidate_rank)[0][0]
    return resolved

def get_model_info(file_data_id):
    model_path = listfile_paths.get(int(file_data_id))
    if model_path:
        is_collection = COLLECTION_PATH_MARKER in normalize_resource_path(model_path)
        model_name = resource_path_after_marker(model_path, COLLECTION_PATH_MARKER) if is_collection else os.path.basename(model_path)
        model_name = model_name.replace('.m2', '.mdx')
        model_dir = os.path.dirname(model_name)
        model_filename = os.path.basename(model_name)
        skin_stem = os.path.splitext(model_filename)[0]
        uses_new_suffix = uses_new_race_gender_suffix(model_filename, '.mdx')
        uses_suffix = uses_race_gender_suffix(model_filename, '.mdx')
        model_base = strip_race_gender_suffix(model_filename, '.mdx')
        model_name = os.path.join(model_dir, model_base) if model_dir else model_base
        return model_name, is_collection, uses_new_suffix, uses_suffix, skin_stem
    return "", False, False, False, ""

def get_model_name(file_data_id):
    model_name, _, _, _, _ = get_model_info(file_data_id)
    return model_name

model_resource_to_file_data = build_model_resource_to_file_data()

df_item_icons = pd.read_csv("ItemIcons.csv", dtype=str)

# Create a dictionary mapping ItemDisplayInfoID to a list of possible icons
icon_map = {}
log("Initializing icon mapping...")
for _, row in df_item_icons.iterrows():
    item_display_id = str(row['ItemDisplayInfoID']).strip()
    icon_file_data_id = str(row['IconFileDataID']).strip()
    if item_display_id not in icon_map:
        icon_map[item_display_id] = []
    icon_map[item_display_id].append(icon_file_data_id)

log(f"Mapped {len(icon_map)} unique ItemDisplayInfoIDs to icons.")

appearance_icon_map = {}
for _, row in df_item_appearance.iterrows():
    item_display_id = str(row.get('ItemDisplayInfoID', '')).strip()
    icon_file_data_id = str(row.get('DefaultIconFileDataID', '')).strip()
    if not item_display_id or item_display_id == "0" or not icon_file_data_id or icon_file_data_id == "0":
        continue
    appearance_icon_map.setdefault(item_display_id, icon_file_data_id)

log(f"Mapped {len(appearance_icon_map)} ItemAppearance default icons.")

log("Data loaded successfully.")

# Ensure correct column names by stripping spaces
df_model_data.columns = df_model_data.columns.str.strip()

def get_texture_name(file_data_id, preserve_collection_path=False, preserve_unisex_suffix=False):
    texture_path = listfile_paths.get(int(file_data_id))
    if texture_path:
        is_collection = COLLECTION_PATH_MARKER in normalize_resource_path(texture_path)
        texture_name = resource_path_after_marker(texture_path, COLLECTION_PATH_MARKER) if preserve_collection_path and is_collection else os.path.basename(texture_path)
        texture_dir = os.path.dirname(texture_name)
        texture_name = os.path.basename(texture_name).replace('.blp', '')
        def with_texture_dir(resolved_texture_name):
            return os.path.join(texture_dir, resolved_texture_name) if texture_dir else resolved_texture_name
        # A: {numID}_{middle}_{slot}_{gender}_{numID2}  →  {numID}_{numID2}_{middle}_{slot}  (client adds gender)
        #   7074627_be_m_au_u_7105174  →  7074627_7105174_be_m_au
        #   6210682_be_m_fo_u_6240359  →  6210682_6240359_be_m_fo
        _slots = r'(?:au|al|ha|tu|tl|lu|ll|ft|fo)'
        m = re.match(r'^(\d+)_(.+)_(' + _slots + r')_([mfu])_(\d{5,})$', texture_name, re.IGNORECASE)
        if m:
            unisex = "_u" if preserve_unisex_suffix and m.group(4).lower() == "u" else ""
            return with_texture_dir(f"{m.group(1)}_{m.group(5)}_{m.group(2)}_{m.group(3)}{unisex}")
        # C: {base}_{slot}_{gender}_{numeric}  →  {base}_{numeric}_{slot}  (client adds gender)
        #   armor_troll_..._au_m_5212665  →  armor_troll_..._5212665_au
        #   4548775_ha_u_4554684          →  4548775_4554684_ha
        m = re.match(r'^(.+)_(' + _slots + r')_([mfu])_(\d{5,})$', texture_name, re.IGNORECASE)
        if m:
            unisex = "_u" if preserve_unisex_suffix and m.group(3).lower() == "u" else ""
            return with_texture_dir(f"{m.group(1)}_{m.group(4)}_{m.group(2)}{unisex}")
        # Generic: no slot pattern. Strip terminal sex markers the client appends
        # itself, but keep terminal "_u" for TextureComponents so WXL can alias
        # the client's "..._u_[M/F].blp" request back to the real unisex export.
        suffix_pattern = r'_[mf]$' if preserve_unisex_suffix else r'_[mfu]$'
        while re.search(suffix_pattern, texture_name, flags=re.IGNORECASE):
            texture_name = re.sub(suffix_pattern, '', texture_name, flags=re.IGNORECASE)
        return with_texture_dir(texture_name)
    return ""

def get_model_texture_name(file_data_id, model_component_folder, preserve_collection_path=False):
    texture_name = get_texture_name(file_data_id, preserve_collection_path)
    if not texture_name:
        return ""

    texture_folder = object_component_folder_from_file_data(file_data_id, include_collections=True)
    if (
        texture_folder
        and model_component_folder
        and texture_folder.lower() != str(model_component_folder).lower()
        and "\\" not in texture_name
        and "/" not in texture_name
    ):
        return f"{texture_folder}\\{texture_name}"

    return texture_name

def get_model_mat_res_texture_entries(item_display_id, model_index, model_component_folder="", preserve_collection_path=False):
    entries = []
    for material_entry in display_model_material_rows.get(int(item_display_id), {}).get(int(model_index), []):
        file_data_id = texture_material_to_file_data.get(material_entry["material_res_id"])
        if pd.isna(file_data_id):
            continue

        texture_name = get_model_texture_name(
            file_data_id,
            model_component_folder,
            preserve_collection_path,
        )
        if texture_name and texture_name not in entries:
            entries.append(texture_name)
    return entries

def get_model_mat_res_collection_texture(item_display_id, model_index):
    for material_entry in display_model_material_rows.get(int(item_display_id), {}).get(int(model_index), []):
        texture_name, texture_is_collection, texture_family, texture_slot = get_collection_texture_name_from_material(
            material_entry["material_res_id"],
            True,
        )
        if texture_name and texture_is_collection:
            return texture_name, texture_family, texture_slot
    return "", "", ""

def get_texture_component_raw_name(file_data_id):
    texture_path = listfile_paths.get(int(file_data_id))
    if texture_path:
        return os.path.basename(texture_path).replace('.blp', '')
    return ""

def texture_component_stem_resolves(component_dir, texture_stem):
    component_stems = texture_component_stems_by_dir.get(component_dir.lower(), set())
    texture_stem = texture_stem.lower()
    return (
        texture_stem in component_stems
        or f"{texture_stem}_m" in component_stems
        or f"{texture_stem}_f" in component_stems
    )

def has_unisex_component_marker(texture_stem):
    return bool(re.search(r'_u(?:$|_\d{5,}$)', texture_stem, flags=re.IGNORECASE))

def strip_terminal_component_gender(texture_stem):
    return re.sub(r'_[mf]$', '', texture_stem, flags=re.IGNORECASE)

def get_texture_component_name(file_data_id):
    texture_path = listfile_paths.get(int(file_data_id))
    if not texture_path:
        return ""

    normalized_path = normalize_resource_path(texture_path)
    component_dir = os.path.dirname(normalized_path).lower()
    raw_stem = os.path.splitext(os.path.basename(normalized_path))[0]
    candidates = []

    def add_candidate(texture_stem):
        if texture_stem and texture_stem not in candidates:
            candidates.append(texture_stem)

    # If the real file is unisex, the DBC must keep that _u marker. Otherwise the
    # client asks for "..._[M/F]" and WXL has no way to infer the missing "_u".
    if has_unisex_component_marker(raw_stem):
        add_candidate(raw_stem)

    # Simple old-client sexed names work best as a neutral stem: "foo_tu_m.blp"
    # becomes "foo_tu", and the client requests "foo_tu_M/F.blp".
    if re.search(r'_[mf]$', raw_stem, flags=re.IGNORECASE):
        add_candidate(strip_terminal_component_gender(raw_stem))

    # Keep the existing retail numeric rewrite as a candidate, but only use it
    # when that rewritten stem corresponds to an actual texture component name.
    add_candidate(get_texture_name(file_data_id, preserve_unisex_suffix=True))
    add_candidate(raw_stem)

    for candidate in candidates:
        if texture_component_stem_resolves(component_dir, candidate):
            return candidate

    return raw_stem

collection_skin_sections_cache = {}
collection_skin_ids_cache = {}

def normalize_component_folder_name(component_folder):
    if not component_folder:
        return ""
    return os.path.basename(str(component_folder).replace("\\", "/")).lower()

def skin_search_paths(skin_name, component_folder=""):
    search_folders = []
    preferred_folder = normalize_component_folder_name(component_folder)
    if preferred_folder:
        search_folders.append(preferred_folder)
    for folder in SKIN_SEARCH_COMPONENT_FOLDERS:
        if folder not in search_folders:
            search_folders.append(folder)

    for root in CLIENT_OBJECT_COMPONENT_ROOTS:
        for folder in search_folders:
            yield os.path.join(root, folder, skin_name)
        yield os.path.join(root, skin_name)

    for root in CLIENT_COLLECTION_ROOTS:
        yield os.path.join(root, skin_name)

def read_collection_skin_sections(skin_stem, component_folder=""):
    if not skin_stem:
        return None

    skin_stem = os.path.splitext(os.path.basename(str(skin_stem)))[0]
    cache_key = (normalize_component_folder_name(component_folder), skin_stem)
    if cache_key in collection_skin_sections_cache:
        return collection_skin_sections_cache[cache_key]

    skin_name = f"{skin_stem}00.skin"
    skin_path = ""
    for candidate in skin_search_paths(skin_name, component_folder):
        if os.path.exists(candidate):
            skin_path = candidate
            break

    if not skin_path:
        collection_skin_sections_cache[cache_key] = None
        return None

    try:
        with open(skin_path, "rb") as skin_file:
            data = skin_file.read()
        if len(data) < SKIN_HEADER.size:
            collection_skin_sections_cache[cache_key] = None
            return None

        (
            magic,
            _index_count,
            _index_offset,
            _triangle_count,
            _triangle_offset,
            _property_count,
            _property_offset,
            submesh_count,
            submesh_offset,
            texunit_count,
            texunit_offset,
            _lod,
            _flags,
        ) = SKIN_HEADER.unpack_from(data, 0)
        if magic != b"SKIN":
            collection_skin_sections_cache[cache_key] = None
            return None

        submesh_end = submesh_offset + submesh_count * SKIN_SECTION_SIZE
        if submesh_offset <= 0 or submesh_end > len(data):
            collection_skin_sections_cache[cache_key] = None
            return None

        submesh_skin_ids = []
        for submesh_index in range(submesh_count):
            offset = submesh_offset + submesh_index * SKIN_SECTION_SIZE
            if offset + 4 > len(data):
                break
            submesh_skin_ids.append(struct.unpack_from("<I", data, offset)[0] & 0xFFFF)

        batch_end = texunit_offset + texunit_count * SKIN_BATCH_SIZE
        if texunit_offset <= 0 or batch_end > len(data):
            collection_skin_sections_cache[cache_key] = []
            return []

        sections = []
        for batch_index in range(texunit_count):
            offset = texunit_offset + batch_index * SKIN_BATCH_SIZE
            if offset + SKIN_BATCH_SECTION_INDEX_OFFSET + 2 > len(data):
                break
            skin_section_index = struct.unpack_from("<H", data, offset + SKIN_BATCH_SECTION_INDEX_OFFSET)[0]
            if skin_section_index >= len(submesh_skin_ids):
                continue
            sections.append({
                "batch_index": batch_index,
                "skin_section_index": skin_section_index,
                "skin_section_id": submesh_skin_ids[skin_section_index],
            })
        collection_skin_sections_cache[cache_key] = sections
        return sections
    except OSError:
        collection_skin_sections_cache[cache_key] = None
        return None

def read_collection_skin_ids(skin_stem, component_folder=""):
    if not skin_stem:
        return None

    skin_stem = os.path.splitext(os.path.basename(str(skin_stem)))[0]
    cache_key = (normalize_component_folder_name(component_folder), skin_stem)
    if cache_key in collection_skin_ids_cache:
        return collection_skin_ids_cache[cache_key]

    sections = read_collection_skin_sections(skin_stem, component_folder)
    if sections is None:
        collection_skin_ids_cache[cache_key] = None
        return None

    skin_ids = []
    for section in sections:
        skin_id = section["skin_section_id"]
        if skin_id not in skin_ids:
            skin_ids.append(skin_id)
    collection_skin_ids_cache[cache_key] = skin_ids
    return skin_ids

def int_list_csv(values):
    return ",".join(str(int(value)) for value in values)

def collection_skin_material_target_info(inventory_type, skin_stem, component_folder=""):
    sections = read_collection_skin_sections(skin_stem, component_folder)
    if not sections:
        return {
            "SkinSectionIDs": "",
            "BatchIndexes": "",
            "TargetSkinSectionIDs": "",
            "TargetBatchIndexes": "",
            "TargetMode": "None",
        }

    skin_ids = []
    batch_indexes = []
    for section in sections:
        skin_id = section["skin_section_id"]
        if skin_id > 0 and skin_id not in skin_ids:
            skin_ids.append(skin_id)
        batch_indexes.append(section["batch_index"])

    target_skin_ids = get_collection_skin_geosets(inventory_type, skin_stem, component_folder) or []
    target_batch_indexes = [
        section["batch_index"]
        for section in sections
        if section["skin_section_id"] in target_skin_ids
    ]

    if target_skin_ids:
        target_mode = "SlotGeosets"
    else:
        # Keep the raw skin map in the sidecar even when the script cannot make
        # a safe slot target. WXL should treat this as metadata, not a global
        # material override.
        target_mode = "SkinMapOnly"

    return {
        "SkinSectionIDs": int_list_csv(skin_ids),
        "BatchIndexes": int_list_csv(batch_indexes),
        "TargetSkinSectionIDs": int_list_csv(target_skin_ids),
        "TargetBatchIndexes": int_list_csv(target_batch_indexes),
        "TargetMode": target_mode,
    }

def collection_geoset_group(skin_id):
    if skin_id == 0:
        return 0
    return skin_id // 100

def get_collection_skin_geosets(inventory_type, skin_stem, component_folder=""):
    skin_ids = read_collection_skin_ids(skin_stem, component_folder)
    geoset_groups = COLLECTION_SKIN_GEOSET_GROUPS.get(inventory_type, [])
    if skin_ids is None:
        return None
    if not skin_ids or all(skin_id == 0 for skin_id in skin_ids):
        return None

    if not geoset_groups:
        return None

    if COLLECTION_USE_ALL_SKIN_GEOSETS and inventory_type != 3:
        return [skin_id for skin_id in skin_ids if skin_id > 0]

    geosets = []
    for geoset_group in geoset_groups:
        for skin_id in skin_ids:
            if skin_id > 0 and collection_geoset_group(skin_id) == geoset_group and skin_id not in geosets:
                geosets.append(skin_id)
    return geosets

def collection_skin_has_no_slot_mesh(inventory_type, skin_stem):
    skin_ids = read_collection_skin_ids(skin_stem)
    if skin_ids is None or not skin_ids or all(skin_id == 0 for skin_id in skin_ids):
        return False
    return get_collection_skin_geosets(inventory_type, skin_stem) == []

def build_collection_model_catalogs():
    catalog = {}
    sided_catalog = {}
    for file_data_id, resource_path in listfile_paths.items():
        normalized_path = normalize_resource_path(resource_path)
        if COLLECTION_PATH_MARKER not in normalized_path or not normalized_path.endswith('.m2'):
            continue

        model_name, is_collection, uses_new_suffix, uses_suffix, skin_stem = get_model_info(file_data_id)
        if not model_name or not is_collection:
            continue

        family, slot = split_collection_family_slot(strip_race_gender_suffix(model_name, '.mdx'))
        if not family or not slot:
            continue

        model_entry = {
            "family": family,
            "slot": slot,
            "model_name": model_name,
            "uses_new_suffix": uses_new_suffix,
            "uses_suffix": uses_suffix,
            "skin_stem": skin_stem,
        }

        existing = catalog.get((family, slot))
        if not existing or (uses_suffix and not existing["uses_suffix"]):
            catalog[(family, slot)] = model_entry

        side = collection_model_side(model_name)
        if side:
            existing = sided_catalog.get((family, slot, side))
            if not existing or (uses_suffix and not existing["uses_suffix"]):
                sided_catalog[(family, slot, side)] = model_entry
    return catalog, sided_catalog

def get_collection_texture_name_from_material(material_resource_id, preserve_collection_path=True):
    if pd.isna(material_resource_id) or not material_resource_id or int(material_resource_id) == 0:
        return "", False, "", ""

    file_data_id = texture_material_to_file_data.get(material_resource_id)
    if pd.isna(file_data_id):
        return "", False, "", ""

    texture_path = listfile_paths.get(int(file_data_id))
    if not texture_path:
        return "", False, "", ""

    is_collection = COLLECTION_PATH_MARKER in normalize_resource_path(texture_path)
    texture_name = get_texture_name(file_data_id, preserve_collection_path)
    family, slot = split_collection_family_slot(texture_name)
    return texture_name, is_collection, family, slot

def build_display_collection_sources():
    sources = {}
    for _, display_row in df_tww.iterrows():
        display_id = int(display_row.get('ID', 0))
        for model_index, model_column, texture_column in (
            (0, 'ModelResourcesID[0]', 'ModelMaterialResourcesID[0]'),
            (1, 'ModelResourcesID[1]', 'ModelMaterialResourcesID[1]'),
        ):
            model_res_id = display_row.get(model_column, 0)
            if pd.isna(model_res_id) or not model_res_id or int(model_res_id) == 0:
                continue

            file_data_id = model_resource_to_file_data.get(model_res_id)
            if pd.isna(file_data_id):
                continue

            model_name, is_collection, uses_new_suffix, uses_suffix, skin_stem = get_model_info(file_data_id)
            if not model_name or not is_collection:
                continue

            texture_name, texture_is_collection, texture_family, _ = get_collection_texture_name_from_material(
                display_row.get(texture_column, 0),
                True
            )
            if not texture_name or not texture_is_collection:
                texture_name, texture_family, _ = get_model_mat_res_collection_texture(display_id, model_index)
                texture_is_collection = bool(texture_name)
            if not texture_name or not texture_is_collection:
                continue

            model_family, model_slot = split_collection_family_slot(model_name)
            family = model_family or texture_family
            if not family:
                continue

            sources.setdefault(display_id, []).append({
                "family": family,
                "slot": model_slot,
                "model_name": model_name,
                "texture_name": texture_name,
                "uses_new_suffix": uses_new_suffix,
                "uses_suffix": uses_suffix,
                "skin_stem": skin_stem,
            })
    return sources

def build_display_component_families():
    families = {}
    for display_id, material_rows in display_material_rows.items():
        for _, material_res_id in material_rows:
            file_data_id = texture_material_to_file_data.get(material_res_id)
            if pd.isna(file_data_id):
                continue

            texture_path = listfile_paths.get(int(file_data_id))
            if not texture_path or "item/texturecomponents/" not in normalize_resource_path(texture_path):
                continue

            texture_name = get_texture_component_raw_name(file_data_id)
            family, slot = split_collection_family_slot(texture_name)
            if not family:
                continue

            families.setdefault(display_id, set()).add(family)
    return families

def build_appearance_order():
    sorted_appearances = df_item_appearance.sort_values(
        by='UiOrder' if 'UiOrder' in df_item_appearance.columns else 'ID'
    )
    order = [int(display_id) for display_id in sorted_appearances['ItemDisplayInfoID'] if pd.notna(display_id)]
    index_by_display = {}
    for index, display_id in enumerate(order):
        index_by_display.setdefault(display_id, index)
    return order, index_by_display

collection_model_catalog, collection_model_side_catalog = build_collection_model_catalogs()
display_collection_sources = build_display_collection_sources()
display_component_families = build_display_component_families()
appearance_display_order, appearance_index_by_display = build_appearance_order()

def get_collection_catalog_model(family, slot, side=""):
    family = str(family or "").lower()
    slot = str(slot or "").lower()
    side = str(side or "").lower()
    if side:
        model = collection_model_side_catalog.get((family, slot, side))
        if model:
            return dict(model)
    model = collection_model_catalog.get((family, slot))
    return dict(model) if model else None

def get_collection_material_candidates(row, preferred_slot):
    candidates = []
    for texture_column in ('ModelMaterialResourcesID[0]', 'ModelMaterialResourcesID[1]'):
        texture_name, texture_is_collection, family, slot = get_collection_texture_name_from_material(
            row.get(texture_column, 0),
            True
        )
        if not texture_name or not texture_is_collection or not family:
            continue
        if preferred_slot and slot and slot != preferred_slot:
            continue
        candidates.append({
            "family": family,
            "slot": slot,
            "texture_name": texture_name,
        })
    return candidates

def synthesize_slot_collection_models_from_material(row, inventory_type):
    if inventory_type == 3:
        candidates = get_collection_material_candidates(row, "shoulder")
        for candidate in candidates:
            left_model = get_collection_catalog_model(candidate["family"], "shoulder", "l")
            right_model = get_collection_catalog_model(candidate["family"], "shoulder", "r")
            if left_model or right_model:
                return {
                    "model_1": left_model,
                    "model_2": right_model,
                    "texture_name": candidate["texture_name"],
                }

    if inventory_type == 6:
        candidates = get_collection_material_candidates(row, "belt")
        for candidate in candidates:
            belt_model = get_collection_catalog_model(candidate["family"], "belt")
            if belt_model:
                return {
                    "model_1": belt_model,
                    "model_2": None,
                    "texture_name": candidate["texture_name"],
                }

    return None

def find_nearby_collection_source(display_id, family, preferred_slot):
    display_id = int(display_id)
    family = family.lower()
    preferred_slot = preferred_slot or "chest"

    def source_is_compatible(source):
        source_slot = source.get("slot", "")
        return (
            source_slot == preferred_slot
            or (preferred_slot != "belt" and source_slot == "chest")
        )

    def source_rank(source, distance):
        if source["slot"] == preferred_slot:
            slot_rank = 0
        elif preferred_slot != "belt" and source["slot"] == "chest":
            slot_rank = 1
        else:
            slot_rank = 2
        return slot_rank, distance

    candidates = []
    direct_sources = display_collection_sources.get(display_id, [])
    for source in direct_sources:
        if source["family"] == family and source_is_compatible(source):
            candidates.append((source_rank(source, 0), source))

    center_index = appearance_index_by_display.get(display_id)
    if center_index is not None:
        start = max(0, center_index - COLLECTION_SYNTHESIS_SEARCH_RADIUS)
        end = min(len(appearance_display_order), center_index + COLLECTION_SYNTHESIS_SEARCH_RADIUS + 1)
        for index in range(start, end):
            candidate_display_id = appearance_display_order[index]
            distance = abs(index - center_index)
            for source in display_collection_sources.get(candidate_display_id, []):
                if source["family"] == family and source_is_compatible(source):
                    candidates.append((source_rank(source, distance), source))

    if not candidates:
        return None

    candidates.sort(key=lambda candidate: candidate[0])
    source = dict(candidates[0][1])
    preferred_model = collection_model_catalog.get((family, preferred_slot))
    if preferred_model:
        source.update(preferred_model)
    return source

def synthesize_collection_model(row, inventory_type):
    if inventory_type not in COLLECTION_SYNTHESIZED_INVENTORY_TYPES:
        return None

    preferred_slot = COLLECTION_PREFERRED_MODEL_SLOT.get(inventory_type)
    if not preferred_slot:
        return None

    display_id = int(row.get('ID', 0))
    families = set(display_component_families.get(display_id, set()))
    families.update(get_row_model_families(row))
    for family in sorted(families):
        source = find_nearby_collection_source(display_id, family, preferred_slot)
        if source:
            return source
    return None

def get_row_model_families(row):
    families = set()

    for model_column in ('ModelResourcesID[0]', 'ModelResourcesID[1]'):
        model_res_id = row.get(model_column, 0)
        if pd.isna(model_res_id) or not model_res_id or int(model_res_id) == 0:
            continue

        file_data_id = model_resource_to_file_data.get(model_res_id)
        if pd.isna(file_data_id):
            continue

        model_name, _, _, _, _ = get_model_info(file_data_id)
        family, _ = split_collection_family_slot(model_name)
        if family:
            families.add(family)

    for texture_column in ('ModelMaterialResourcesID[0]', 'ModelMaterialResourcesID[1]'):
        texture_name, _, family, _ = get_collection_texture_name_from_material(
            row.get(texture_column, 0),
            False
        )
        if family:
            families.add(family)

    return families

def append_model_entry(model_entries, model_name):
    if model_name and model_name not in model_entries:
        model_entries.append(model_name)

def join_model_entries(model_entries):
    return ";".join(entry for entry in model_entries if entry)

def append_semicolon_entries(value, entries):
    parts = [part for part in str(value).split(";") if part] if value else []
    for entry in entries:
        if entry and entry not in parts:
            parts.append(entry)
    return ";".join(parts)

def append_texture_layer_entries(value, entries, part_index=0):
    entries = [entry for entry in entries if entry]
    if not entries:
        return value

    parts = [part.strip() for part in str(value or "").split(";")]
    if not parts or parts == [""]:
        parts = [""]
    while len(parts) <= part_index:
        parts.append("")

    layers = [layer.strip() for layer in parts[part_index].split(":") if layer.strip()]
    for entry in entries:
        if entry not in layers:
            layers.append(entry)
    parts[part_index] = ":".join(layers)
    return ";".join(parts).rstrip(";")

def left_shoulder_model_name(model_name):
    if not model_name:
        return model_name

    shoulder_name = re.sub(r'(^|[\\/])rshoulder_', r'\1lshoulder_', model_name, flags=re.IGNORECASE)
    shoulder_name = re.sub(r'_r(\.mdx)$', r'_l\1', shoulder_name, flags=re.IGNORECASE)
    shoulder_name = re.sub(r'_right(\.mdx)$', r'_left\1', shoulder_name, flags=re.IGNORECASE)
    return shoulder_name

def right_shoulder_model_name(model_name):
    if not model_name:
        return model_name

    shoulder_name = re.sub(r'(^|[\\/])lshoulder_', r'\1rshoulder_', model_name, flags=re.IGNORECASE)
    shoulder_name = re.sub(r'_l(\.mdx)$', r'_r\1', shoulder_name, flags=re.IGNORECASE)
    shoulder_name = re.sub(r'_left(\.mdx)$', r'_right\1', shoulder_name, flags=re.IGNORECASE)
    return shoulder_name

def split_wxl_sidecar_model(model_name):
    model_name = str(model_name or "").strip()
    if not model_name:
        return "", ""
    if ":" not in model_name:
        return model_name, ""
    model_stem, geosets = model_name.split(":", 1)
    return model_stem.strip(), geosets.strip()

def wxl_list_part(value, index):
    parts = [part.strip() for part in str(value or "").split(";")]
    if index < len(parts):
        return parts[index]
    if parts:
        return parts[0]
    return ""

def wxl_sidecar_side(model_name, model_index):
    side = collection_model_side(model_name)
    if side == "l":
        return "L"
    if side == "r":
        return "R"
    if model_index == 1 and str(model_name or "").lower().startswith("lshoulder_"):
        return "L"
    if model_index == 2 and str(model_name or "").lower().startswith("rshoulder_"):
        return "R"
    return "C"

def wxl_sidecar_folder(model_name, component_folder, inventory_type, has_geosets):
    folder = str(component_folder or "").strip()
    lower_model = str(model_name or "").lower()
    if folder:
        return folder
    if has_geosets or lower_model.startswith("collections_"):
        return "Collections"
    if lower_model.startswith(("lshoulder_", "rshoulder_", "shoulder_")) or "_shoulder_l" in lower_model or "_shoulder_r" in lower_model:
        return "Shoulder"
    if lower_model.startswith("cape_"):
        return "Cape"
    if lower_model.startswith("tabard_"):
        return "Tabard"
    return INVENTORY_TYPE_TO_OBJECT_FOLDER.get(inventory_type, "")

def wxl_sidecar_attach(model_name, component_folder, inventory_type, model_index, has_geosets):
    if has_geosets and inventory_type == 10 and normalize_component_folder_name(component_folder) == "collections":
        return str(COLLECTION_ATTACHMENT)
    attach = slot_attach_override_for_model(inventory_type, model_name, component_folder, model_index)
    if attach:
        return attach
    if has_geosets:
        return str(COLLECTION_ATTACHMENT)
    slot_attachments = SLOT_ATTACHMENT_OVERRIDES.get(inventory_type)
    if not slot_attachments:
        return ""
    return slot_attachments[0] if model_index == 1 else slot_attachments[1]

def append_wxl_sidecar_entries(sidecar_rows, output_row, inventory_type, model_1_component_folder, model_2_component_folder):
    display_id = output_row.get("ID", "")
    slot = INVENTORY_TYPE_TO_WXL_SLOT.get(inventory_type, "")

    for model_index, model_column, texture_column, component_folder in (
        (1, "ModelName_1", "ModelTexture_1", model_1_component_folder),
        (2, "ModelName_2", "ModelTexture_2", model_2_component_folder),
    ):
        model_parts = [part.strip() for part in str(output_row.get(model_column, "") or "").split(";") if part.strip()]
        if not model_parts:
            continue

        for part_index, model_part in enumerate(model_parts):
            model_stem, geosets = split_wxl_sidecar_model(model_part)
            if not model_stem:
                continue

            texture_stem = wxl_list_part(output_row.get(texture_column, ""), part_index)
            has_geosets = bool(geosets)
            folder = wxl_sidecar_folder(model_stem, component_folder, inventory_type, has_geosets)
            attach = wxl_sidecar_attach(model_stem, folder, inventory_type, model_index, has_geosets)

            sidecar_rows.append({
                "DisplayID": display_id,
                "ItemID": "",
                "Slot": slot,
                "Layer": "Overlay" if part_index else "Base",
                "Side": wxl_sidecar_side(model_stem, model_index),
                "Folder": folder,
                "Model": model_stem,
                "Texture": texture_stem,
                "Geosets": geosets,
                "Attach": attach,
                "SuffixPolicy": "DBC",
                "TexturePolicy": "Layered" if ":" in texture_stem else "Exact",
                "Flags": "",
            })

def is_collection_model_reference(model_stem, component_folder, skin_stem, is_collection_path):
    if is_collection_path:
        return True
    if normalize_component_folder_name(component_folder) == "collections":
        return True
    return (
        str(model_stem or "").lower().startswith("collections_")
        or str(skin_stem or "").lower().startswith("collections_")
    )

def should_emit_hide_material(inventory_type, component_folder, material_entry, is_collection_model, target_info):
    if is_collection_model:
        return False
    if inventory_type not in (1, 3):
        return False
    if normalize_component_folder_name(component_folder) not in ("head", "shoulder"):
        return False
    if int(material_entry.get("texture_type", 0)) != 3:
        return False
    return bool(target_info.get("TargetSkinSectionIDs") and target_info.get("TargetBatchIndexes"))

def append_model_material_sidecar_entries(
    material_sidecar_rows,
    output_row,
    inventory_type,
    model_1_component_folder,
    model_2_component_folder,
    model_1_skin_stem,
    model_2_skin_stem,
    model_1_is_collection_path,
    model_2_is_collection_path,
):
    display_id = int(output_row.get("ID", 0))
    for model_index, model_column, component_folder, skin_stem, is_collection_path in (
        (0, "ModelName_1", model_1_component_folder, model_1_skin_stem, model_1_is_collection_path),
        (1, "ModelName_2", model_2_component_folder, model_2_skin_stem, model_2_is_collection_path),
    ):
        model_stem, _ = split_wxl_sidecar_model(wxl_list_part(output_row.get(model_column, ""), 0))
        target_info = collection_skin_material_target_info(inventory_type, skin_stem, component_folder)
        is_collection_model = is_collection_model_reference(model_stem, component_folder, skin_stem, is_collection_path)
        for layer_index, material_entry in enumerate(display_model_material_rows.get(display_id, {}).get(model_index, [])):
            file_data_id = texture_material_to_file_data.get(material_entry["material_res_id"])
            if pd.isna(file_data_id):
                continue

            row_target_info = dict(target_info)
            if should_emit_hide_material(inventory_type, component_folder, material_entry, is_collection_model, row_target_info):
                texture_name = "__hide__"
                row_target_info["TargetMode"] = "HideSlotGeosets"
            else:
                texture_name = get_model_texture_name(
                    file_data_id,
                    component_folder,
                    COLLECTION_PATH_MARKER in normalize_resource_path(listfile_paths.get(int(file_data_id), "")),
                )
            if not texture_name:
                continue

            material_sidecar_rows.append({
                "DisplayID": str(display_id),
                "ModelIndex": str(model_index),
                "ModelColumn": model_column,
                "Model": model_stem,
                "Layer": str(layer_index),
                "TextureType": str(material_entry["texture_type"]),
                "MaterialResourcesID": str(int(material_entry["material_res_id"])),
                "FileDataID": str(int(file_data_id)),
                "Folder": object_component_folder_from_file_data(file_data_id, include_collections=True),
                "Texture": texture_name,
                "SkinSectionIDs": row_target_info["SkinSectionIDs"],
                "BatchIndexes": row_target_info["BatchIndexes"],
                "TargetSkinSectionIDs": row_target_info["TargetSkinSectionIDs"],
                "TargetBatchIndexes": row_target_info["TargetBatchIndexes"],
                "TargetMode": row_target_info["TargetMode"],
            })

ASSET_SLOT_TO_INVENTORY_TYPE = {
    "helm": 1,
    "shoulder": 3,
    "chest": 5,
    "belt": 6,
    "pant": 7,
    "boot": 8,
    "bracer": 9,
    "glove": 10,
}

def infer_inventory_type_from_row_assets(row):
    inferred_types = []

    def add_slot(slot):
        inventory_type = ASSET_SLOT_TO_INVENTORY_TYPE.get(slot)
        if inventory_type and inventory_type not in inferred_types:
            inferred_types.append(inventory_type)

    for model_column in ('ModelResourcesID[0]', 'ModelResourcesID[1]'):
        model_res_id = row.get(model_column, 0)
        if pd.isna(model_res_id) or not model_res_id or int(model_res_id) == 0:
            continue

        file_data_id = model_resource_to_file_data.get(model_res_id)
        if pd.isna(file_data_id):
            continue

        model_name, _, _, _, _ = get_model_info(file_data_id)
        _, slot = split_collection_family_slot(model_name)
        add_slot(slot)

    for texture_column in ('ModelMaterialResourcesID[0]', 'ModelMaterialResourcesID[1]'):
        _, texture_is_collection, _, slot = get_collection_texture_name_from_material(
            row.get(texture_column, 0),
            True
        )
        if texture_is_collection:
            add_slot(slot)

    display_id = int(row.get('ID', 0))
    for model_index in (0, 1):
        _, _, slot = get_model_mat_res_collection_texture(display_id, model_index)
        add_slot(slot)

    return inferred_types[0] if len(inferred_types) == 1 else 0

def get_display_inventory_type(item_display_id, row=None):
    inventory_types = item_display_to_inventory_types.get(int(item_display_id), set())
    for inventory_type in (20, 5, 10, 7, 8, 6, 9, 4, 19, 3, 1, 16):
        if inventory_type in inventory_types:
            return inventory_type

    display_types = item_display_to_display_types.get(int(item_display_id), set())
    for display_type in (3, 8, 5, 6, 4, 7, 9, 1, 0):
        if display_type in display_types:
            return DISPLAY_TYPE_TO_INVENTORY_TYPE.get(display_type, 0)

    if row is not None:
        inferred_inventory_type = infer_inventory_type_from_row_assets(row)
        if inferred_inventory_type:
            return inferred_inventory_type

    return next(iter(inventory_types), 0)

def clear_collection_model_state():
    return {
        "model_name": "",
        "is_collection_path": False,
        "uses_new_suffix": False,
        "uses_suffix": False,
        "skin_stem": "",
        "texture_override": "",
        "uses_filter": False,
    }

def get_geoset_option(row, column_index):
    value = row.get(f'GeosetGroup[{column_index}]', 0)
    if pd.isna(value) or int(value) <= 0:
        return 1
    return int(value)

def get_collection_geosets(row, inventory_type, model_name, skin_stem=""):
    skin_geosets = get_collection_skin_geosets(inventory_type, skin_stem)
    if skin_geosets is not None:
        return skin_geosets

    geoset_rules = INVENTORY_COLLECTION_GEOSETS.get(inventory_type)
    if not geoset_rules:
        lower_model_name = model_name.lower()
        for tokens, hinted_rules in COLLECTION_FILENAME_GEOSET_HINTS:
            if any(token in lower_model_name for token in tokens):
                geoset_rules = hinted_rules
                break

    geosets = []
    for base, geoset_group_index in geoset_rules or []:
        geoset = base + get_geoset_option(row, geoset_group_index)
        if geoset not in geosets:
            geosets.append(geoset)
    return geosets

def apply_collection_suffix(model_name, is_collection, row, inventory_type, skin_stem=""):
    if not model_name or not is_collection:
        return model_name, False

    geosets = get_collection_geosets(row, inventory_type, model_name, skin_stem)
    if not geosets:
        return model_name, False

    return f"{model_name}:{','.join(str(geoset) for geoset in geosets)}", True

def build_icon2_config(
    model_1_present,
    model_2_present,
    model_1_is_collection_path,
    model_2_is_collection_path,
    model_1_uses_filter,
    model_2_uses_filter,
    model_1_uses_new_suffix,
    model_2_uses_new_suffix,
    model_1_uses_suffix,
    model_2_uses_suffix,
    model_1_attach_override="",
    model_2_attach_override="",
    custom_folder="",
):
    if (
        not model_1_uses_new_suffix
        and not model_2_uses_new_suffix
        and not model_1_is_collection_path
        and not model_2_is_collection_path
        and not model_1_uses_suffix
        and not model_2_uses_suffix
        and not model_1_attach_override
        and not model_2_attach_override
        and not custom_folder
    ):
        return ""

    attach_1 = str(model_1_attach_override or (COLLECTION_ATTACHMENT if model_1_uses_filter else ""))
    attach_2 = str(model_2_attach_override or (COLLECTION_ATTACHMENT if model_2_uses_filter else ""))
    flags_value = 0

    if model_1_uses_filter or model_2_uses_filter:
        flags_value |= COLLECTION_HIDE_BUILT_IN_FLAG

    if model_1_uses_new_suffix or model_2_uses_new_suffix:
        flags_value |= COLLECTION_NEW_SUFFIX_FLAG

    if (
        (model_1_uses_suffix and not model_1_is_collection_path) or
        (model_2_uses_suffix and not model_2_is_collection_path)
    ):
        flags_value |= MODEL_FORCE_RACE_GENDER_FLAG

    collection_suffix_states = []
    if model_1_is_collection_path:
        collection_suffix_states.append(model_1_uses_suffix)
    if model_2_is_collection_path:
        collection_suffix_states.append(model_2_uses_suffix)
    if collection_suffix_states and not any(collection_suffix_states):
        flags_value |= COLLECTION_NO_GENDER_FLAG | COLLECTION_NO_RACE_FLAG

    flags = str(flags_value) if flags_value else ""

    fields = [attach_1, attach_2, flags]

    collection_without_filter = (
        (model_1_is_collection_path and not model_1_uses_filter) or
        (model_2_is_collection_path and not model_2_uses_filter)
    )
    non_collection_model_present = (
        (model_1_present and not model_1_is_collection_path) or
        (model_2_present and not model_2_is_collection_path)
    )

    if custom_folder:
        fields.append(custom_folder)
    elif collection_without_filter and not non_collection_model_present:
        fields.append("Collections")

    return ":".join(fields).rstrip(":")

def resolve_icon_file_data(icon_file_data_id, item_display_id, source_label):
    icon_file_data_id = str(icon_file_data_id).strip()
    if icon_file_data_id.isdigit():
        texture_name = get_texture_name(int(icon_file_data_id))
        if texture_name:
            log(f"Resolved InventoryIcon_1 for ItemDisplayInfoID {item_display_id} from {source_label}: {texture_name}")
            return texture_name, ""
        return UNKNOWN_ICON_NAME, f"{source_label} IconFileDataID {icon_file_data_id} not found in listfile.csv"

    if icon_file_data_id:
        log(f"Assigned InventoryIcon_1 directly for ItemDisplayInfoID {item_display_id} from {source_label}: {icon_file_data_id}")
        return icon_file_data_id, ""

    return UNKNOWN_ICON_NAME, f"{source_label} icon missing"

def infer_inventory_icon_from_output_row(output_row):
    if not output_row:
        return ""

    def normalized_icon_bases(raw_value):
        for part in str(raw_value or "").split(";"):
            stem = part.strip().replace("/", "\\")
            if not stem:
                continue
            stem = stem.rsplit("\\", 1)[-1].split(":", 1)[0]
            stem = os.path.splitext(stem)[0].lower()
            if not stem:
                continue

            bases = [stem]
            if stem.startswith("collections_"):
                bases.append(stem[len("collections_"):])
            if stem.startswith("lshoulder_"):
                bases.append("shoulder_" + stem[len("lshoulder_"):])
            if stem.startswith("rshoulder_"):
                bases.append("shoulder_" + stem[len("rshoulder_"):])
            yield from bases

    for column in ("ModelTexture_1", "ModelTexture_2", "ModelName_1", "ModelName_2"):
        for base in normalized_icon_bases(output_row.get(column, "")):
            probe = base
            while probe:
                icon_key = probe if probe.startswith("inv_") else f"inv_{probe}"
                icon_name = inventory_icon_stems.get(icon_key.lower())
                if icon_name:
                    return icon_name
                if "_" not in probe:
                    break
                probe = probe.rsplit("_", 1)[0]
    return ""

def resolve_inventory_icon(item_display_id, output_row=None):
    icon_matches = icon_map.get(str(item_display_id).strip(), [])
    if not icon_matches:
        fallback_icon = appearance_icon_map.get(str(item_display_id).strip(), "")
        if fallback_icon:
            return resolve_icon_file_data(fallback_icon, item_display_id, "ItemAppearance.DefaultIconFileDataID")
        inferred_icon = infer_inventory_icon_from_output_row(output_row)
        if inferred_icon:
            log(f"Inferred InventoryIcon_1 for ItemDisplayInfoID {item_display_id}: {inferred_icon}")
            return inferred_icon, ""
        return UNKNOWN_ICON_NAME, "No ItemIcons.csv entry for ItemDisplayInfoID"

    icon_file_data_id = str(icon_matches[0]).strip()
    log(f"Using first available icon for ItemDisplayInfoID {item_display_id}: {icon_file_data_id}")

    texture_name, reason = resolve_icon_file_data(icon_file_data_id, item_display_id, "ItemIcons.csv")
    if texture_name != UNKNOWN_ICON_NAME:
        return texture_name, reason

    fallback_icon = appearance_icon_map.get(str(item_display_id).strip(), "")
    if fallback_icon:
        texture_name, reason = resolve_icon_file_data(fallback_icon, item_display_id, "ItemAppearance.DefaultIconFileDataID")
        if texture_name != UNKNOWN_ICON_NAME:
            return texture_name, reason

    inferred_icon = infer_inventory_icon_from_output_row(output_row)
    if inferred_icon:
        log(f"Inferred InventoryIcon_1 for ItemDisplayInfoID {item_display_id}: {inferred_icon}")
        return inferred_icon, ""
    return texture_name, reason

# Process and store results
output_rows = []
sidecar_rows = []
model_material_sidecar_rows = []
unresolved_icon_rows = []
batch_size = 1000
for index, row in df_tww.iterrows():
    model_res_1 = row.iloc[11]  # 12th column (zero-indexed)
    model_res_2 = row.iloc[12]  # 13th column (zero-indexed)
    model_name_1 = ""
    model_name_2 = ""
    model_1_is_collection_path = False
    model_2_is_collection_path = False
    model_1_uses_collection_filter = False
    model_2_uses_collection_filter = False
    model_1_uses_new_suffix = False
    model_2_uses_new_suffix = False
    model_1_uses_suffix = False
    model_2_uses_suffix = False
    model_1_skin_stem = ""
    model_2_skin_stem = ""
    model_1_component_folder = ""
    model_2_component_folder = ""
    model_texture_1_override = ""
    model_texture_2_override = ""
    model_texture_1_extra_entries = []
    inventory_type = get_display_inventory_type(row.get('ID', 0), row)

    if model_res_1 != 0 and 'ModelResourcesID' in df_model_data.columns:
        file_data_1 = model_resource_to_file_data.get(model_res_1)
        if pd.notna(file_data_1):
            model_1_component_folder = object_component_folder_from_file_data(file_data_1)
            model_name_1, model_1_is_collection_path, model_1_uses_new_suffix, model_1_uses_suffix, model_1_skin_stem = get_model_info(file_data_1)
            if model_1_is_collection_path and collection_model_uses_slot_attachment(inventory_type, model_name_1):
                model_1_component_folder = "Collections"
                model_1_is_collection_path = False
            else:
                model_name_1, model_1_uses_collection_filter = apply_collection_suffix(
                    model_name_1, model_1_is_collection_path, row, inventory_type, model_1_skin_stem
                )
                if (
                    model_1_is_collection_path
                    and not model_1_uses_collection_filter
                    and collection_skin_has_no_slot_mesh(inventory_type, model_1_skin_stem)
                ):
                    model_1_state = clear_collection_model_state()
                    model_name_1 = model_1_state["model_name"]
                    model_1_is_collection_path = model_1_state["is_collection_path"]
                    model_1_uses_new_suffix = model_1_state["uses_new_suffix"]
                    model_1_uses_suffix = model_1_state["uses_suffix"]
                    model_1_skin_stem = model_1_state["skin_stem"]
                    model_1_uses_collection_filter = model_1_state["uses_filter"]

    if model_res_2 != 0 and 'ModelResourcesID' in df_model_data.columns:
        file_data_2 = model_resource_to_file_data.get(model_res_2)
        if pd.notna(file_data_2):
            model_2_component_folder = object_component_folder_from_file_data(file_data_2)
            model_name_2, model_2_is_collection_path, model_2_uses_new_suffix, model_2_uses_suffix, model_2_skin_stem = get_model_info(file_data_2)
            if model_2_is_collection_path and collection_model_uses_slot_attachment(inventory_type, model_name_2):
                model_2_component_folder = "Collections"
                model_2_is_collection_path = False
            else:
                model_name_2, model_2_uses_collection_filter = apply_collection_suffix(
                    model_name_2, model_2_is_collection_path, row, inventory_type, model_2_skin_stem
                )
                if (
                    model_2_is_collection_path
                    and not model_2_uses_collection_filter
                    and collection_skin_has_no_slot_mesh(inventory_type, model_2_skin_stem)
                ):
                    model_2_state = clear_collection_model_state()
                    model_name_2 = model_2_state["model_name"]
                    model_2_is_collection_path = model_2_state["is_collection_path"]
                    model_2_uses_new_suffix = model_2_state["uses_new_suffix"]
                    model_2_uses_suffix = model_2_state["uses_suffix"]
                    model_2_skin_stem = model_2_state["skin_stem"]
                    model_2_uses_collection_filter = model_2_state["uses_filter"]

    if (
        model_name_1
        and model_name_2
        and model_name_1 == model_name_2
        and (inventory_type == 3 or "shoulder" in model_name_1.lower())
    ):
        model_name_1 = left_shoulder_model_name(model_name_1)
        model_name_2 = right_shoulder_model_name(model_name_2)

    if not model_name_1 and not model_name_2:
        slot_collection = synthesize_slot_collection_models_from_material(row, inventory_type)
        if slot_collection:
            model_1 = slot_collection.get("model_1")
            model_2 = slot_collection.get("model_2")
            if model_1:
                model_name_1 = model_1["model_name"]
                model_1_component_folder = "Collections"
                model_1_is_collection_path = False
                model_1_uses_new_suffix = model_1["uses_new_suffix"]
                model_1_uses_suffix = model_1["uses_suffix"]
                model_1_skin_stem = model_1["skin_stem"]
                model_texture_1_override = slot_collection.get("texture_name", "")
            if model_2:
                model_name_2 = model_2["model_name"]
                model_2_component_folder = "Collections"
                model_2_is_collection_path = False
                model_2_uses_new_suffix = model_2["uses_new_suffix"]
                model_2_uses_suffix = model_2["uses_suffix"]
                model_2_skin_stem = model_2["skin_stem"]
                model_texture_2_override = slot_collection.get("texture_name", "")

    if not model_name_1 and not model_name_2:
        synthetic_collection = synthesize_collection_model(row, inventory_type)
        if synthetic_collection:
            model_name_1 = synthetic_collection["model_name"]
            model_1_is_collection_path = True
            model_1_uses_new_suffix = synthetic_collection["uses_new_suffix"]
            model_1_uses_suffix = synthetic_collection["uses_suffix"]
            model_1_skin_stem = synthetic_collection["skin_stem"]
            model_name_1, model_1_uses_collection_filter = apply_collection_suffix(
                model_name_1, model_1_is_collection_path, row, inventory_type, model_1_skin_stem
            )
            model_texture_1_override = synthetic_collection["texture_name"]
            if not model_1_uses_collection_filter:
                model_1_state = clear_collection_model_state()
                model_name_1 = model_1_state["model_name"]
                model_1_is_collection_path = model_1_state["is_collection_path"]
                model_1_uses_new_suffix = model_1_state["uses_new_suffix"]
                model_1_uses_suffix = model_1_state["uses_suffix"]
                model_1_skin_stem = model_1_state["skin_stem"]
                model_texture_1_override = model_1_state["texture_override"]
                model_1_uses_collection_filter = model_1_state["uses_filter"]

    if model_1_is_collection_path and not model_1_uses_collection_filter:
        model_1_state = clear_collection_model_state()
        model_name_1 = model_1_state["model_name"]
        model_1_is_collection_path = model_1_state["is_collection_path"]
        model_1_uses_new_suffix = model_1_state["uses_new_suffix"]
        model_1_uses_suffix = model_1_state["uses_suffix"]
        model_1_skin_stem = model_1_state["skin_stem"]
        model_texture_1_override = model_1_state["texture_override"]
        model_1_uses_collection_filter = model_1_state["uses_filter"]

    if model_2_is_collection_path and not model_2_uses_collection_filter:
        model_2_state = clear_collection_model_state()
        model_name_2 = model_2_state["model_name"]
        model_2_is_collection_path = model_2_state["is_collection_path"]
        model_2_uses_new_suffix = model_2_state["uses_new_suffix"]
        model_2_uses_suffix = model_2_state["uses_suffix"]
        model_2_skin_stem = model_2_state["skin_stem"]
        model_2_uses_collection_filter = model_2_state["uses_filter"]

    if (
        inventory_type == 3
        and model_name_1
        and model_name_2
        and not model_1_is_collection_path
        and not model_2_is_collection_path
    ):
        shoulder_collection = synthesize_collection_model(row, inventory_type)
        if shoulder_collection:
            shoulder_collection_name, shoulder_collection_uses_filter = apply_collection_suffix(
                shoulder_collection["model_name"],
                True,
                row,
                inventory_type,
                shoulder_collection["skin_stem"],
            )
            if shoulder_collection_uses_filter:
                model_name_1 = append_semicolon_entries(model_name_1, [shoulder_collection_name])
                model_texture_1_extra_entries.append(shoulder_collection["texture_name"])
                model_1_is_collection_path = True
                model_1_uses_collection_filter = True
                model_1_uses_new_suffix = model_1_uses_new_suffix or shoulder_collection["uses_new_suffix"]
                model_1_uses_suffix = model_1_uses_suffix or shoulder_collection["uses_suffix"]
                model_1_skin_stem = shoulder_collection["skin_stem"]

    output_row = {col: row[col] if col in row else "" for col in wotlk_columns}
    
    # Map GeosetGroup and HelmetGeosetVis
    output_row['GeosetGroup_1'] = row.get('GeosetGroup[0]', '')
    output_row['GeosetGroup_2'] = row.get('GeosetGroup[1]', '')
    output_row['GeosetGroup_3'] = row.get('GeosetGroup[2]', '')
    
    output_row['HelmetGeosetVis_1'] = row.get('HelmetGeosetVis[0]', '')
    output_row['HelmetGeosetVis_2'] = row.get('HelmetGeosetVis[1]', '')
    
    output_row['SpellVisualID'] = row.get('StateSpellVisualKitID', '')
    output_row['GroupSoundIndex'] = 0  # Defaulting to 0 as requested
    output_row['ModelName_1'] = model_name_1
    output_row['ModelName_2'] = model_name_2
    output_row['ParticleColorId'] = row.get('ParticleColorID', '')
    model_1_custom_folder, model_1_attach_override = object_component_icon2_override(model_1_component_folder)
    model_2_custom_folder, model_2_attach_override = object_component_icon2_override(model_2_component_folder)
    model_1_attach_override = (
        slot_attach_override_for_model(inventory_type, model_name_1, model_1_component_folder, 1)
        or model_1_attach_override
    )
    model_2_attach_override = (
        slot_attach_override_for_model(inventory_type, model_name_2, model_2_component_folder, 2)
        or model_2_attach_override
    )
    custom_folder_candidates = [
        folder for folder in (model_1_custom_folder, model_2_custom_folder) if folder
    ]
    icon2_custom_folder = ""
    if custom_folder_candidates and all(folder == custom_folder_candidates[0] for folder in custom_folder_candidates):
        icon2_custom_folder = custom_folder_candidates[0]

    output_row['InventoryIcon_2'] = build_icon2_config(
        bool(model_name_1),
        bool(model_name_2),
        model_1_is_collection_path,
        model_2_is_collection_path,
        model_1_uses_collection_filter,
        model_2_uses_collection_filter,
        model_1_uses_new_suffix,
        model_2_uses_new_suffix,
        model_1_uses_suffix,
        model_2_uses_suffix,
        model_1_attach_override,
        model_2_attach_override,
        icon2_custom_folder,
    )

    inventory_icon, unresolved_reason = resolve_inventory_icon(row.get('ID', ''), output_row)
    output_row['InventoryIcon_1'] = inventory_icon
    if unresolved_reason:
        unresolved_icon_rows.append({
            "ItemDisplayInfoID": row.get('ID', ''),
            "Reason": unresolved_reason
        })
        log(f"No InventoryIcon_1 for ItemDisplayInfoID {row.get('ID', '')}: {unresolved_reason}")

    item_id = int(row['ID'])

    # Process ModelTexture_1 and ModelTexture_2
    texture_res_1 = row.get('ModelMaterialResourcesID[0]', 0)
    texture_res_2 = row.get('ModelMaterialResourcesID[1]', 0)
    
    if texture_res_1 != 0:
        file_data_1 = texture_material_to_file_data.get(texture_res_1)
        if pd.notna(file_data_1):
            output_row['ModelTexture_1'] = get_model_texture_name(
                file_data_1,
                model_1_component_folder,
                model_1_is_collection_path,
            )
    elif model_texture_1_override:
        output_row['ModelTexture_1'] = model_texture_1_override

    model_mat_res_texture_entries_1 = get_model_mat_res_texture_entries(
        item_id,
        0,
        model_1_component_folder,
        model_1_is_collection_path,
    )
    if not output_row.get('ModelTexture_1', '') and model_mat_res_texture_entries_1:
        output_row['ModelTexture_1'] = model_mat_res_texture_entries_1[0]

    if model_texture_1_extra_entries:
        output_row['ModelTexture_1'] = append_semicolon_entries(
            output_row.get('ModelTexture_1', ''),
            model_texture_1_extra_entries,
        )
    
    if texture_res_2 != 0:
        file_data_2 = texture_material_to_file_data.get(texture_res_2)
        if pd.notna(file_data_2):
            output_row['ModelTexture_2'] = get_model_texture_name(
                file_data_2,
                model_2_component_folder,
                model_2_is_collection_path,
            )
    elif model_texture_2_override:
        output_row['ModelTexture_2'] = model_texture_2_override

    model_mat_res_texture_entries_2 = get_model_mat_res_texture_entries(
        item_id,
        1,
        model_2_component_folder,
        model_2_is_collection_path,
    )
    if not output_row.get('ModelTexture_2', '') and model_mat_res_texture_entries_2:
        output_row['ModelTexture_2'] = model_mat_res_texture_entries_2[0]
    material_matches = display_material_rows.get(item_id, [])
    
    for component_section_raw, material_res_id in material_matches:
        component_section = component_section_raw + 1  # ComponentSection +1 for correct Texture_X mapping
        
        file_data_match = texture_material_to_file_data.get(material_res_id)
        if pd.notna(file_data_match):
            texture_name = get_texture_component_name(file_data_match)
            texture_col = f"Texture_{component_section}"
            if texture_col in output_row:
                output_row[texture_col] = texture_name

    # Retail geoset-group values are not compatible with the 3.3.5 character-component selector. When a
    # collection overlay supplies the 3D legs/boots and the DBC also supplies baked component textures,
    # retain the character surfaces at their legacy default instead of feeding modern values (for example
    # the 7,2 pair on display 681918) into the old geoset switch. Otherwise the lower-leg/foot textures are
    # loaded but have no character surface on which to render.
    has_collection_overlay = model_1_uses_collection_filter or model_2_uses_collection_filter
    has_leg_components = output_row.get("Texture_6") or output_row.get("Texture_7")
    has_foot_components = output_row.get("Texture_7") or output_row.get("Texture_8")
    if has_collection_overlay and (
        (inventory_type == 7 and has_leg_components) or
        (inventory_type == 8 and has_foot_components)
    ):
        output_row["GeosetGroup_1"] = 0
        output_row["GeosetGroup_2"] = 0

    append_wxl_sidecar_entries(
        sidecar_rows,
        output_row,
        inventory_type,
        model_1_component_folder,
        model_2_component_folder,
    )
    append_model_material_sidecar_entries(
        model_material_sidecar_rows,
        output_row,
        inventory_type,
        model_1_component_folder,
        model_2_component_folder,
        model_1_skin_stem,
        model_2_skin_stem,
        model_1_is_collection_path,
        model_2_is_collection_path,
    )
    
    output_rows.append(output_row)
    
    if index % batch_size == 0 and index > 0:
        df_batch = pd.DataFrame(output_rows, columns=wotlk_columns)
        df_batch.to_csv(file_wotlk_converted, mode='a', header=not os.path.exists(file_wotlk_converted), index=False)
        output_rows = []  # Clear batch
        log(f"Checkpoint: Saved {index} rows to file.")

if output_rows:
    df_batch = pd.DataFrame(output_rows, columns=wotlk_columns)
    df_batch.to_csv(file_wotlk_converted, mode='a', header=not os.path.exists(file_wotlk_converted), index=False)
    log("Final batch saved.")

with open(file_unresolved_icons, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["ItemDisplayInfoID", "Reason"])
    writer.writeheader()
    writer.writerows(unresolved_icon_rows)

with open(file_wxl_item_display_models, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=wxl_sidecar_columns)
    writer.writeheader()
    writer.writerows(sidecar_rows)

with open(file_wxl_item_display_model_materials, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=wxl_model_material_columns)
    writer.writeheader()
    writer.writerows(model_material_sidecar_rows)

log("Saving converted data...")
log(f"Conversion complete! File saved as: {file_wotlk_converted}")
log(f"WXL sidecar model data saved as: {file_wxl_item_display_models} with {len(sidecar_rows)} rows.")
log(f"WXL sidecar model material data saved as: {file_wxl_item_display_model_materials} with {len(model_material_sidecar_rows)} rows.")
log(f"Unresolved inventory icon report saved as: {file_unresolved_icons} with {len(unresolved_icon_rows)} rows.")

# Close log file
log_file.close()
