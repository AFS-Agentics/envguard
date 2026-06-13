"""Schema validation — parse, validate, and generate .env schema files.

A .env.schema file defines expected environment variables with optional rules:

    # <KEY> <type>   <required> <description>
    DATABASE_URL  str   true    "PostgreSQL connection string"
    API_KEY       str   true    "API key for external service"
    DEBUG         bool  false   "Enable debug mode (0/1, true/false, yes/no)"
    PORT          int   false   "Server port (default: 8080)"
"""

import os
import re
import sys
from pathlib import Path
from typing import Any


# ── schema types ─────────────────────────────────────────────────────────

SCHEMA_TYPES = {"str", "int", "float", "bool", "path", "url", "email", "json"}
TRUTHY = {"1", "true", "yes", "y", "on", "enable", "enabled"}
FALSY = {"0", "false", "no", "n", "off", "disable", "disabled"}
SCHEMA_LINE_RE = re.compile(
    r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s+"
    r"(?P<type>str|int|float|bool|path|url|email|json)\s+"
    r"(?P<required>true|false)\s+"
    r'"(?P<description>[^"]*)"\s*'
    r"(?P<default>.*)?$"
)
ENV_LINE_RE = re.compile(r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)")


class SchemaEntry:
    __slots__ = ("key", "type", "required", "description", "default")

    def __init__(self, key: str, typ: str, required: bool, description: str,
                 default: str | None = None):
        self.key = key
        self.type = typ
        self.required = required
        self.description = description
        self.default = default

    def validate_value(self, value: str) -> tuple[bool, str]:
        """Return (is_valid, coerced_or_reason)."""
        v = value.strip()
        match self.type:
            case "str":
                return True, v
            case "int":
                try:
                    return True, str(int(v))
                except ValueError:
                    return False, f"'{v}' is not a valid integer"
            case "float":
                try:
                    return True, str(float(v))
                except ValueError:
                    return False, f"'{v}' is not a valid float"
            case "bool":
                if v.lower() in TRUTHY:
                    return True, "true"
                elif v.lower() in FALSY:
                    return True, "false"
                return False, f"'{v}' is not a valid boolean (use 1/0, true/false, yes/no)"
            case "path":
                # must be a plausible path (non-empty, no weird chars)
                if not v or "/\0" in v:
                    return False, f"'{v}' is not a valid path"
                return True, v
            case "url":
                if v.startswith(("http://", "https://", "ftp://", "file://")):
                    return True, v
                return False, f"'{v}' is not a valid URL (must start with http://, https://, etc.)"
            case "email":
                if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
                    return True, v
                return False, f"'{v}' is not a valid email address"
            case "json":
                try:
                    import json as _json
                    _json.loads(v)
                    return True, v
                except (ValueError, TypeError):
                    return False, f"'{v}' is not valid JSON"
        return True, v

    def to_schema_line(self) -> str:
        dflt = f"  # default: {self.default}" if self.default else ""
        return (
            f"{self.key}  {self.type}  {'true' if self.required else 'false'}  "
            f'"{self.description}"{dflt}'
        )

    def to_example_line(self, fill_default: bool = False) -> str:
        if fill_default and self.default is not None:
            return f"# {self.description}\n# {self.key}={self.default}"
        match self.type:
            case "str" | "url" | "email" | "json" | "path":
                placeholder = "your-value-here"
            case "int":
                placeholder = "0"
            case "float":
                placeholder = "0.0"
            case "bool":
                placeholder = "false"
            case _:
                placeholder = ""
        return f"# {self.description}\n# {self.key}={placeholder}"


# ── parse helpers ────────────────────────────────────────────────────────

