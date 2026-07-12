#!/usr/bin/env python3
"""
CleanSQL.py

Fix broken single-quoted strings in INSERTs/REPLACEs, batch single-row statements, and
guard against truncated tails.

What it does
------------
1) Repairs string literals conservatively:
   - inside strings: \'  -> ''
   - in-word apostrophes (e.g., O'Brien) -> ''   (only when between word chars)
   - keeps proper end-of-string quotes as single '
2) Normalizes statement endings to exactly one semicolon (no ';;').
3) Batches compatible single-row INSERTs/REPLACEs into multi-row blocks for speed.
4) Validates each VALUES (...) tuple; skips/logs any that still look malformed.
5) (Optional) Truncates the output to the last full semicolon to avoid a
   dangling partial statement at EOF.

Usage
-----
python CleanSQL.py input.sql --inplace
python CleanSQL.py input.sql -o output.sql --batch-size 1000 --bad-out bad.sql
python CleanSQL.py input.sql -o output.sql --no-batch
python CleanSQL.py input.sql -o output.sql --no-truncate-tail
"""

from __future__ import annotations
import argparse
import re
from pathlib import Path
from typing import List, Tuple, Dict, Optional

# --------------------------
# Tokenizing / splitting SQL
# --------------------------

def split_statements(sql_text: str) -> List[str]:
    """
    Split SQL into statements by semicolons while respecting single-quoted strings.
    Keeps comments/whitespace attached to their statements.
    """
    stmts: List[str] = []
    buf: List[str] = []
    in_str = False
    i = 0
    n = len(sql_text)

    while i < n:
        ch = sql_text[i]
        buf.append(ch)

        if in_str:
            if ch == "\\":
                # consume escaped char inside string
                if i + 1 < n:
                    buf.append(sql_text[i + 1])
                    i += 1
            elif ch == "'":
                # doubled quote stays inside string
                if i + 1 < n and sql_text[i + 1] == "'":
                    buf.append("'")
                    i += 1
                else:
                    in_str = False
        else:
            if ch == "'":
                in_str = True

        # statement boundary
        if not in_str and ch == ";":
            stmts.append("".join(buf).strip())
            buf = []
        i += 1

    rest = "".join(buf).strip()
    if rest:
        stmts.append(rest)
    return stmts


# --------------------------
# String literal correction
# --------------------------

_word_char = re.compile(r"[A-Za-z0-9_]")

def fix_string_literals(stmt: str) -> str:
    """
    Walk stmt and fix string literals conservatively:
      - inside strings: \' -> ''
      - in-word apostrophes (prev and next are word chars) -> ''
      - end-of-string quotes remain single
    """
    out: List[str] = []
    i, n = 0, len(stmt)
    in_str = False

    while i < n:
        ch = stmt[i]

        if not in_str:
            out.append(ch)
            if ch == "'":
                in_str = True
            i += 1
            continue

        # Inside a string -----------------
        if ch == "\\":
            # If it escapes a single quote, convert to doubled quote
            if i + 1 < n and stmt[i + 1] == "'":
                out.append("''")
                i += 2
            else:
                # keep other escapes verbatim
                out.append(ch)
                i += 1
            continue

        if ch == "'":
            # Already doubled -> keep doubled
            if i + 1 < n and stmt[i + 1] == "'":
                out.append("''")
                i += 2
                continue

            # Decide: end-of-string or apostrophe-in-word?
            prev_c = out[-1] if out else ""
            next_c = stmt[i + 1] if i + 1 < n else ""

            prev_is_word = bool(_word_char.match(prev_c)) if prev_c else False
            next_is_word = bool(_word_char.match(next_c)) if next_c else False

            if prev_is_word and next_is_word:
                # in-word apostrophe (O'Brien) -> double it
                out.append("''")
                i += 1
            else:
                # Peek ahead for end-of-string conditions
                j = i + 1
                while j < n and stmt[j] in " \t\r\n":
                    j += 1
                if j >= n or stmt[j] in {",", ")", ";"}:
                    # treat as string terminator
                    out.append("'")
                    in_str = False
                    i += 1
                else:
                    # Followed by some other token; safer to escape
                    out.append("''")
                    i += 1
            continue

        # Normal char inside string
        out.append(ch)
        i += 1

    # If file ended while inside a string, close it
    if in_str:
        out.append("'")
    return "".join(out)


