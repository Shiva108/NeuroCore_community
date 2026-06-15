"""Harden exposed Supabase SECURITY DEFINER functions."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, TextIO

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from neurocore.core.operator_state import load_env_file


@dataclass(frozen=True)
class FunctionSecurityState:
    schema_name: str
    function_name: str
    identity_args: str
    is_security_definer: bool
    anon_execute: bool
    authenticated_execute: bool

    @property
    def signature(self) -> str:
        return (
            f"{_quote_ident(self.schema_name)}."
            f"{_quote_ident(self.function_name)}({self.identity_args})"
        )

    @property
    def publicly_callable(self) -> bool:
        return self.anon_execute or self.authenticated_execute


class ScriptError(RuntimeError):
    """Raised when the fix cannot be prepared or verified safely."""


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Revoke anon/authenticated execute permissions from an exposed "
            "Supabase SECURITY DEFINER function. Dry-run by default."
        )
    )
    parser.add_argument(
        "--schema",
        default="public",
        help="Schema containing the function. Defaults to public.",
    )
    parser.add_argument(
        "--function",
        default="rls_auto_enable",
        help="Function name to inspect. Defaults to rls_auto_enable.",
    )
    parser.add_argument(
        "--args",
        default="",
        help=(
            "Function identity arguments for overloaded functions, for example "
            '"arg_name text". Leave empty for zero-argument functions.'
        ),
    )
    parser.add_argument(
        "--database-url",
        default="",
        help="Explicit Postgres connection string. Overrides --database-url-env.",
    )
    parser.add_argument(
        "--database-url-env",
        default="NEUROCORE_PRODUCTION_DATABASE_URL",
        help=(
            "Environment variable containing the Postgres connection string. "
            "Defaults to NEUROCORE_PRODUCTION_DATABASE_URL."
        ),
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Optional env file to load before resolving the database URL.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the revoke instead of printing the SQL only.",
    )
    return parser


def _runtime_env(
    environ: Mapping[str, str] | None = None,
    *,
    env_file: str = "",
) -> dict[str, str]:
    values = dict(os.environ if environ is None else environ)
    if env_file:
        values.update(load_env_file(Path(env_file), base_env=values))
    return values


def _resolve_database_url(args: argparse.Namespace, env: Mapping[str, str]) -> str:
    if args.database_url.strip():
        return args.database_url.strip()
    database_url = env.get(args.database_url_env, "").strip()
    if database_url:
        return database_url
    raise ScriptError(
        f"Missing database URL. Set {args.database_url_env} or pass --database-url."
    )


def _connect(database_url: str):
    import psycopg

    return psycopg.connect(database_url)


def fetch_function_states(
    connection: Any,
    *,
    schema_name: str,
    function_name: str,
) -> list[FunctionSecurityState]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            select
                n.nspname,
                p.proname,
                pg_get_function_identity_arguments(p.oid) as identity_args,
                p.prosecdef,
                has_function_privilege('anon', p.oid, 'EXECUTE') as anon_execute,
                has_function_privilege('authenticated', p.oid, 'EXECUTE')
                    as authenticated_execute
            from pg_proc p
            join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = %s
              and p.proname = %s
            order by pg_get_function_identity_arguments(p.oid)
            """,
            (schema_name, function_name),
        )
        return [
            FunctionSecurityState(
                schema_name=row[0],
                function_name=row[1],
                identity_args=row[2],
                is_security_definer=row[3],
                anon_execute=row[4],
                authenticated_execute=row[5],
            )
            for row in cursor.fetchall()
        ]


def _select_target(
    states: list[FunctionSecurityState],
    *,
    schema_name: str,
    function_name: str,
    requested_args: str,
) -> FunctionSecurityState:
    if not states:
        raise ScriptError(
            f"Function {schema_name}.{function_name} was not found in the target database."
        )
    if requested_args:
        for state in states:
            if state.identity_args == requested_args:
                return state
        available = ", ".join(state.identity_args or "()" for state in states)
        raise ScriptError(
            f"Function overload {schema_name}.{function_name}({requested_args}) "
            f"was not found. Available signatures: {available}."
        )
    if len(states) > 1:
        available = ", ".join(state.identity_args or "()" for state in states)
        raise ScriptError(
            f"Multiple overloads found for {schema_name}.{function_name}. "
            f"Rerun with --args. Available signatures: {available}."
        )
    return states[0]


def build_revoke_sql(state: FunctionSecurityState) -> str:
    return (
        f"revoke execute on function {state.signature} "
        "from public, anon, authenticated;"
    )


def apply_revoke_execute(
    connection: Any,
    state: FunctionSecurityState,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(build_revoke_sql(state))


def _print_state(state: FunctionSecurityState, stdout: TextIO) -> None:
    print(
        (
            f"Function {state.schema_name}.{state.function_name}"
            f"({state.identity_args})"
        ),
        file=stdout,
    )
    print(
        f"- SECURITY DEFINER: {'yes' if state.is_security_definer else 'no'}",
        file=stdout,
    )
    print(
        f"- anon can execute: {'yes' if state.anon_execute else 'no'}",
        file=stdout,
    )
    print(
        (
            "- authenticated can execute: "
            f"{'yes' if state.authenticated_execute else 'no'}"
        ),
        file=stdout,
    )


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    environ: Mapping[str, str] | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    try:
        env = _runtime_env(environ, env_file=args.env_file)
        database_url = _resolve_database_url(args, env)

        with _connect(database_url) as connection:
            states = fetch_function_states(
                connection,
                schema_name=args.schema,
                function_name=args.function,
            )
            target = _select_target(
                states,
                schema_name=args.schema,
                function_name=args.function,
                requested_args=args.args.strip(),
            )
            _print_state(target, stdout)

            if not target.is_security_definer:
                print(
                    "No change needed: the function is already SECURITY INVOKER.",
                    file=stdout,
                )
                return 0

            if not target.publicly_callable:
                print(
                    "No change needed: anon/authenticated can no longer execute it.",
                    file=stdout,
                )
                return 0

            revoke_sql = build_revoke_sql(target)
            print("", file=stdout)
            print("Planned fix:", file=stdout)
            print(revoke_sql, file=stdout)

            if not args.apply:
                print("", file=stdout)
                print(
                    "Dry run only. Rerun with --apply to execute the revoke.",
                    file=stdout,
                )
                return 0

            apply_revoke_execute(connection, target)
            connection.commit()

            updated = _select_target(
                fetch_function_states(
                    connection,
                    schema_name=args.schema,
                    function_name=args.function,
                ),
                schema_name=args.schema,
                function_name=args.function,
                requested_args=target.identity_args,
            )
            if updated.publicly_callable:
                raise ScriptError(
                    "Verification failed: anon/authenticated still retain EXECUTE."
                )

            print("", file=stdout)
            print("Fix applied and verified.", file=stdout)
            if updated.schema_name == "public":
                print(
                    "Follow-up: move the function to a private schema if it is only "
                    "used internally by policies or admin flows.",
                    file=stdout,
                )
            return 0
    except (ScriptError, Exception) as exc:
        print(str(exc), file=stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