def parse_schema(path: Path) -> dict[str, SchemaEntry]:
    """Parse a .env.schema file into a dict of SchemaEntry objects."""
    entries: dict[str, SchemaEntry] = {}
    if not path.exists():
        return entries

    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = SCHEMA_LINE_RE.match(line)
        if not m:
            print(f"  ⚠  {path}:{lineno} — skipping unparsable line: {line}", file=sys.stderr)
            continue
        d = m.groupdict()
        default = d.get("default", "").strip()
        if default.startswith("# default:"):
            default = default[len("# default:"):].strip()
        elif not default:
            default = None
        entry = SchemaEntry(
            key=d["key"],
            typ=d["type"],
            required=d["required"] == "true",
            description=d["description"],
            default=default,
        )
        entries[entry.key] = entry
    return entries


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env-style file into a dict of key-value pairs."""
    pairs: dict[str, str] = {}
    if not path.exists():
        return pairs
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = ENV_LINE_RE.match(line)
        if m:
            pairs[m.group("key")] = m.group("value")
    return pairs


# ── commands ─────────────────────────────────────────────────────────────

def _find_env_files(root: Path) -> list[Path]:
    """Find .env, .env.* files in root (excluding schema/example/backup/profiles)."""
    exclude = {".env.schema", ".env.example", ".env.backup"}
    files: list[Path] = []
    for f in root.iterdir():
        name = f.name
        if name in exclude:
            continue
        if name == ".env" or (name.startswith(".env.") and f.is_file()):
            files.append(f)
    # sort so .env comes first, then .env.dev, .env.prod, etc.
    files.sort(key=lambda p: p.name)
    return files


def _schema_path(root: Path) -> Path:
    return root / ".env.schema"


def _example_path(root: Path) -> Path:
    return root / ".env.example"


def _profiles_dir(root: Path) -> Path:
    pd = root / ".envguard"
    pd.mkdir(parents=True, exist_ok=True)
    return pd


def bootstrap_schema(root: Path, force: bool = False, merge: bool = False) -> None:
    """Auto-detect .env files and create .env.schema from them."""
    schema_file = _schema_path(root)
    if schema_file.exists() and not force and not merge:
        print(f"❌ .env.schema already exists at {schema_file}")
        print("   Use --force to overwrite or --merge to add new entries.")
        return

    env_files = _find_env_files(root)
    if not env_files:
        print(f"❌ No .env files found in {root}")
        return

    existing: dict[str, SchemaEntry] = {}
    if merge and schema_file.exists():
        existing = parse_schema(schema_file)

    # Collect all keys and infer types from values
    all_keys: dict[str, set[str]] = {}
    for ef in env_files:
        pairs = parse_env_file(ef)
        for k, v in pairs.items():
            if k not in all_keys:
                all_keys[k] = set()
            all_keys[k].add(v)

    new_entries: list[SchemaEntry] = []
    for key in sorted(all_keys):
        if key in existing:
            continue  # keep existing entry
        values = all_keys[key]
        # infer type from the sample values
        typ = _infer_type(values)
        entry = SchemaEntry(key=key, typ=typ, required=True, description=key.lower())
        new_entries.append(entry)

    if not new_entries and not merge:
        print("  ⚠  All env vars already have schema entries (nothing new to add).")
        return

    if merge:
        entries = list(existing.values()) + new_entries
        if not new_entries:
            print("  ✓  Nothing new to merge.")
            return
    else:
        entries = new_entries

    lines = ["# EnvGuard .env.schema", "# Format: KEY  type  required  \"description\"  # default: value", ""]
    for e in entries:
        lines.append(e.to_schema_line())

    schema_file.write_text("\n".join(lines) + "\n")
    print(f"  ✓  Wrote {len(entries)} entries to {schema_file}")


def _infer_type(values: set[str]) -> str:
    """Guess the best schema type from a set of actual values."""
    cleaned = {v.strip() for v in values if v.strip()}
    if not cleaned:
        return "str"

    # check bool
    if all(v.lower() in TRUTHY | FALSY for v in cleaned):
        return "bool"
    # check int
    if all(_is_int(v) for v in cleaned):
        return "int"
    # check float
    if all(_is_float(v) for v in cleaned):
        return "float"
    # check URL
    if all(v.startswith(("http://", "https://")) for v in cleaned):
        return "url"
    # check email
    if all(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v) for v in cleaned):
        return "email"
    # check JSON
    try:
        import json as _json
        if all(_json.loads(v) for v in cleaned):
            return "json"
    except (ValueError, TypeError):
        pass

    return "str"


def _is_int(v: str) -> bool:
    try:
        int(v.strip())
        return True
    except ValueError:
        return False


def _is_float(v: str) -> bool:
    try:
        float(v.strip())
        return True
    except ValueError:
        return False


def validate_schema(root: Path, strict: bool = False, silent: bool = False) -> int:
    """Validate .env file(s) against .env.schema. Returns exit code (0=ok)."""
    schema_file = _schema_path(root)
    schema = parse_schema(schema_file)
    if not schema:
        if not silent:
            print(f"❌ No schema found at {schema_file}")
            print("   Run 'envguard init' to bootstrap one.")
        return 1

    env_files = _find_env_files(root)
    if not env_files:
        if not silent:
            print(f"❌ No .env files found in {root}")
        return 1

    errors = 0
    warnings = 0

    for ef in env_files:
        pairs = parse_env_file(ef)
        checked_keys = set()
        for key, entry in schema.items():
            checked_keys.add(key)
            if key not in pairs:
                if entry.required:
                    if not silent:
                        print(f"  ❌ {ef.name}: missing required '{key}' ({entry.description})")
                    errors += 1
                else:
                    if not silent:
                        print(f"  ⚠  {ef.name}: optional '{key}' not set ({entry.description})")
                    warnings += 1
                continue
            valid, msg = entry.validate_value(pairs[key])
            if not valid:
                if not silent:
                    print(f"  ❌ {ef.name}: '{key}' {msg}")
                errors += 1

        # Check for keys in .env but not in schema (extra vars)
        for key in pairs:
            if key not in checked_keys:
                if not silent:
                    print(f"  ⚠  {ef.name}: '{key}' not declared in .env.schema")
                warnings += 1

    if silent:
        return 1 if errors > 0 else 0

    label = (ef.name if len(env_files) == 1 else f"{len(env_files)} env files")
    if errors == 0 and warnings == 0:
        print(f"  ✓  All {len(schema)} schema entries validated cleanly across {label}.")
        return 0

    summary = []
    if errors:
        summary.append(f"{errors} error{'s' if errors>1 else ''}")
    if warnings:
        summary.append(f"{warnings} warning{'s' if warnings>1 else ''}")
    print(f"  {' + '.join(summary)} found.")
    if strict:
        return 1 if (errors + warnings) > 0 else 0
    return 1 if errors > 0 else 0


def generate_example(root: Path, force: bool = False, fill_defaults: bool = False) -> None:
    """Generate .env.example from .env.schema."""
    schema_file = _schema_path(root)
    schema = parse_schema(schema_file)
    if not schema:
        print(f"❌ No schema found at {schema_file}")
        return

    example_file = _example_path(root)
    if example_file.exists() and not force:
        print(f"❌ {example_file} already exists. Use --force to overwrite.")
        return

    lines = [
        "# .env.example — generated by envguard",
        "# Copy this to .env and fill in your values.",
        "",
    ]
    for key in sorted(schema):
        entry = schema[key]
        lines.append(entry.to_example_line(fill_defaults))
        lines.append("")

    example_file.write_text("\n".join(lines) + "\n")
    print(f"  ✓  Generated {example_file} with {len(schema)} entries.")


def doctor_check(root: Path) -> int:
    """Health-check: check for common env file problems."""
    print(f"🔍 EnvGuard Doctor — {root}")
    print(f"{'─' * 50}")

    schema_file = _schema_path(root)
    example_file = _example_path(root)
    env_files = _find_env_files(root)
    profiles = _profiles_dir(root)

    issues = 0

    # Check schema exists
    if schema_file.exists():
        schema = parse_schema(schema_file)
        print(f"  ✓  .env.schema exists ({len(schema)} entries)")
    else:
        print(f"  ❌  .env.schema missing — run 'envguard init'")
        issues += 1
        schema = {}

    # Check .env.example
    if example_file.exists():
        print(f"  ✓  .env.example exists")
    else:
        print(f"  ⚠  .env.example missing — run 'envguard generate'")
        issues += 1

    # Check .env files
    if env_files:
        print(f"  ✓  {len(env_files)} .env file(s) found: {', '.join(f.name for f in env_files)}")
    else:
        print(f"  ⚠  No .env files found — project may not use env vars yet")
        issues += 1

    # Check for .gitignore entries
    gitignore = root / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        env_ignored = any(
            line.strip() in (".env", ".env.*", ".env.encrypted", ".envguard/")
            for line in content.splitlines()
        )
        if not env_ignored:
            print(f"  ⚠  .env and .env.encrypted not in .gitignore — add them!")
            issues += 1
        else:
            print(f"  ✓  .env files covered in .gitignore")
    else:
        print(f"  ⚠  No .gitignore found")

    # Check encrypted file conventions
    enc_files = list(root.glob("*.encrypted"))
    if enc_files:
        print(f"  ⚠  {len(enc_files)} encrypted file(s) found")

    # Check profiles
    profile_files = list(profiles.glob("*.env"))
    if profile_files:
        print(f"  ✓  {len(profile_files)} profile(s) saved: {', '.join(f.stem for f in profile_files)}")

    # Check schema vs env consistency
    if schema and env_files:
        result = validate_schema(root, silent=True)
        if result == 0:
            print(f"  ✓  All env files pass schema validation")
        else:
            print(f"  ❌  Schema validation found issues — run 'envguard validate'")
            issues += 1

    print(f"{'─' * 50}")
    if issues == 0:
        print("✅ All checks passed!")
        return 0
    print(f"⚠  {issues} issue(s) found.")
    return 1
