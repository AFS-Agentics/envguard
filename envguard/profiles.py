"""Profile management — save and switch between named .env configurations.

Profiles are stored as .env files under .envguard/<name>.env.
"""

import os
import sys
import shutil
from pathlib import Path


def _profiles_dir(root: Path) -> Path:
    pd = root / ".envguard"
    pd.mkdir(parents=True, exist_ok=True)
    return pd


def _env_path(root: Path) -> Path:
    return root / ".env"


def profile_list(root: Path) -> None:
    """List all saved profiles."""
    pdir = _profiles_dir(root)
    profiles = sorted(pdir.glob("*.env"))
    if not profiles:
        print(f"  ℹ   No profiles saved yet in {pdir}")
        print("  💡  Run 'envguard profile pin <name>' to save one.")
        return

    # Detect current profile by comparing .env content
    current_env = _env_path(root)
    current_pairs = _parse_env(current_env) if current_env.exists() else {}

    print(f"  📋  Profiles in {root.name}/.envguard:")
    for pf in profiles:
        name = pf.stem
        profile_pairs = _parse_env(pf)
        active = " ← active" if profile_pairs and profile_pairs == current_pairs else ""
        print(f"     • {name}{active}")
    print(f"     ({len(profiles)} profile(s))")


def profile_show(root: Path, name: str) -> None:
    """Show the contents of a saved profile."""
    pdir = _profiles_dir(root)
    profile_file = pdir / f"{name}.env"
    if not profile_file.exists():
        print(f"❌ Profile '{name}' not found at {profile_file}")
        sys.exit(1)
    pairs = _parse_env(profile_file)
    print(f"  📄  Profile: {name}")
    for key, value in sorted(pairs.items()):
        masked = value[:3] + "****" if len(value) > 6 and any(c in value for c in ["sk", "key", "token", "secret", "pass"]) else value
        print(f"     {key}={masked}")
    print(f"     ({len(pairs)} variables)")


def profile_switch(root: Path, name: str) -> None:
    """Switch .env to a saved profile (copy profile → .env)."""
    pdir = _profiles_dir(root)
    profile_file = pdir / f"{name}.env"
    if not profile_file.exists():
        print(f"❌ Profile '{name}' not found at {profile_file}")
        print(f"   Available: {', '.join(p.stem for p in pdir.glob('*.env'))}")
        sys.exit(1)

    env_file = _env_path(root)
    # Backup current .env if it exists
    if env_file.exists():
        backup = env_file.with_name(".env.backup")
        shutil.copy2(env_file, backup)
        print(f"  ℹ   Backed up current .env → .env.backup")

    shutil.copy2(profile_file, env_file)
    print(f"  ✓  Switched to profile '{name}'")
    print(f"  ℹ   Run 'envguard doctor' to validate the new env.")


def profile_pin(root: Path, name: str, force: bool = False) -> None:
    """Pin current .env state to a named profile."""
    env_file = _env_path(root)
    if not env_file.exists():
        print(f"❌ No .env file found in {root}")
        sys.exit(1)

    pdir = _profiles_dir(root)
    profile_file = pdir / f"{name}.env"
    if profile_file.exists() and not force:
        print(f"❌ Profile '{name}' already exists. Use --force to overwrite.")
        sys.exit(1)

    shutil.copy2(env_file, profile_file)
    print(f"  ✓  Pinned current .env as profile '{name}'")


# ── internal helpers ─────────────────────────────────────────────────────

def _parse_env(path: Path) -> dict[str, str]:
    """Simple .env parser (comments and blank lines are skipped)."""
    pairs: dict[str, str] = {}
    if not path.exists():
        return pairs
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            pairs[key.strip()] = value.strip()
    return pairs
