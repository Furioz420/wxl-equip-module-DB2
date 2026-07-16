// wxl-equip-extension: client-side virtual M2 path table and file provider.
//
// Virtual paths encode (cmo × model × merged-geoset-filter × texture) in the filename so the
// engine's model hash table sees a distinct cache key for every unique combination. The host
// serve hook is bypassed; bytes are served directly from an in-process table. Collection skins are
// also prefiltered here so synchronous skin loads receive the same geoset-trimmed data.
//
// Copyright (C) 2026 WarcraftXL
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program. If not, see <https://www.gnu.org/licenses/>.

#include "VirtualPath.hpp"

#include "runtime/storage/StorageHook.hpp"
#include "game/io/Io.hpp"
#include "offsets/engine/Io.hpp"
#include "structure/m2/M2Format.hpp"

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <unordered_map>
#include <vector>

namespace wxl::scripts::equipextension
{
    namespace
    {
        static bool EquipLogEnabled() noexcept
        {
            static int enabled = []() noexcept -> int {
#pragma warning(suppress: 4996)
                const char* env = std::getenv("WXL_EQUIP_LOG");
                if (env && *env && *env != '0' && *env != 'n' && *env != 'N')
                    return 1;

#pragma warning(suppress: 4996)
                FILE* flag = std::fopen("WarcraftXL_equip.log.enable", "rb");
                if (!flag) return 0;
                std::fclose(flag);
                return 1;
            }();
            return enabled != 0;
        }

        static void VPathLog(const char* fmt, ...) noexcept
        {
            if (!EquipLogEnabled()) return;
#pragma warning(suppress: 4996)
            FILE* f = std::fopen("WarcraftXL_equip.log", "a");
            if (!f) return;
            va_list ap; va_start(ap, fmt);
            std::vfprintf(f, fmt, ap);
            va_end(ap);
            std::fputc('\n', f);
            std::fclose(f);
        }
        // virtual path -> raw file bytes (both .mdx and 00.skin entries per model).
        // Accessed from the game's main thread only; no mutex needed.
        std::unordered_map<std::string, std::vector<uint8_t>> g_virtualBytes;

        // cmo -> list of virtual paths it owns, for O(n) cleanup on eviction.
        std::unordered_map<void*, std::vector<std::string>> g_cmoVPaths;

        // ─── Key building ─────────────────────────────────────────────────────

        // Sorts ids[0..n-1] ascending in place (insertion sort; n <= 16).
        static void SortIds(uint16_t* ids, uint32_t n) noexcept
        {
            for (uint32_t i = 1; i < n; ++i)
            {
                uint16_t k = ids[i];
                int32_t j = static_cast<int32_t>(i) - 1;
                while (j >= 0 && ids[j] > k) { ids[j + 1] = ids[j]; --j; }
                ids[j + 1] = k;
            }
        }

        // Writes a uint16 as decimal into *q, advancing q. Returns new q.
        static char* WriteU16(char* q, char* end, uint16_t v) noexcept
        {
            char tmp[8]; int len = 0;
            if (v == 0) { tmp[len++] = '0'; }
            else { while (v) { tmp[len++] = '0' + (v % 10); v /= 10; } }
            for (int a = 0, b = len - 1; a < b; ++a, --b) { char c = tmp[a]; tmp[a] = tmp[b]; tmp[b] = c; }
            for (int i = 0; i < len && q < end; ++i) *q++ = tmp[i];
            return q;
        }

        // Writes a uintptr_t as lowercase hex into *q, advancing q. Returns new q.
        static char* WriteHex(char* q, char* end, uintptr_t v) noexcept
        {
            char tmp[9]; int len = 0;
            const char* digits = "0123456789abcdef";
            do { tmp[len++] = digits[v & 0xF]; v >>= 4; } while (v);
            for (int a = 0, b = len - 1; a < b; ++a, --b) { char c = tmp[a]; tmp[a] = tmp[b]; tmp[b] = c; }
            for (int i = 0; i < len && q < end; ++i) *q++ = tmp[i];
            return q;
        }

