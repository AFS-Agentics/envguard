"""CLI entrypoint for EnvGuard. All command parsing and dispatch lives here."""

import argparse
import sys
import os
from pathlib import Path

from envguard import __version__
from envguard.schema import (
    bootstrap_schema,
    validate_schema,
    generate_example,
)
from envguard.crypto import encrypt_file, decrypt_file
from envguard.profiles import profile_list, profile_switch, profile_pin, profile_show


def _resolve_project_root(target: str | None) -> Path:
    """Walk up from cwd (or target) looking for .env or .env.schema."""
    if target:
        root = Path(target).resolve()
        if not root.is_dir():
            print(f"❌ Error: '{target}' is not a directory", file=sys.stderr)
            sys.exit(1)
        return root
    # walk up from cwd looking for signs of an env-managed project
    cwd = Path.cwd().resolve()
    for ancestor in [cwd] + list(cwd.parents):
        if (ancestor / ".env").exists() or (ancestor / ".env.schema").exists():
            return ancestor
    return cwd


def cmd_init(args: argparse.Namespace) -> None:
    """Bootstrap .env.schema from existing .env file(s)."""
    root = _resolve_project_root(args.path)
    bootstrap_schema(root, force=args.force, merge=args.merge)


def cmd_validate(args: argparse.Namespace) -> None:
    """Validate .env file(s) against .env.schema."""
    root = _resolve_project_root(args.path)
    strict = getattr(args, "strict", False)
    silent = getattr(args, "silent", False)
    exit_code = validate_schema(root, strict=strict, silent=silent)
    sys.exit(exit_code)


def cmd_generate(args: argparse.Namespace) -> None:
    """Generate .env.example from the current schema."""
    root = _resolve_project_root(args.path)
    generate_example(root, force=args.force, fill_defaults=getattr(args, "fill", False))


def cmd_encrypt(args: argparse.Namespace) -> None:
    """Encrypt a .env file with a password."""
    password = _get_password(args)
    encrypt_file(args.file, password, output=args.output, force=args.force)


def cmd_decrypt(args: argparse.Namespace) -> None:
    """Decrypt a .env.encrypted file."""
    password = _get_password(args)
    decrypt_file(args.file, password, output=args.output, force=args.force)


def cmd_doctor(args: argparse.Namespace) -> None:
    """Health-check all env-related files in the project."""
    root = _resolve_project_root(args.path)
    from envguard.schema import doctor_check
    exit_code = doctor_check(root)
    sys.exit(exit_code)


def cmd_profile(args: argparse.Namespace) -> None:
    """Dispatch profile sub-commands."""
    root = _resolve_project_root(args.path)
    match args.profile_action:
        case "list":
            profile_list(root)
        case "show":
            profile_show(root, args.name)
        case "switch":
            profile_switch(root, args.name)
        case "pin":
            profile_pin(root, args.name, force=args.force)
        case _:
            print(f"❌ Unknown profile action: {args.profile_action}", file=sys.stderr)
            sys.exit(1)


# ── helpers ──────────────────────────────────────────────────────────────

def _get_password(args: argparse.Namespace) -> str:
    """Retrieve password from flag, env var, or prompt."""
    if args.password:
        return args.password
    env_pw = os.environ.get("ENVGUARD_PASSWORD")
    if env_pw:
        return env_pw
    try:
        import getpass
        pw = getpass.getpass("Enter encryption password: ")
        confirm = getpass.getpass("Confirm password: ")
        if pw != confirm:
            print("❌ Passwords do not match", file=sys.stderr)
            sys.exit(1)
        return pw
    except (EOFError, KeyboardInterrupt):
        print("\n❌ Password input cancelled", file=sys.stderr)
        sys.exit(1)


