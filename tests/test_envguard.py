"""Tests for EnvGuard — schema parsing, validation, crypto, and profiles."""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from envguard.schema import (
    SchemaEntry,
    parse_schema,
    parse_env_file,
    bootstrap_schema,
    validate_schema,
    generate_example,
    doctor_check,
)
from envguard.crypto import encrypt_file, decrypt_file
from envguard.profiles import profile_list, profile_pin, profile_switch, profile_show


# ── fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_project():
    """Create a temporary project directory with clean state."""
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d)


@pytest.fixture
def project_with_env(tmp_project):
    """Create a project with a .env file."""
    (tmp_project / ".env").write_text(
        "DATABASE_URL=postgres://localhost:5432/mydb\n"
        "API_KEY=sk-abc123\n"
        "DEBUG=true\n"
        "PORT=8080\n"
    )
    return tmp_project


@pytest.fixture
def project_with_schema(tmp_project):
    """Create a project with .env.schema."""
    (tmp_project / ".env.schema").write_text(
        'DATABASE_URL  str   true   "PostgreSQL connection string"\n'
        'API_KEY       str   true   "API key"\n'
        'DEBUG         bool  false  "Enable debug"\n'
        'PORT          int   false  "Server port"  # default: 8080\n'
    )
    return tmp_project


# ── SchemaEntry tests ────────────────────────────────────────────────────

class TestSchemaEntry:
    def test_str_validation(self):
        e = SchemaEntry("NAME", "str", True, "a name")
        valid, val = e.validate_value("hello")
        assert valid is True
        assert val == "hello"

    def test_int_validation(self):
        e = SchemaEntry("PORT", "int", True, "port")
        valid, val = e.validate_value("8080")
        assert valid is True
        assert val == "8080"

    def test_int_invalid(self):
        e = SchemaEntry("PORT", "int", True, "port")
        valid, _ = e.validate_value("not-a-number")
        assert valid is False

    def test_bool_true_values(self):
        e = SchemaEntry("DEBUG", "bool", False, "debug")
        for val in ["true", "1", "yes", "TRUE", "Yes"]:
            valid, v = e.validate_value(val)
            assert valid is True
            assert v == "true"

    def test_bool_false_values(self):
        e = SchemaEntry("DEBUG", "bool", False, "debug")
        for val in ["false", "0", "no", "FALSE"]:
            valid, v = e.validate_value(val)
            assert valid is True
            assert v == "false"

    def test_bool_invalid(self):
        e = SchemaEntry("DEBUG", "bool", False, "debug")
        valid, _ = e.validate_value("maybe")
        assert valid is False

    def test_url_valid(self):
        e = SchemaEntry("URL", "url", True, "url")
        valid, _ = e.validate_value("https://example.com/path")
        assert valid is True

    def test_url_invalid(self):
        e = SchemaEntry("URL", "url", True, "url")
        valid, _ = e.validate_value("not-a-url")
        assert valid is False

    def test_email_valid(self):
        e = SchemaEntry("EMAIL", "email", True, "email")
        valid, _ = e.validate_value("user@example.com")
        assert valid is True

    def test_email_invalid(self):
        e = SchemaEntry("EMAIL", "email", True, "email")
        valid, _ = e.validate_value("not-email")
        assert valid is False

    def test_path_valid(self):
        e = SchemaEntry("PATH", "path", True, "path")
        valid, _ = e.validate_value("/usr/local/bin")
        assert valid is True

    def test_json_valid(self):
        e = SchemaEntry("CFG", "json", True, "json")
        valid, _ = e.validate_value('{"a": 1}')
        assert valid is True

    def test_json_invalid(self):
        e = SchemaEntry("CFG", "json", True, "json")
        valid, _ = e.validate_value("{broken}")
        assert valid is False

    def test_float_valid(self):
        e = SchemaEntry("RATE", "float", True, "rate")
        valid, _ = e.validate_value("3.14")
        assert valid is True

    def test_float_invalid(self):
        e = SchemaEntry("RATE", "float", True, "rate")
        valid, _ = e.validate_value("not-float")
        assert valid is False

    def test_to_schema_line(self):
        e = SchemaEntry("PORT", "int", False, "Server port", "8080")
        line = e.to_schema_line()
        assert "PORT" in line
        assert "int" in line
        assert "false" in line  # required=false
        assert "Server port" in line
        assert "8080" in line

    def test_to_example_line(self):
        e = SchemaEntry("PORT", "int", False, "Server port", "8080")
        line = e.to_example_line(fill_default=True)
        assert "PORT=8080" in line

    def test_to_example_line_no_default(self):
        e = SchemaEntry("KEY", "str", True, "A key")
        line = e.to_example_line()
        assert "your-value-here" in line


