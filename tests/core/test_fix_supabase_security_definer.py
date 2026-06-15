import importlib.util
import io
import sys
from pathlib import Path


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "fix_supabase_security_definer.py"
    )
    spec = importlib.util.spec_from_file_location(
        "fix_supabase_security_definer_module", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FIX = _load_module()


class _DummyConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        return None


def test_build_revoke_sql_quotes_identifiers():
    state = FIX.FunctionSecurityState(
        schema_name='pub"lic',
        function_name="rls_auto_enable",
        identity_args="",
        is_security_definer=True,
        anon_execute=True,
        authenticated_execute=True,
    )

    sql = FIX.build_revoke_sql(state)

    assert (
        sql == 'revoke execute on function "pub""lic"."rls_auto_enable"() '
        "from public, anon, authenticated;"
    )


def test_main_dry_run_prints_revoke_sql(tmp_path, monkeypatch):
    env_path = tmp_path / ".env.hosted.local"
    env_path.write_text(
        "NEUROCORE_PRODUCTION_DATABASE_URL=postgresql://example\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(FIX, "_connect", lambda _: _DummyConnection())
    monkeypatch.setattr(
        FIX,
        "fetch_function_states",
        lambda connection, schema_name, function_name: [
            FIX.FunctionSecurityState(
                schema_name=schema_name,
                function_name=function_name,
                identity_args="",
                is_security_definer=True,
                anon_execute=True,
                authenticated_execute=True,
            )
        ],
    )
    apply_calls = []
    monkeypatch.setattr(
        FIX,
        "apply_revoke_execute",
        lambda connection, state: apply_calls.append(state.signature),
    )

    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = FIX.main(
        ["--env-file", str(env_path)],
        stdout=stdout,
        stderr=stderr,
        environ={},
    )

    assert exit_code == 0
    assert not apply_calls
    assert (
        "Dry run only. Rerun with --apply to execute the revoke." in stdout.getvalue()
    )
    assert "revoke execute on function" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_main_apply_revokes_and_verifies(tmp_path, monkeypatch):
    env_path = tmp_path / ".env.hosted.local"
    env_path.write_text(
        "NEUROCORE_PRODUCTION_DATABASE_URL=postgresql://example\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(FIX, "_connect", lambda _: _DummyConnection())
    states = [
        FIX.FunctionSecurityState(
            schema_name="public",
            function_name="rls_auto_enable",
            identity_args="",
            is_security_definer=True,
            anon_execute=True,
            authenticated_execute=True,
        ),
        FIX.FunctionSecurityState(
            schema_name="public",
            function_name="rls_auto_enable",
            identity_args="",
            is_security_definer=True,
            anon_execute=False,
            authenticated_execute=False,
        ),
    ]
    monkeypatch.setattr(
        FIX,
        "fetch_function_states",
        lambda connection, schema_name, function_name: [states.pop(0)],
    )
    apply_calls = []
    monkeypatch.setattr(
        FIX,
        "apply_revoke_execute",
        lambda connection, state: apply_calls.append(state.signature),
    )

    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = FIX.main(
        ["--env-file", str(env_path), "--apply"],
        stdout=stdout,
        stderr=stderr,
        environ={},
    )

    assert exit_code == 0
    assert apply_calls == ['"public"."rls_auto_enable"()']
    assert "Fix applied and verified." in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_main_requires_args_for_overloaded_functions(tmp_path, monkeypatch):
    env_path = tmp_path / ".env.hosted.local"
    env_path.write_text(
        "NEUROCORE_PRODUCTION_DATABASE_URL=postgresql://example\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(FIX, "_connect", lambda _: _DummyConnection())
    monkeypatch.setattr(
        FIX,
        "fetch_function_states",
        lambda connection, schema_name, function_name: [
            FIX.FunctionSecurityState(
                schema_name=schema_name,
                function_name=function_name,
                identity_args="",
                is_security_definer=True,
                anon_execute=True,
                authenticated_execute=True,
            ),
            FIX.FunctionSecurityState(
                schema_name=schema_name,
                function_name=function_name,
                identity_args="tenant_id uuid",
                is_security_definer=True,
                anon_execute=True,
                authenticated_execute=True,
            ),
        ],
    )

    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = FIX.main(
        ["--env-file", str(env_path)],
        stdout=stdout,
        stderr=stderr,
        environ={},
    )

    assert exit_code == 1
    assert "Multiple overloads found for public.rls_auto_enable." in stderr.getvalue()