# ── main ─────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="envguard",
        description="🔐 EnvGuard — Manage, validate, and secure environment variables.",
        epilog="Report issues: https://github.com/AFS-Agentics/envguard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"envguard {__version__}")

    sub = p.add_subparsers(dest="command", required=True)

    # init
    init_p = sub.add_parser("init", help="Bootstrap .env.schema from existing .env file(s)")
    init_p.add_argument("--path", "-p", help="Project directory (default: auto-detect)")
    init_p.add_argument("--force", "-f", action="store_true", help="Overwrite if .env.schema exists")
    init_p.add_argument("--merge", "-m", action="store_true", help="Merge with existing .env.schema")
    init_p.set_defaults(func=cmd_init)

    # validate
    val_p = sub.add_parser("validate", help="Validate .env file(s) against .env.schema")
    val_p.add_argument("--path", "-p", help="Project directory (default: auto-detect)")
    val_p.add_argument("--strict", "-s", action="store_true", help="Treat warnings as errors")
    val_p.add_argument("--silent", action="store_true", help="Exit codes only, no output")
    val_p.set_defaults(func=cmd_validate)

    # generate
    gen_p = sub.add_parser("generate", help="Generate .env.example from .env.schema")
    gen_p.add_argument("--path", "-p", help="Project directory (default: auto-detect)")
    gen_p.add_argument("--force", "-f", action="store_true", help="Overwrite if .env.example exists")
    gen_p.add_argument("--fill", action="store_true", help="Fill defaults into .env.example values")
    gen_p.set_defaults(func=cmd_generate)

    # encrypt
    enc_p = sub.add_parser("encrypt", help="Encrypt a .env file with AES-256-GCM")
    enc_p.add_argument("file", help="Path to .env file to encrypt")
    enc_p.add_argument("--output", "-o", help="Output path (default: <file>.encrypted)")
    enc_p.add_argument("--password", help="Encryption password (uses ENVGUARD_PASSWORD or prompt if omitted)")
    enc_p.add_argument("--force", "-f", action="store_true", help="Overwrite existing output file")
    enc_p.set_defaults(func=cmd_encrypt)

    # decrypt
    dec_p = sub.add_parser("decrypt", help="Decrypt a .env.encrypted file")
    dec_p.add_argument("file", help="Path to encrypted file")
    dec_p.add_argument("--output", "-o", help="Output path (default: <basename>.env or stdout)")
    dec_p.add_argument("--password", help="Decryption password (uses ENVGUARD_PASSWORD or prompt if omitted)")
    dec_p.add_argument("--force", "-f", action="store_true", help="Overwrite existing output file")
    dec_p.set_defaults(func=cmd_decrypt)

    # doctor
    doc_p = sub.add_parser("doctor", help="Health-check all env files in the project")
    doc_p.add_argument("--path", "-p", help="Project directory (default: auto-detect)")
    doc_p.set_defaults(func=cmd_doctor)

    # profile
    prof_p = sub.add_parser("profile", help="Manage environment variable profiles")
    prof_sub = prof_p.add_subparsers(dest="profile_action", required=True)

    prof_list = prof_sub.add_parser("list", help="List saved profiles")
    prof_list.set_defaults(func=cmd_profile)

    prof_show = prof_sub.add_parser("show", help="Show details of a profile")
    prof_show.add_argument("name", help="Profile name")
    prof_show.set_defaults(func=cmd_profile)

    prof_switch = prof_sub.add_parser("switch", help="Switch to a saved profile")
    prof_switch.add_argument("name", help="Profile name to switch to")
    prof_switch.set_defaults(func=cmd_profile)

    prof_pin = prof_sub.add_parser("pin", help="Pin current .env state to a named profile")
    prof_pin.add_argument("name", help="Profile name")
    prof_pin.add_argument("--force", "-f", action="store_true", help="Overwrite existing profile")
    prof_pin.set_defaults(func=cmd_profile)

    for sp in [prof_list, prof_show, prof_switch, prof_pin]:
        sp.add_argument("--path", "-p", help="Project directory (default: auto-detect)")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