def ensure_one_semicolon(stmt: str) -> str:
    """Strip trailing semicolons/spaces and ensure exactly one semicolon."""
    core = stmt.rstrip()
    core = core.rstrip(";")
    return core + ";"


# --------------------------
# INSERT parsing and batching
# --------------------------

_insert_re = re.compile(
    r"""^\s*((?:INSERT(?:\s+IGNORE|\s+DELAYED|\s+LOW_PRIORITY)?|REPLACE(?:\s+DELAYED|\s+LOW_PRIORITY)?)\s+INTO)\s+
         (`?[\w.]+`?)\s*
         \(\s*([^)]+?)\s*\)\s*
         VALUES\s*\(\s*(.+)\s*\)\s*;?\s*$""",
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

def parse_single_row_insert(stmt: str) -> Optional[Tuple[str, str, str, str]]:
    """
    Return (verb, table, columns, values_tuple_str) for a single-row INSERT/REPLACE.
    Only matches INSERT/REPLACE ... (cols) VALUES (...).
    """
    m = _insert_re.match(stmt.strip())
    if not m:
        return None
    verb, table, cols, values = m.groups()
    return verb, table, cols, values

def _normalize_ident(s: str) -> str:
    # normalize identifier for grouping (remove backticks, lowercased)
    return s.strip().strip("`").lower()

def _normalize_cols(cols: str) -> str:
    # normalize columns list for grouping (strip spaces/backticks, lowercased)
    parts = []
    for c in cols.split(","):
        parts.append(_normalize_ident(c))
    return ",".join(parts)

def validate_tuple(values: str) -> bool:
    """
    Returns True if the tuple body has balanced single quotes and ends outside a string.
    Treats '' as an escaped single quote and \' as well.
    """
    i, n = 0, len(values)
    in_str = False
    while i < n:
        ch = values[i]
        if not in_str:
            if ch == "'":
                in_str = True
            i += 1
            continue
        # inside string:
        if ch == "\\" and i + 1 < n:
            # skip escaped next char (\' or \anything)
            i += 2
            continue
        if ch == "'":
            # doubled quote => skip both, stay inside
            if i + 1 < n and values[i + 1] == "'":
                i += 2
                continue
            # close string
            in_str = False
            i += 1
            continue
        i += 1
    return not in_str  # must end outside a string

def batch_inserts(
    statements: List[str],
    batch_size: int = 500,
    bad_out_path: Optional[Path] = None,
    enable_batch: bool = True,
) -> List[str]:
    """
    Group compatible single-row INSERTs/REPLACEs into multi-row blocks.
    Any tuple that still fails quote validation is skipped and written
    as an individual INSERT to `bad_out_path` (if provided).
    If enable_batch is False, returns statements unchanged.
    """
    if not enable_batch:
        return statements

    groups: Dict[Tuple[str, str, str], List[Tuple[str, str, str, str]]] = {}
    parsed_cache: List[Tuple[bool, Optional[Tuple[str, str, str]]]] = []

    # Collect
    for stmt in statements:
        parsed = parse_single_row_insert(stmt)
        if not parsed:
            parsed_cache.append((False, None))
            continue
        verb, table, cols, values = parsed
        key = (verb.upper(), _normalize_ident(table), _normalize_cols(cols))
        groups.setdefault(key, []).append((verb, table, cols, values.strip()))
        parsed_cache.append((True, key))

    bad_out = None
    if bad_out_path is not None:
        bad_out = bad_out_path.open("w", encoding="utf-8")

    final: List[str] = []
    emitted: set[Tuple[str, str, str]] = set()

    # Emit in original order (first time we see a group's stmt)
    for idx, (is_ins, key) in enumerate(parsed_cache):
        if not is_ins:
            # passthrough non-INSERT or unrecognized INSERTs
            final.append(statements[idx])
            continue
        assert key is not None
        if key in emitted:
            continue

        rows = groups.get(key, [])
        if not rows:
            continue

        # Keep valid tuples; log invalid ones
        good_vals: List[str] = []
        for (verb_raw, table_raw, cols_raw, values_body) in rows:
            if validate_tuple(values_body):
                good_vals.append(values_body)
            else:
                if bad_out is not None:
                    bad_out.write(f"{verb_raw} {table_raw} ({cols_raw}) VALUES ({values_body});\n")

        if good_vals:
            # preserve original formatting from first row for verb/table/cols
            verb_raw, table_raw, cols_raw, _ = rows[0]
            for i in range(0, len(good_vals), batch_size):
                chunk = good_vals[i:i + batch_size]
                final.append(
                    f"{verb_raw} {table_raw} ({cols_raw}) VALUES "
                    + ", ".join(f"({v})" for v in chunk)
                    + ";"
                )

        emitted.add(key)

    if bad_out is not None:
        bad_out.close()

    return final


# --------------------------
# Tail truncation
# --------------------------

def truncate_to_last_semicolon(text: str) -> str:
    """
    Return text up to and including the last semicolon.
    If none found, return empty string (avoids dangling partial statement).
    """
    i = text.rfind(";")
    return text[: i + 1] if i != -1 else ""


# --------------------------
# Main pipeline
# --------------------------

def process_sql(
    sql_text: str,
    batch_size: int,
    bad_out: Optional[Path],
    enable_batch: bool,
    truncate_tail: bool,
) -> str:
    # 1) split
    stmts = split_statements(sql_text)

    # 2) fix strings + one semicolon
    fixed = [ensure_one_semicolon(fix_string_literals(s)) for s in stmts if s.strip()]

    # 3) batch INSERTs/REPLACEs (and validate tuples)
    batched = batch_inserts(
        fixed,
        batch_size=batch_size,
        bad_out_path=bad_out,
        enable_batch=enable_batch,
    )

    # 4) Join with newline
    result = "\n".join(batched) + "\n"

    # 5) (optional) guard against trailing partial stmt
    if truncate_tail:
        result = truncate_to_last_semicolon(result)

    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path, help="Output .sql file (default: input.fixed.sql)")
    ap.add_argument("--inplace", action="store_true", help="Overwrite input file")
    ap.add_argument("--batch-size", type=int, default=500, help="Rows per INSERT/REPLACE block")
    ap.add_argument("--bad-out", type=Path, default=None, help="Write any skipped/invalid tuples here")
    ap.add_argument("--no-batch", action="store_true", help="Do not batch INSERTs; only fix strings/semicolons")
    ap.add_argument("--no-truncate-tail", action="store_true", help="Do not truncate to last full statement")
    args = ap.parse_args()

    text = args.input.read_text(encoding="utf-8", errors="ignore")
    result = process_sql(
        text,
        batch_size=args.batch_size,
        bad_out=args.bad_out,
        enable_batch=not args.no_batch,
        truncate_tail=not args.no_truncate_tail,
    )

    if args.inplace:
        args.input.write_text(result, encoding="utf-8")
        print(f"[OK] Wrote fixed SQL in-place: {args.input}")
        if args.bad_out:
            print(f"[INFO] Any invalid tuples were written to: {args.bad_out}")
    else:
        out = args.output or args.input.with_suffix(args.input.suffix + ".fixed.sql")
        out.write_text(result, encoding="utf-8")
        print(f"[OK] Wrote: {out}")
        if args.bad_out:
            print(f"[INFO] Any invalid tuples were written to: {args.bad_out}")

if __name__ == "__main__":
    main()