# ── Schema parsing tests ─────────────────────────────────────────────────

class TestParseSchema:
    def test_parse_empty(self, tmp_project):
        s = parse_schema(tmp_project / ".env.schema")
        assert s == {}

    def test_parse_basic(self, tmp_project):
        schema_file = tmp_project / ".env.schema"
        schema_file.write_text(
            'DB_URL  str  true  "database url"\n'
            'PORT  int  false  "port"\n'
        )
        s = parse_schema(schema_file)
        assert len(s) == 2
        assert "DB_URL" in s
        assert s["DB_URL"].type == "str"
        assert s["DB_URL"].required is True
        assert "PORT" in s
        assert s["PORT"].type == "int"
        assert s["PORT"].required is False

    def test_parse_with_default(self, tmp_project):
        schema_file = tmp_project / ".env.schema"
        schema_file.write_text('PORT  int  false  "port"  # default: 3000\n')
        s = parse_schema(schema_file)
        assert s["PORT"].default == "3000"

    def test_parse_skips_comments(self, tmp_project):
        schema_file = tmp_project / ".env.schema"
        schema_file.write_text(
            "# This is a comment\n"
            'KEY str true "a key"\n'
        )
        s = parse_schema(schema_file)
        assert len(s) == 1


class TestParseEnvFile:
    def test_parse_basic(self, tmp_project):
        env_file = tmp_project / ".env"
        env_file.write_text("KEY=value\nOTHER=123\n")
        pairs = parse_env_file(env_file)
        assert pairs == {"KEY": "value", "OTHER": "123"}

    def test_parse_skip_comments(self, tmp_project):
        env_file = tmp_project / ".env"
        env_file.write_text("# comment\nKEY=val\n")
        pairs = parse_env_file(env_file)
        assert pairs == {"KEY": "val"}

    def test_parse_skip_blank(self, tmp_project):
        env_file = tmp_project / ".env"
        env_file.write_text("KEY=val\n\nOTHER=456\n")
        pairs = parse_env_file(env_file)
        assert pairs == {"KEY": "val", "OTHER": "456"}

    def test_parse_not_exists(self, tmp_project):
        pairs = parse_env_file(tmp_project / ".env")
        assert pairs == {}


# ── Bootstrap Schema tests ───────────────────────────────────────────────

class TestBootstrapSchema:
    def test_bootstrap_from_env(self, project_with_env):
        bootstrap_schema(project_with_env)
        schema_file = project_with_env / ".env.schema"
        assert schema_file.exists()
        content = schema_file.read_text()
        assert "DATABASE_URL" in content
        assert "API_KEY" in content
        assert "DEBUG" in content
        assert "PORT" in content
        # DEBUG should be inferred as bool since values are true/false
        schema = parse_schema(schema_file)
        assert schema["DEBUG"].type == "bool"
        assert schema["PORT"].type == "int"

    def test_bootstrap_force_overwrite(self, project_with_env):
        # Create an existing schema
        (project_with_env / ".env.schema").write_text('EXISTING  str  true  "existing"\n')
        bootstrap_schema(project_with_env, force=True)
        content = (project_with_env / ".env.schema").read_text()
        assert "EXISTING" not in content  # overwritten entirely
        assert "DATABASE_URL" in content

    def test_bootstrap_merge(self, project_with_env):
        (project_with_env / ".env.schema").write_text('EXISTING  str  true  "existing"\n')
        bootstrap_schema(project_with_env, merge=True)
        content = (project_with_env / ".env.schema").read_text()
        assert "EXISTING" in content
        assert "DATABASE_URL" in content

    def test_bootstrap_no_env(self, tmp_project):
        bootstrap_schema(tmp_project)
        assert not (tmp_project / ".env.schema").exists()

    def test_bootstrap_already_exists(self, project_with_env):
        (project_with_env / ".env.schema").write_text('EXISTING  str  true  "existing"\n')
        bootstrap_schema(project_with_env)  # no force, no merge
        assert "EXISTING" in (project_with_env / ".env.schema").read_text()
        # Should not have added new entries
        assert "DATABASE_URL" not in (project_with_env / ".env.schema").read_text()