        static char LowerAscii(char c) noexcept
        {
            return (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c;
        }

        static uint32_t HashCString(const char* s) noexcept
        {
            uint32_t h = 2166136261u;
            if (!s) return h;
            while (*s)
            {
                h ^= static_cast<uint8_t>(LowerAscii(*s++));
                h *= 16777619u;
            }
            return h;
        }

        // Builds the virtual .m2 key (sortedIds must already be sorted ascending).
        // Format: <stem>_wxl_<id0>_<id1>..._tex_<texbasename>[_mat<hash>][_grp<hex>]_cmo<hex>.m2  (all lowercase)
        // The engine normalises all paths to lowercase and uses .m2; keys must match that form.
        static size_t BuildKey(char* out, size_t outSz, void* cmo,
                                const char* realMdxPath,
                                const uint16_t* sortedIds, uint32_t idCount,
                                const char* texPath,
                                const char* materialPatchSpec,
                                uint32_t variantKey) noexcept
        {
            if (!out || outSz == 0) return 0;
            char* q   = out;
            char* end = out + outSz - 1; // reserve one byte for null

            // Copy the stem (path without extension) — lowercase.
            const char* lastDot = nullptr;
            for (const char* p = realMdxPath; *p; ++p) if (*p == '.') lastDot = p;
            const char* stemEnd = lastDot ? lastDot : realMdxPath + std::strlen(realMdxPath);
            for (const char* p = realMdxPath; p < stemEnd && q < end; ) *q++ = LowerAscii(*p++);

            // _wxl_
            for (const char* s = "_wxl_"; *s && q < end; ) *q++ = *s++;

            // Sorted geoset IDs separated by '_'.
            for (uint32_t i = 0; i < idCount && q < end; ++i)
            {
                if (i > 0 && q < end) *q++ = '_';
                q = WriteU16(q, end, sortedIds[i]);
            }

            // _tex_<texbasename> (basename = filename without path prefix or extension) — lowercase.
            if (texPath && *texPath)
            {
                const char* base = texPath;
                for (const char* p = texPath; *p; ++p)
                    if (*p == '\\' || *p == '/') base = p + 1;
                const char* dot = nullptr;
                for (const char* p = base; *p; ++p) if (*p == '.') dot = p;
                const char* baseEnd = dot ? dot : base + std::strlen(base);

                for (const char* s = "_tex_"; *s && q < end; ) *q++ = *s++;
                for (const char* p = base; p < baseEnd && q < end; ) *q++ = LowerAscii(*p++);
            }

            if (materialPatchSpec && *materialPatchSpec)
            {
                for (const char* s = "_mat"; *s && q < end; ) *q++ = *s++;
                q = WriteHex(q, end, HashCString(materialPatchSpec));
            }

            if (variantKey != 0)
            {
                for (const char* s = "_grp"; *s && q < end; ) *q++ = *s++;
                q = WriteHex(q, end, variantKey);
            }

            // _cmo<hex> (lowercase)
            for (const char* s = "_cmo"; *s && q < end; ) *q++ = *s++;
            q = WriteHex(q, end, reinterpret_cast<uintptr_t>(cmo));

            // .m2  (engine requests all paths with .m2 extension, not .mdx)
            for (const char* s = ".m2"; *s && q < end; ) *q++ = *s++;

            *q = '\0';
            return static_cast<size_t>(q - out);
        }

        // ─── File reading ─────────────────────────────────────────────────────

        // Reads all bytes of a game archive file via the existing IO wrappers.
        // Uses kOpenWholeFile so the handle buffer holds the full content immediately.
        static bool ReadGameFile(const char* path, std::vector<uint8_t>& out) noexcept
        {
            namespace io    = wxl::game::io;
            namespace iooff = wxl::offsets::engine::io;

            void* handle = nullptr;
            if (!io::FileOpen(path, iooff::kOpenWholeFile, &handle) || !handle)
                return false;

            uint32_t sizeHigh = 0;
            uint32_t size     = io::FileSize(handle, &sizeHigh);
            bool ok = false;
            if (size > 0 && sizeHigh == 0)
            {
                out.resize(size);
                uint32_t got = 0;
                io::FileRead(handle, out.data(), size, &got);
                ok = (got == size);
                if (!ok) out.clear();
            }
            io::FileClose(handle);
            return ok;
        }

        // Derives the real skin path: strip extension (.m2 or .mdx), append 00.skin.
        static void RealSkinPath(char* out, size_t outSz, const char* realMdxPath) noexcept
        {
            const char* lastDot = nullptr;
            for (const char* p = realMdxPath; *p; ++p) if (*p == '.') lastDot = p;
            size_t stemLen = lastDot ? static_cast<size_t>(lastDot - realMdxPath)
                                     : std::strlen(realMdxPath);
            if (stemLen >= outSz) stemLen = outSz - 1;
            std::memcpy(out, realMdxPath, stemLen);
            const char* suffix = "00.skin";
            size_t rem = outSz - stemLen - 1;
            size_t suffLen = std::strlen(suffix);
            if (suffLen > rem) suffLen = rem;
            std::memcpy(out + stemLen, suffix, suffLen);
            out[stemLen + suffLen] = '\0';
        }

        // Derives the virtual skin path from a virtual .m2 key: strip .m2, append 00.skin.
        static void VirtualSkinPath(char* out, size_t outSz, const char* virtualM2Key) noexcept
        {
            RealSkinPath(out, outSz, virtualM2Key); // same operation: strip extension, append 00.skin
        }

        static uint16_t ReadU16(const std::vector<uint8_t>& bytes, size_t off) noexcept
        {
            return static_cast<uint16_t>(bytes[off] | (bytes[off + 1] << 8));
        }

        static uint32_t ReadU32(const std::vector<uint8_t>& bytes, size_t off) noexcept
        {
            return static_cast<uint32_t>(bytes[off])
                 | (static_cast<uint32_t>(bytes[off + 1]) << 8)
                 | (static_cast<uint32_t>(bytes[off + 2]) << 16)
                 | (static_cast<uint32_t>(bytes[off + 3]) << 24);
        }

        static void WriteU32(std::vector<uint8_t>& bytes, size_t off, uint32_t value) noexcept
        {
            bytes[off + 0] = static_cast<uint8_t>(value);
            bytes[off + 1] = static_cast<uint8_t>(value >> 8);
            bytes[off + 2] = static_cast<uint8_t>(value >> 16);
            bytes[off + 3] = static_cast<uint8_t>(value >> 24);
        }

        static void WriteLeU16(std::vector<uint8_t>& bytes, size_t off, uint16_t value) noexcept
        {
            bytes[off + 0] = static_cast<uint8_t>(value);
            bytes[off + 1] = static_cast<uint8_t>(value >> 8);
        }

        static void Align4(std::vector<uint8_t>& bytes)
        {
            while (bytes.size() & 3u) bytes.push_back(0);
        }

        static uint32_t ParseU32Span(const char* begin, const char* end, uint32_t fallback) noexcept
        {
            if (!begin || !end || begin >= end) return fallback;
            const char* p = begin;
            while (p < end && (*p == ' ' || *p == '\t')) ++p;
            if (p >= end || *p < '0' || *p > '9') return fallback;
            uint32_t value = 0;
            while (p < end && *p >= '0' && *p <= '9')
                value = value * 10u + static_cast<uint32_t>(*p++ - '0');
            return value;
        }

        struct MaterialPatch
        {
            uint32_t layer = 0xffffffffu;
            uint32_t textureType = 0xffffffffu;
            uint16_t batches[64] = {};
            uint16_t sections[64] = {};
            uint32_t batchCount = 0;
            uint32_t sectionCount = 0;
            bool hide = false;
            bool hideEdgeFade = false;
            const char* path = nullptr;
            size_t pathLen = 0;
        };

        static bool StartsWithCI(const char* s, size_t len, const char* prefix) noexcept
        {
            if (!s || !prefix) return false;
            size_t i = 0;
            while (prefix[i])
            {
                if (i >= len) return false;
                char a = s[i];
                char b = prefix[i];
                if (a >= 'A' && a <= 'Z') a = static_cast<char>(a - 'A' + 'a');
                if (b >= 'A' && b <= 'Z') b = static_cast<char>(b - 'A' + 'a');
                if (a != b) return false;
                ++i;
            }
            return true;
        }

        static bool IsHidePath(const char* path, size_t pathLen) noexcept
        {
            return StartsWithCI(path, pathLen, "__hide__") ||
                   StartsWithCI(path, pathLen, "hide");
        }

        static void ParseNumberList(const char* begin, const char* end,
                                    uint16_t* out, uint32_t* count, uint32_t maxCount) noexcept
        {
            if (!out || !count || !begin || !end) return;
            const char* p = begin;
            while (p < end && *count < maxCount)
            {
                while (p < end && (*p < '0' || *p > '9')) ++p;
                if (p >= end) break;
                uint32_t value = 0;
                while (p < end && *p >= '0' && *p <= '9')
                    value = value * 10u + static_cast<uint32_t>(*p++ - '0');
                if (value <= 0xffffu)
                    out[(*count)++] = static_cast<uint16_t>(value);
            }
        }

        static uint32_t ParseMaterialPatchSpec(const char* spec,
                                               MaterialPatch* out,
                                               uint32_t outCount) noexcept
        {
            if (!spec || !*spec || !out || outCount == 0) return 0;

            uint32_t count = 0;
            const char* part = spec;
            while (*part && count < outCount)
            {
                const char* end = part;
                while (*end && *end != '|') ++end;

                const char* c1 = part;
                while (c1 < end && *c1 != ':') ++c1;
                const char* c2 = c1 < end ? c1 + 1 : end;
                while (c2 < end && *c2 != ':') ++c2;
                const char* c3 = c2 < end ? c2 + 1 : end;
                while (c3 < end && *c3 != ':') ++c3;
                const char* eq = c3 < end ? c3 + 1 : end;
                while (eq < end && *eq != '=') ++eq;

                if (c1 < end && c2 < end && c3 < end && eq < end)
                {
                    MaterialPatch p = {};
                    p.layer = ParseU32Span(part, c1, 0xffffffffu);
                    p.textureType = ParseU32Span(c1 + 1, c2, 0xffffffffu);
                    ParseNumberList(c2 + 1, c3, p.batches, &p.batchCount, 64);
                    ParseNumberList(c3 + 1, eq, p.sections, &p.sectionCount, 64);
                    p.path = eq + 1;
                    while (p.path < end && (*p.path == ' ' || *p.path == '\t')) ++p.path;
                    const char* pathEnd = end;
                    while (pathEnd > p.path && (pathEnd[-1] == ' ' || pathEnd[-1] == '\t')) --pathEnd;
                    p.pathLen = static_cast<size_t>(pathEnd - p.path);
                    p.hideEdgeFade = StartsWithCI(p.path, p.pathLen, "__hide__edgefade");
                    p.hide = p.hideEdgeFade || IsHidePath(p.path, p.pathLen);
                    if (p.layer != 0xffffffffu && p.pathLen > 0 &&
                        (p.hideEdgeFade || p.batchCount > 0 || p.sectionCount > 0))
                    {
                        out[count++] = p;
                    }
                }

                if (!*end) break;
                part = end + 1;
            }
            return count;
        }

        static bool ContainsU16(const uint16_t* values, uint32_t count, uint16_t value) noexcept
        {
            for (uint32_t i = 0; i < count; ++i)
                if (values[i] == value) return true;
            return false;
        }

        static bool ReadTextureCombos(const std::vector<uint8_t>& modelBytes,
                                      std::vector<uint16_t>& out) noexcept
        {
            namespace fmt = wxl::structure::m2;
            if (modelBytes.size() < sizeof(fmt::M2Header)) return false;
            if (ReadU32(modelBytes, 0x00) != fmt::kMagicMD20) return false;

            const uint32_t comboCount = ReadU32(modelBytes, 0x80);
            const uint32_t comboOfs = ReadU32(modelBytes, 0x84);
            if (comboCount == 0) return false;
            if (comboOfs > modelBytes.size()) return false;
            if (comboCount > (modelBytes.size() - comboOfs) / sizeof(uint16_t)) return false;

            out.resize(comboCount);
            for (uint32_t i = 0; i < comboCount; ++i)
                out[i] = ReadU16(modelBytes, comboOfs + i * sizeof(uint16_t));
            return true;
        }

        static bool WriteTextureCombos(std::vector<uint8_t>& modelBytes,
                                       const std::vector<uint16_t>& combos) noexcept
        {
            if (combos.empty() || combos.size() > 0xffffffffu) return false;
            Align4(modelBytes);
            const uint32_t ofs = static_cast<uint32_t>(modelBytes.size());
            const size_t bytes = combos.size() * sizeof(uint16_t);
            modelBytes.resize(modelBytes.size() + bytes);
            for (size_t i = 0; i < combos.size(); ++i)
                WriteLeU16(modelBytes, ofs + i * sizeof(uint16_t), combos[i]);
            WriteU32(modelBytes, 0x80, static_cast<uint32_t>(combos.size()));
            WriteU32(modelBytes, 0x84, ofs);
            return true;
        }

        static uint32_t TextureFlags(const std::vector<uint8_t>& modelBytes,
                                     uint32_t textureIndex) noexcept
        {
            namespace fmt = wxl::structure::m2;
            if (modelBytes.size() < sizeof(fmt::M2Header)) return 0;
            if (ReadU32(modelBytes, 0x00) != fmt::kMagicMD20) return 0;

            const uint32_t texCount = ReadU32(modelBytes, 0x50);
            const uint32_t texOfs = ReadU32(modelBytes, 0x54);
            constexpr uint32_t kTexStride = sizeof(fmt::M2Texture);
            if (textureIndex >= texCount || texOfs > modelBytes.size()) return 0;
            if (texCount > (modelBytes.size() - texOfs) / kTexStride) return 0;
            return ReadU32(modelBytes, texOfs + textureIndex * kTexStride + 0x04);
        }

        static uint32_t AppendHardcodedTexture(std::vector<uint8_t>& modelBytes,
                                               const char* path,
                                               size_t pathLen,
                                               uint32_t flags) noexcept
        {
            namespace fmt = wxl::structure::m2;
            if (!path || pathLen == 0 || modelBytes.size() < sizeof(fmt::M2Header)) return 0xffffffffu;
            if (ReadU32(modelBytes, 0x00) != fmt::kMagicMD20) return 0xffffffffu;

            const uint32_t texCount = ReadU32(modelBytes, 0x50);
            const uint32_t texOfs = ReadU32(modelBytes, 0x54);
            constexpr uint32_t kTexStride = sizeof(fmt::M2Texture);
            if (texOfs > modelBytes.size()) return 0xffffffffu;
            if (texCount > (modelBytes.size() - texOfs) / kTexStride) return 0xffffffffu;
            if (texCount >= 0xffffu) return 0xffffffffu;

            Align4(modelBytes);
            const uint32_t pathOfs = static_cast<uint32_t>(modelBytes.size());
            modelBytes.insert(modelBytes.end(), path, path + pathLen);
            modelBytes.push_back(0);

            Align4(modelBytes);
            const uint32_t newTexOfs = static_cast<uint32_t>(modelBytes.size());
            std::vector<uint8_t> oldTextures(modelBytes.begin() + texOfs,
                                             modelBytes.begin() + texOfs + texCount * kTexStride);
            modelBytes.insert(modelBytes.end(), oldTextures.begin(), oldTextures.end());
            const size_t rec = modelBytes.size();
            modelBytes.resize(modelBytes.size() + kTexStride);
            WriteU32(modelBytes, rec + 0x00, fmt::kTexTypeHardcoded);
            // Preserve the replaced slot's wrap/clamp state. Animated UV overlays commonly
            // require both wrap bits; clearing them makes the texture clamp for most of its
            // scroll and visibly snap when the global sequence loops.
            WriteU32(modelBytes, rec + 0x04, flags);
            WriteU32(modelBytes, rec + 0x08, static_cast<uint32_t>(pathLen + 1));
            WriteU32(modelBytes, rec + 0x0C, pathOfs);

            WriteU32(modelBytes, 0x50, texCount + 1);
            WriteU32(modelBytes, 0x54, newTexOfs);
            return texCount;
        }

        static bool BatchMatchesPatch(const MaterialPatch& patch,
                                      const std::vector<uint8_t>& skinBytes,
                                      uint32_t batchIndex,
                                      size_t batchOfs,
                                      uint32_t submeshCount,
                                      uint32_t submeshOfs) noexcept
        {
            if (ContainsU16(patch.batches, patch.batchCount, static_cast<uint16_t>(batchIndex)))
                return true;

            if (patch.sectionCount == 0) return false;
            const uint16_t skinSectionIndex = ReadU16(skinBytes, batchOfs + 0x04);
            if (skinSectionIndex >= submeshCount) return false;
            const size_t sub = submeshOfs + static_cast<size_t>(skinSectionIndex) * 0x30;
            if (sub + 0x30 > skinBytes.size()) return false;
            const uint16_t sectionId = ReadU16(skinBytes, sub + 0x00);
            return ContainsU16(patch.sections, patch.sectionCount, sectionId);
        }

        static bool BatchLooksLikeEdgeFade(const std::vector<uint8_t>& skinBytes,
                                           size_t batchOfs) noexcept
        {
            if (batchOfs + sizeof(wxl::structure::m2::M2Batch) > skinBytes.size()) return false;
            const uint8_t flags = skinBytes[batchOfs + 0x00];
            const uint16_t shaderId = ReadU16(skinBytes, batchOfs + 0x02);
            return (flags & 0x80) != 0 && (shaderId == 0x4011 || shaderId == 0x8015);
        }

        static uint32_t RemoveHiddenBatches(std::vector<uint8_t>& skinBytes,
                                            const std::vector<uint8_t>& hidden,
                                            uint32_t batchCount,
                                            uint32_t batchOfs) noexcept
        {
            if (hidden.empty() || batchCount == 0) return 0;
            constexpr uint32_t kBatchStride = sizeof(wxl::structure::m2::M2Batch);

            std::vector<uint8_t> kept;
            kept.reserve(static_cast<size_t>(batchCount) * kBatchStride);
            uint32_t removed = 0;
            for (uint32_t bi = 0; bi < batchCount; ++bi)
            {
                const size_t b = batchOfs + static_cast<size_t>(bi) * kBatchStride;
                if (bi < hidden.size() && hidden[bi])
                {
                    ++removed;
                    continue;
                }
                kept.insert(kept.end(), skinBytes.begin() + b, skinBytes.begin() + b + kBatchStride);
            }
            if (removed == 0 || kept.empty()) return 0;

            Align4(skinBytes);
            const uint32_t newOfs = static_cast<uint32_t>(skinBytes.size());
            skinBytes.insert(skinBytes.end(), kept.begin(), kept.end());
            WriteU32(skinBytes, 0x24, static_cast<uint32_t>(kept.size() / kBatchStride));
            WriteU32(skinBytes, 0x28, newOfs);
            return removed;
        }

        static void PatchTargetedMaterialTextures(std::vector<uint8_t>& modelBytes,
                                                  std::vector<uint8_t>& skinBytes,
                                                  const char* materialPatchSpec) noexcept
        {
            if (!materialPatchSpec || !*materialPatchSpec) return;
            if (skinBytes.size() < 0x2C || std::memcmp(skinBytes.data(), "SKIN", 4) != 0) return;

            MaterialPatch patches[16];
            const uint32_t patchCount = ParseMaterialPatchSpec(materialPatchSpec, patches, 16);
            if (!patchCount) return;

            const uint32_t submeshCount = ReadU32(skinBytes, 0x1C);
            const uint32_t submeshOfs = ReadU32(skinBytes, 0x20);
            const uint32_t batchCount = ReadU32(skinBytes, 0x24);
            const uint32_t batchOfs = ReadU32(skinBytes, 0x28);
            if (submeshOfs > skinBytes.size() || batchOfs > skinBytes.size()) return;
            if (submeshCount > (skinBytes.size() - submeshOfs) / 0x30) return;
            if (batchCount > (skinBytes.size() - batchOfs) / sizeof(wxl::structure::m2::M2Batch)) return;

            std::vector<uint8_t> hiddenBatches(batchCount, 0);
            uint32_t markedHidden = 0;
            bool hasTexturePatches = false;
            for (uint32_t pi = 0; pi < patchCount; ++pi)
            {
                if (!patches[pi].hide)
                {
                    hasTexturePatches = true;
                    continue;
                }
                for (uint32_t bi = 0; bi < batchCount; ++bi)
                {
                    const size_t b = batchOfs + static_cast<size_t>(bi) * sizeof(wxl::structure::m2::M2Batch);
                    const bool match = patches[pi].hideEdgeFade
                        ? BatchLooksLikeEdgeFade(skinBytes, b)
                        : BatchMatchesPatch(patches[pi], skinBytes, bi, b, submeshCount, submeshOfs);
                    if (!match)
                        continue;
                    if (!hiddenBatches[bi])
                    {
                        hiddenBatches[bi] = 1;
                        ++markedHidden;
                    }
                }
            }

            std::vector<uint16_t> combos;
            if (hasTexturePatches && !ReadTextureCombos(modelBytes, combos))
            {
                VPathLog("  VPathPopulate: material texture patch skipped, no textureCombos spec='%s'",
                         materialPatchSpec);
                hasTexturePatches = false;
            }

            uint32_t patchedBatches = 0;
            uint32_t appendedTextures = 0;
            const size_t originalComboCount = combos.size();
            constexpr uint32_t kBatchStride = sizeof(wxl::structure::m2::M2Batch);
            for (uint32_t pi = 0; pi < patchCount; ++pi)
            {
                if (!hasTexturePatches) break;
                if (patches[pi].hide) continue;

                uint32_t replacementFlags = 0;
                bool hasTarget = false;
                for (uint32_t bi = 0; bi < batchCount; ++bi)
                {
                    const size_t b = batchOfs + static_cast<size_t>(bi) * kBatchStride;
                    if (bi < hiddenBatches.size() && hiddenBatches[bi]) continue;
                    if (!BatchMatchesPatch(patches[pi], skinBytes, bi, b, submeshCount, submeshOfs))
                        continue;

                    const uint16_t textureCount = ReadU16(skinBytes, b + 0x0E);
                    const uint16_t comboIndex = ReadU16(skinBytes, b + 0x10);
                    if (textureCount == 0 || patches[pi].layer >= textureCount) continue;
                    if (static_cast<uint32_t>(comboIndex) + textureCount > combos.size()) continue;
                    replacementFlags =
                        TextureFlags(modelBytes, combos[comboIndex + patches[pi].layer]);
                    hasTarget = true;
                    break;
                }
                if (!hasTarget) continue;

                const uint32_t newTexIndex = AppendHardcodedTexture(
                    modelBytes, patches[pi].path, patches[pi].pathLen, replacementFlags);
                if (newTexIndex == 0xffffffffu) continue;
                ++appendedTextures;

                for (uint32_t bi = 0; bi < batchCount; ++bi)
                {
                    const size_t b = batchOfs + static_cast<size_t>(bi) * kBatchStride;
                    if (bi < hiddenBatches.size() && hiddenBatches[bi]) continue;
                    if (!BatchMatchesPatch(patches[pi], skinBytes, bi, b, submeshCount, submeshOfs))
                        continue;

                    const uint16_t textureCount = ReadU16(skinBytes, b + 0x0E);
                    const uint16_t comboIndex = ReadU16(skinBytes, b + 0x10);
                    if (textureCount == 0 || patches[pi].layer >= textureCount) continue;
                    if (static_cast<uint32_t>(comboIndex) + textureCount > combos.size()) continue;
                    if (combos.size() + textureCount > 0xffffu) continue;

                    const uint16_t newComboIndex = static_cast<uint16_t>(combos.size());
                    for (uint32_t ti = 0; ti < textureCount; ++ti)
                        combos.push_back(combos[comboIndex + ti]);
                    combos[newComboIndex + patches[pi].layer] = static_cast<uint16_t>(newTexIndex);
                    WriteLeU16(skinBytes, b + 0x10, newComboIndex);
                    ++patchedBatches;
                }
            }

            if (combos.size() != originalComboCount && WriteTextureCombos(modelBytes, combos))
            {
                VPathLog("  VPathPopulate: material patch textures=%u batches=%u combos=%zu spec='%s'",
                         appendedTextures, patchedBatches, combos.size(), materialPatchSpec);
            }

            const uint32_t removed = RemoveHiddenBatches(skinBytes, hiddenBatches, batchCount, batchOfs);
            if (removed || markedHidden)
            {
                VPathLog("  VPathPopulate: material hide marked=%u removed=%u spec='%s'",
                         markedHidden, removed, materialPatchSpec);
            }
        }

        static void PatchReplaceableTextureTypes(std::vector<uint8_t>& modelBytes,
                                                 const char* texPath) noexcept
        {
            namespace fmt = wxl::structure::m2;

            if (modelBytes.size() < sizeof(fmt::M2Header)) return;
            if (ReadU32(modelBytes, 0x00) != fmt::kMagicMD20) return;

            const uint32_t texCount = ReadU32(modelBytes, 0x50);
            const uint32_t texOfs   = ReadU32(modelBytes, 0x54);
            constexpr uint32_t kTexStride = sizeof(fmt::M2Texture);
            if (texOfs > modelBytes.size()) return;
            if (texCount > (modelBytes.size() - texOfs) / kTexStride) return;

            const bool hasTexPath = texPath && *texPath;
            uint32_t patchedWeaponBlade = 0;
            uint32_t patchedObjectSkin = 0;
            for (uint32_t i = 0; i < texCount; ++i)
            {
                const size_t rec = static_cast<size_t>(texOfs) + static_cast<size_t>(i) * kTexStride;
                const uint32_t texType = ReadU32(modelBytes, rec + 0x00);
                if (texType == fmt::kTexTypeWeaponBlade)
                {
                    WriteU32(modelBytes, rec + 0x00, hasTexPath ? fmt::kTexTypeHardcoded : fmt::kTexTypeObjectSkin);
                    ++patchedWeaponBlade;
                    continue;
                }

                if (hasTexPath && texType == fmt::kTexTypeObjectSkin)
                {
                    WriteU32(modelBytes, rec + 0x00, fmt::kTexTypeHardcoded);
                    ++patchedObjectSkin;
                }
            }

            if (patchedWeaponBlade || patchedObjectSkin)
            {
                if (hasTexPath)
                    VPathLog("  VPathPopulate: promoted replaceable texture types weaponBlade=%u objectSkin=%u -> HARDCODED",
                             patchedWeaponBlade, patchedObjectSkin);
                else
                    VPathLog("  VPathPopulate: patched WEAPON_BLADE texture types=%u -> OBJECT_SKIN",
                             patchedWeaponBlade);
            }
        }

        static void PatchHardcodedModelTextures(std::vector<uint8_t>& modelBytes,
                                                const char* texPath) noexcept
        {
            namespace fmt = wxl::structure::m2;
            if (!texPath || !*texPath || modelBytes.size() < sizeof(fmt::M2Header)) return;
            if (ReadU32(modelBytes, 0x00) != fmt::kMagicMD20) return;

            const uint32_t texCount = ReadU32(modelBytes, 0x50);
            const uint32_t texOfs   = ReadU32(modelBytes, 0x54);
            constexpr uint32_t kTexStride = sizeof(fmt::M2Texture);
            if (texOfs > modelBytes.size()) return;
            if (texCount > (modelBytes.size() - texOfs) / kTexStride) return;

            uint32_t patchable = 0;
            for (uint32_t i = 0; i < texCount; ++i)
            {
                const size_t rec = static_cast<size_t>(texOfs) + static_cast<size_t>(i) * kTexStride;
                if (ReadU32(modelBytes, rec + 0x00) != fmt::kTexTypeHardcoded) continue;
                ++patchable;
            }
            if (!patchable) return;

            const size_t texLen = std::strlen(texPath);
            if (modelBytes.size() + texLen + 1 > 0xffffffffu) return;

            const uint32_t pathOfs = static_cast<uint32_t>(modelBytes.size());
            modelBytes.insert(modelBytes.end(), texPath, texPath + texLen);
            modelBytes.push_back(0);

            for (uint32_t i = 0; i < texCount; ++i)
            {
                const size_t rec = static_cast<size_t>(texOfs) + static_cast<size_t>(i) * kTexStride;
                if (ReadU32(modelBytes, rec + 0x00) != fmt::kTexTypeHardcoded) continue;
                WriteU32(modelBytes, rec + 0x08, static_cast<uint32_t>(texLen + 1));
                WriteU32(modelBytes, rec + 0x0C, pathOfs);
            }

            VPathLog("  VPathPopulate: patched hardcoded textures=%u tex='%s'", patchable, texPath);
        }

        static bool ContainsId(const uint16_t* ids, uint32_t count, uint16_t value) noexcept
        {
            for (uint32_t i = 0; i < count; ++i)
                if (ids[i] == value) return true;
            return false;
        }

        static void ApplySkinByteFilter(std::vector<uint8_t>& skinBytes,
                                        const uint16_t* geoIds,
                                        uint32_t geoCount) noexcept
        {
            if (geoCount == 0 || skinBytes.size() < 0x2C) return;
            if (std::memcmp(skinBytes.data(), "SKIN", 4) != 0) return;

            const uint32_t rawIndexCount = ReadU32(skinBytes, 0x0C);
            const uint32_t rawIndexOfs   = ReadU32(skinBytes, 0x10);
            const uint32_t submeshCount  = ReadU32(skinBytes, 0x1C);
            const uint32_t submeshOfs    = ReadU32(skinBytes, 0x20);
            if (rawIndexOfs > skinBytes.size() || submeshOfs > skinBytes.size()) return;
            if (rawIndexCount > (skinBytes.size() - rawIndexOfs) / sizeof(uint16_t)) return;
            if (submeshCount > (skinBytes.size() - submeshOfs) / 0x30) return;

            uint32_t kept = 0;
            uint32_t zeroed = 0;
            for (uint32_t si = 0; si < submeshCount; ++si)
            {
                const size_t sub = submeshOfs + si * 0x30;
                const uint16_t sectionId = ReadU16(skinBytes, sub + 0x00);
                const uint16_t level = ReadU16(skinBytes, sub + 0x02);
                const uint16_t indexStart = ReadU16(skinBytes, sub + 0x08);
                const uint16_t indexCount = ReadU16(skinBytes, sub + 0x0A);
                if (indexCount == 0) continue;

                uint32_t fullIndexStart = (static_cast<uint32_t>(level) << 16) | indexStart;
                if (fullIndexStart > rawIndexCount || indexCount > rawIndexCount - fullIndexStart)
                {
                    fullIndexStart = indexStart;
                    if (fullIndexStart > rawIndexCount || indexCount > rawIndexCount - fullIndexStart) continue;
                }

                if (ContainsId(geoIds, geoCount, sectionId))
                {
                    ++kept;
                    continue;
                }

                std::memset(skinBytes.data() + rawIndexOfs + fullIndexStart * sizeof(uint16_t),
                            0,
                            indexCount * sizeof(uint16_t));
                ++zeroed;
            }
            VPathLog("  VPathPopulate: prefiltered skin kept=%u zeroed=%u", kept, zeroed);
        }

        // Produces a lowercase .m2 path from a (possibly mixed-case, .mdx-extension) DBC path.
        // The host stores loose files as lowercase .m2; ReadGameFile must use that form.
        static void NormalizeRealPath(char* out, size_t outSz, const char* src) noexcept
        {
            char* dst  = out;
            char* dend = out + outSz - 1;
            while (*src && dst < dend) *dst++ = LowerAscii(*src++);
            *dst = '\0';
            // Replace trailing .mdx → .m2 (DBC uses .mdx; host stores as .m2).
            char* lastDot = nullptr;
            for (char* p = out; *p; ++p) if (*p == '.') lastDot = p;
            if (lastDot && std::strcmp(lastDot, ".mdx") == 0)
                { lastDot[1] = 'm'; lastDot[2] = '2'; lastDot[3] = '\0'; }
        }

        // ─── Client-side provider ─────────────────────────────────────────────

        static bool VirtualProvide(const char* name, std::vector<uint8_t>& out)
        {
            if (!name || !std::strstr(name, "_wxl_")) return false;
            VPathLog("  VirtualProvide: '%s'", name);
            auto it = g_virtualBytes.find(name);
            if (it == g_virtualBytes.end())
            {
                VPathLog("  VirtualProvide: NOT FOUND (table has %zu entries)", g_virtualBytes.size());
                return false;
            }
            VPathLog("  VirtualProvide: HIT (%zu bytes)", it->second.size());
            out = it->second;
            return true;
        }

        struct Registrar
        {
            Registrar() { wxl::runtime::storage::RegisterClientProvider(&VirtualProvide); }
        };
        static Registrar g_registrar;
    }

