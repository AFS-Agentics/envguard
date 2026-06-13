"""Encryption / decryption using AES-256-GCM.

Uses the cryptography library for cross-platform, audited crypto.
Stores a random salt and nonce alongside the ciphertext for
deterministic-key derivation via PBKDF2.
"""

import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# ── constants ────────────────────────────────────────────────────────────

SALT_SIZE = 16
NONCE_SIZE = 12
PBKDF2_ITERATIONS = 600_000
MAGIC = b"EGCM"  # EnvGuard Ciphertext Marker
HEADER_SIZE = len(MAGIC) + SALT_SIZE + NONCE_SIZE


# ── key derivation ───────────────────────────────────────────────────────

def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a 256-bit AES key from a password using PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


# ── encrypt / decrypt ────────────────────────────────────────────────────

def encrypt_file(input_path: str, password: str,
                 output: str | None = None, force: bool = False) -> None:
    """Encrypt a .env file, writing <input>.encrypted by default."""
    in_path = Path(input_path).resolve()
    if not in_path.exists():
        print(f"❌ File not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    if output:
        out_path = Path(output).resolve()
    else:
        out_path = in_path.with_name(in_path.name + ".encrypted")

    if out_path.exists() and not force:
        print(f"❌ Output exists: {out_path}. Use --force to overwrite.", file=sys.stderr)
        sys.exit(1)

    plaintext = in_path.read_bytes()

    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    key = _derive_key(password, salt)

    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    # Format: MAGIC + SALT + NONCE + CIPHERTEXT
    payload = MAGIC + salt + nonce + ciphertext
    out_path.write_bytes(payload)

    # Remove original? No — safety first, let user rm manually or use --wipe
    print(f"  ✓  Encrypted: {in_path.name} → {out_path}")
    print(f"  🔑  Algorithm: AES-256-GCM with PBKDF2 ({PBKDF2_ITERATIONS:,} iterations)")


def decrypt_file(input_path: str, password: str,
                 output: str | None = None, force: bool = False) -> None:
    """Decrypt an .env.encrypted file."""
    in_path = Path(input_path).resolve()
    if not in_path.exists():
        print(f"❌ File not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    payload = in_path.read_bytes()
    if len(payload) < HEADER_SIZE:
        print(f"❌ Corrupted file: too short ({len(payload)} bytes)", file=sys.stderr)
        sys.exit(1)

    if payload[:len(MAGIC)] != MAGIC:
        print(f"❌ Not a valid EnvGuard encrypted file (bad magic bytes)", file=sys.stderr)
        sys.exit(1)

    offset = len(MAGIC)
    salt = payload[offset:offset + SALT_SIZE]
    nonce = payload[offset + SALT_SIZE:offset + SALT_SIZE + NONCE_SIZE]
    ciphertext = payload[offset + SALT_SIZE + NONCE_SIZE:]

    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)

    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        print(f"❌ Decryption failed: {exc}", file=sys.stderr)
        print("   Wrong password or corrupted file.", file=sys.stderr)
        sys.exit(1)

    if output:
        out_path = Path(output).resolve()
        if out_path.exists() and not force:
            print(f"❌ Output exists: {out_path}. Use --force.", file=sys.stderr)
            sys.exit(1)
        out_path.write_bytes(plaintext)
        print(f"  ✓  Decrypted: {in_path.name} → {out_path}")
    else:
        # Print to stdout (safe for piping / eval)
        sys.stdout.buffer.write(plaintext)