# ── Validation tests ─────────────────────────────────────────────────────

class TestValidateSchema:
    def test_validate_pass(self, project_with_env, project_with_schema):
        # Copy schema into project_with_env
        schema_content = (project_with_schema / ".env.schema").read_text()
        (project_with_env / ".env.schema").write_text(schema_content)
        rc = validate_schema(project_with_env)
        assert rc == 0

    def test_validate_missing_required(self, project_with_env, project_with_schema):
        (project_with_env / ".env.schema").write_text(
            'DATABASE_URL  str   true   "db"\n'
            'MISSING_KEY   str   true   "should be missing"\n'
        )
        rc = validate_schema(project_with_env)
        assert rc == 1

    def test_validate_type_mismatch(self, project_with_env):
        (project_with_env / ".env.schema").write_text(
            'PORT  int  true  "port"\n'
        )
        (project_with_env / ".env").write_text("PORT=not-a-number\n")
        rc = validate_schema(project_with_env)
        assert rc == 1

    def test_validate_strict_warnings(self, project_with_env):
        (project_with_env / ".env.schema").write_text(
            'PORT  int  false  "port"\n'
        )
        (project_with_env / ".env").write_text("OTHER=value\n")
        rc = validate_schema(project_with_env, strict=False)
        assert rc == 0  # warnings but no errors
        rc = validate_schema(project_with_env, strict=True)
        assert rc == 1  # warnings count as errors in strict mode

    def test_validate_silent(self, project_with_env):
        (project_with_env / ".env.schema").write_text(
            'PORT  int  true  "port"\n'
        )
        (project_with_env / ".env").write_text("OTHER=value\n")
        rc = validate_schema(project_with_env, silent=True)
        assert rc == 1

    def test_validate_no_schema(self, tmp_project):
        rc = validate_schema(tmp_project)
        assert rc == 1

    def test_validate_no_env(self, tmp_project):
        (tmp_project / ".env.schema").write_text('KEY  str  true  "key"\n')
        rc = validate_schema(tmp_project)
        assert rc == 1


# ── Generate example tests ───────────────────────────────────────────────

class TestGenerateExample:
    def test_generate_basic(self, project_with_schema):
        generate_example(project_with_schema)
        example = project_with_schema / ".env.example"
        assert example.exists()
        content = example.read_text()
        assert "DATABASE_URL" in content
        assert "PORT" in content
        assert "your-value-here" in content

    def test_generate_fill_default(self, project_with_schema):
        generate_example(project_with_schema, fill_defaults=True)
        example = project_with_schema / ".env.example"
        content = example.read_text()
        assert "PORT=8080" in content  # default from schema

    def test_generate_no_overwrite(self, project_with_schema):
        (project_with_schema / ".env.example").write_text("existing\n")
        generate_example(project_with_schema)
        assert (project_with_schema / ".env.example").read_text() == "existing\n"

    def test_generate_force(self, project_with_schema):
        (project_with_schema / ".env.example").write_text("old\n")
        generate_example(project_with_schema, force=True)
        assert "DATABASE_URL" in (project_with_schema / ".env.example").read_text()

    def test_generate_no_schema(self, tmp_project):
        generate_example(tmp_project)
        assert not (tmp_project / ".env.example").exists()


# ── Doctor tests ─────────────────────────────────────────────────────────

class TestDoctor:
    def test_doctor_clean(self, project_with_env, project_with_schema):
        (project_with_env / ".env.schema").write_text(
            (project_with_schema / ".env.schema").read_text()
        )
        (project_with_env / ".gitignore").write_text(".env\n.env.encrypted\n")
        (project_with_env / ".env.example").write_text("# Example env file\n")
        rc = doctor_check(project_with_env)
        assert rc == 0

    def test_doctor_missing_schema(self, project_with_env):
        rc = doctor_check(project_with_env)
        assert rc == 1  # missing schema

    def test_doctor_missing_gitignore(self, project_with_schema):
        (project_with_schema / ".env").write_text("KEY=val\n")
        rc = doctor_check(project_with_schema)
        # should flag missing .gitignore
        assert rc == 1