    // ─── Public API ───────────────────────────────────────────────────────────

    size_t VPathBuildKey(char* out, size_t outSz, void* cmo,
                         const char* realMdxPath,
                         const uint16_t* geoIds, uint32_t geoCount,
                         const char* texPath,
                         uint32_t variantKey,
                         const char* materialPatchSpec)
    {
        uint16_t sorted[16];
        uint32_t n = geoCount < 16 ? geoCount : 16;
        for (uint32_t i = 0; i < n; ++i) sorted[i] = geoIds[i];
        SortIds(sorted, n);
        return BuildKey(out, outSz, cmo, realMdxPath, sorted, n, texPath,
                        materialPatchSpec, variantKey);
    }

    bool VPathPopulate(void* cmo, const char* realMdxPath,
                       const uint16_t* geoIds, uint32_t geoCount,
                       const char* texPath,
                       uint32_t variantKey,
                       const char* materialPatchSpec)
    {
        uint16_t sorted[16];
        uint32_t n = geoCount < 16 ? geoCount : 16;
        for (uint32_t i = 0; i < n; ++i) sorted[i] = geoIds[i];
        SortIds(sorted, n);

        // Build virtual .mdx key.
        char vMdx[264];
        if (!BuildKey(vMdx, sizeof(vMdx), cmo, realMdxPath, sorted, n, texPath,
                      materialPatchSpec, variantKey)) return false;

        // No-op if already populated (same permutation on a re-equip cycle).
        if (g_virtualBytes.count(vMdx)) return true;

        // Build virtual .skin key.
        char vSkin[264];
        VirtualSkinPath(vSkin, sizeof(vSkin), vMdx);

        // Normalise the real path for host I/O: lowercase, .m2 extension.
        // DBC paths are mixed-case .mdx; the host stores loose files as lowercase .m2.
        char normPath[264];
        NormalizeRealPath(normPath, sizeof(normPath), realMdxPath);

        // Read real .m2 bytes (via normalized path).
        std::vector<uint8_t> mdxBytes;
        if (!ReadGameFile(normPath, mdxBytes))
        {
            VPathLog("  VPathPopulate: mdx READ FAILED '%s'", normPath);
            return false;
        }
        VPathLog("  VPathPopulate: mdx '%s' -> %zu bytes", normPath, mdxBytes.size());
        PatchReplaceableTextureTypes(mdxBytes, texPath);
        PatchHardcodedModelTextures(mdxBytes, texPath);

        // Read real 00.skin bytes.
        char rSkin[264];
        RealSkinPath(rSkin, sizeof(rSkin), normPath);
        std::vector<uint8_t> skinBytes;
        ReadGameFile(rSkin, skinBytes); // skin may be absent for some models; that is OK
        if (!skinBytes.empty())
            ApplySkinByteFilter(skinBytes, sorted, n);
        if (!skinBytes.empty())
            PatchTargetedMaterialTextures(mdxBytes, skinBytes, materialPatchSpec);
        VPathLog("  VPathPopulate: skin '%s' -> %zu bytes", rSkin, skinBytes.size());
        VPathLog("  VPathPopulate: vMdx='%s'", vMdx);
        VPathLog("  VPathPopulate: vSkin='%s'", vSkin);

        // Store in table.
        g_virtualBytes.emplace(vMdx,  std::move(mdxBytes));
        if (!skinBytes.empty())
            g_virtualBytes.emplace(vSkin, std::move(skinBytes));

        // Register both under cmo for cleanup.
        auto& paths = g_cmoVPaths[cmo];
        paths.emplace_back(vMdx);
        if (g_virtualBytes.count(vSkin)) paths.emplace_back(vSkin);
        return true;
    }

    void VPathEvictCmo(void* cmo)
    {
        auto it = g_cmoVPaths.find(cmo);
        if (it == g_cmoVPaths.end()) return;
        for (const auto& path : it->second)
            g_virtualBytes.erase(path);
        g_cmoVPaths.erase(it);
    }
}
