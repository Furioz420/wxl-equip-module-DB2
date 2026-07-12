# DBC converter helpers

These scripts are the WXL/Eunoia helper pipeline used to build compatible `ItemDisplayInfo.dbc` and `Item.dbc` data from retail exports.

They are intentionally committed without generated CSVs, SQL dumps, DB2 files, listfiles, or logs. Put the required input exports next to the script you are running, then run the script from that folder.

## Requirements

- Python 3
- `pandas`
- Retail CSV exports for the tables listed below
- A FileDataID listfile exported as `listfile.csv`

Install Python dependencies with:

```powershell
py -m pip install pandas
```

## ItemDisplayInfo

Script:

```text
tools/dbc-converters/itemdisplayinfo/ItemDisplayDBC.py
```

Expected input files in the working folder:

```text
ItemDisplayInfo.csv
ModelFileData.csv
TextureFileData.csv
ItemDisplayInfoMaterialRes.csv
ItemDisplayInfoModelMatRes.csv
ItemIcons.csv
item.csv
itemappearance.csv
itemmodifiedappearance.csv
listfile.csv
```

Important optional environment variables:

```powershell
$env:WXL_CLIENT_OBJECT_COMPONENT_ROOTS = "D:\Path\To\Client\Data\Patch-4.MPQ\item\objectcomponents"
$env:WXL_CLIENT_COLLECTION_ROOTS = "D:\Path\To\Client\Data\Patch-4.MPQ\item\objectcomponents\collections"
```

`WXL_CLIENT_COLLECTION_ROOTS` is optional when it is just `<object root>\collections`.

Main outputs:

```text
ItemDisplayInfo_WotLK_Converted.csv
WXLItemDisplayModels.csv
WXLItemDisplayModelMaterials.csv
unresolved_inventory_icons.csv
conversion_log.txt
```

`WXLItemDisplayModels.csv` contains structured extra models per display ID. `WXLItemDisplayModelMaterials.csv` contains targeted model-material texture rows for special retail layers.

## Item

Script:

```text
tools/dbc-converters/item/ItemDBC.py
```

Expected input files in the working folder:

```text
ItemTWW.csv
Item.csv
itemmodifiedappearance.csv
itemappearance.csv
```

Optional inputs:

```text
EunoiaItemTemplate.sql
BlizzlikeItem.dbc.csv
ItemDisplayInfoEunoia.csv
```

The item script also looks for converted ItemDisplayInfo targets in the neighboring ItemDisplayInfo folders used by the original tool layout. If those files are not present, it falls back to ItemAppearance coverage.

Main outputs:

```text
Converted_Item.csv
Final_Converted_Item.csv
Item.dbc.csv
Generated_Item_Variants.csv
Orphan_Item_Appearances.csv
ItemDisplay_Coverage_Report.csv
```

The output guarantees that each known unique `ItemDisplayInfoID` can get an item row when source data is available. Unknown icons should stay as a question-mark fallback rather than blocking generation.

## SQL helpers

The item folder also includes:

```text
CreateSQL.py
CleanSQL.py
```

These are optional helpers for generating or cleaning server-side `item_template` SQL after `Item.dbc.csv` has been produced.

## Shipping outputs

For a WXL client patch, place the generated sidecars in:

```text
DBFilesClient\WXLItemDisplayModels.csv
DBFilesClient\WXLItemDisplayModelMaterials.csv
```

They can be shipped loose, in an open-folder `.MPQ`, or packed in a real MPQ.
