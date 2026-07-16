# WXL Equip Module — DB2

An updated [WarcraftXL](https://github.com/WarcraftXL/WarcraftXL) equipment module that loads retail equipment models, textures, material layers, attachment data, and geoset targeting from Blizzard-like **12.1.x DB2 data**.

> **Credit: the original equip module was created by [Morfium](https://github.com/Morfium-G/wxl-equip-extension). This repository builds on Morfium's work and updates it for DB2-driven retail equipment support.**

This version is designed around the retail DB2 graph rather than generated equipment sidecars or manually expanded `ItemDisplayInfo.dbc` rows.

## Required dependency

> **[Furioz420/wxl-retail-db2](https://github.com/Furioz420/wxl-retail-db2) is required. Without it, this module cannot load the DB2 item-display data and will not provide its intended retail equipment support.**

`wxl-retail-db2` decodes and publishes the immutable item-display index consumed by this module. The index is derived from Blizzard-like 12.1.x tables and their relationships, including:

- `Item`
- `ItemAppearance`
- `ItemModifiedAppearance`
- `ItemDisplayInfo`
- `ItemDisplayInfoMaterialRes`
- `ItemDisplayInfoModelMatRes`
- `ModelFileData`
- `TextureFileData`
- `ComponentModelFileData`
- `ComponentTextureFileData`

FileDataID path resolution and WDC5 decoding are supplied through the shared WarcraftXL host-extension DB2 services used by `wxl-retail-db2`.

## What this module does

- Builds equipment attachments from the DB2 item-display graph.
- Supports multiple model channels for an equipment display.
- Resolves retail model and texture assets through FileDataIDs.
- Supports collection M2s and character-root-skinned equipment.
- Derives geoset filtering and material batch targets from referenced `.skin` files.
- Applies `ItemDisplayInfoMaterialRes` and `ItemDisplayInfoModelMatRes` material layers.
- Preserves texture wrap/clamp flags when replacing model textures, including animated UV overlays.
- Refreshes equipment that was created before the asynchronous DB2 snapshot became ready.
- Supports CharacterModelFrame and duplicated model trees while guarding collection bone remaps.

The former `WXLItemDisplayModels.csv` and `WXLItemDisplayModelMaterials.csv` sidecars are not used.

## Data flow

```text
Blizzard-like 12.1.x DB2 files
        |
        v
wxl-retail-db2 + host DB2 services
        |
        v
immutable item-display index
        |
        v
wxl-equip-module-DB2
        |
        v
runtime models, textures, materials and geosets
```

Publication is atomic. The equip module never consumes a partially built index. Equipment dispatched before the index is ready is remembered and rebuilt after publication when the matching character render context is available.

## Requirements

- A WarcraftXL source tree and compatible 3.3.5a client runtime.
- [Furioz420/wxl-retail-db2](https://github.com/Furioz420/wxl-retail-db2).
- The WarcraftXL host-extension DB2 services required by `wxl-retail-db2`.
- Blizzard-like DB2 data matching a supported 12.1.x layout.
- The referenced retail M2, SKIN, and texture assets available through the mounted archive set.

DB2 layouts are validated before use. If Blizzard changes a table layout, the retail DB2 layer fails closed instead of interpreting incompatible records.

## Installation

Place the repositories in the WarcraftXL `scripts` directory:

```text
WarcraftXL/
  scripts/
    wxl-equip-module-DB2/
    wxl-retail-db2/
    wxl-host-extension/
```

The module is compiled into `WarcraftXL.dll`; it does not produce a separate runtime DLL. Build WarcraftXL normally:

```powershell
cmake --build build/dll --config Release --target WarcraftXL
```

Deploy the resulting `WarcraftXL.dll` and `d3d9.dll` to the client directory using the normal WarcraftXL workflow.

## Runtime behavior

The DB2 index supplies the structured attachment list for retail displays. Model paths, texture paths, model columns, material layers, target sections, and target batches come from the published retail data and derived SKIN metadata.

Root-skinned collection models copy only the required character bone transforms. Dedicated helm, shoulder, belt, and other attachment-point models retain their own native animation chains.

## Debug logging

Verbose equipment diagnostics are disabled by default because they run on hot model, skin, and per-frame paths.

Enable logging with either:

- `WXL_EQUIP_LOG=1`
- a `WarcraftXL_equip.log.enable` file beside the client executable

Diagnostics are appended to `WarcraftXL_equip.log`.

## Compatibility notes

- This repository targets the Blizzard-like **12.1.x** DB2 pipeline used by `wxl-retail-db2`.
- Retail updates may require schema/layout updates in `wxl-retail-db2` before new data can be loaded.
- Native client fallback behavior may still be visible while the DB2 snapshot is starting, but the module retries the affected equipment after publication.
- Neck, ring, trinket, and other slots without visible attached equipment models are outside the current attachment scope.

## Credits

**Original module design and implementation: [Morfium](https://github.com/Morfium-G/wxl-equip-extension).**

DB2-driven retail integration and ongoing updates: [Furioz420](https://github.com/Furioz420).