# ── Crypto tests ─────────────────────────────────────────────────────────

class TestCrypto:
    def test_encrypt_decrypt_roundtrip(self, tmp_project):
        env_file = tmp_project / ".env"
        env_file.write_text("SECRET=super-sensitive-data\nAPI_KEY=abc123\n")
        password = "test-password-123"

        encrypt_file(str(env_file), password, force=True)
        enc_file = tmp_project / ".env.encrypted"
        assert enc_file.exists()
        assert enc_file.stat().st_size > 0

        # decrypt to stdout and capture
        out_file = tmp_project / ".env.restored"
        decrypt_file(str(enc_file), password, output=str(out_file), force=True)
        assert out_file.read_text() == env_file.read_text()

    def test_encrypt_wrong_password(self, tmp_project):
        env_file = tmp_project / ".env"
        env_file.write_text("KEY=value\n")
        password = "correct-pw"
        wrong = "wrong-pw"

        encrypt_file(str(env_file), password, force=True)
        enc_file = tmp_project / ".env.encrypted"
        out_file = tmp_project / ".env.restored"

        with pytest.raises(SystemExit):
            decrypt_file(str(enc_file), wrong, output=str(out_file), force=True)
        assert not out_file.exists()

    def test_encrypt_output_path(self, tmp_project):
        env_file = tmp_project / ".env"
        env_file.write_text("KEY=val\n")
        custom_out = tmp_project / "custom.enc"
        encrypt_file(str(env_file), "pw", output=str(custom_out), force=True)
        assert custom_out.exists()
        decrypt_file(str(custom_out), "pw", output=str(tmp_project / "restored"), force=True)
        assert (tmp_project / "restored").read_text() == "KEY=val\n"

    def test_decrypt_bad_format(self, tmp_project):
        bogus = tmp_project / "bogus.enc"
        bogus.write_bytes(b"not-valid-data")
        with pytest.raises(SystemExit):
            decrypt_file(str(bogus), "pw", output=str(tmp_project / "out"), force=True)


# ── Profile tests ────────────────────────────────────────────────────────

class TestProfiles:
    def test_pin_and_list(self, tmp_project):
        # Create .env and pin it
        (tmp_project / ".env").write_text("KEY=val\nOTHER=123\n")
        profile_pin(tmp_project, "dev", force=True)
        assert (tmp_project / ".envguard" / "dev.env").exists()
        assert (tmp_project / ".envguard" / "dev.env").read_text() == "KEY=val\nOTHER=123\n"

    def test_switch_profile(self, tmp_project):
        (tmp_project / ".env").write_text("KEY=old\n")
        profile_pin(tmp_project, "staging", force=True)
        # Modify .env
        (tmp_project / ".env").write_text("KEY=new\n")
        profile_switch(tmp_project, "staging")
        assert (tmp_project / ".env").read_text() == "KEY=old\n"

    def test_pin_no_env(self, tmp_project):
        with pytest.raises(SystemExit):
            profile_pin(tmp_project, "test")

    def test_pin_existing_no_force(self, tmp_project):
        (tmp_project / ".env").write_text("KEY=val\n")
        profile_pin(tmp_project, "test", force=True)
        with pytest.raises(SystemExit):
            profile_pin(tmp_project, "test")

    def test_show_profile(self, tmp_project):
        (tmp_project / ".env").write_text("KEY=val\nSECRET=sk-abc\n")
        profile_pin(tmp_project, "prod", force=True)
        profile_show(tmp_project, "prod")

    def test_show_missing(self, tmp_project):
        with pytest.raises(SystemExit):
            profile_show(tmp_project, "nonexistent")


# ── CLI integration test ─────────────────────────────────────────────────

class TestCLI:
    def test_help_smoke(self):
        from envguard.cli import build_parser
        parser = build_parser()
        # Just make sure it doesn't crash
        assert parser.prog == "envguard"

    def test_version_smoke(self):
        from envguard import __version__
        assert isinstance(__version__, str)
        assert len(__version__) > 0
