from __future__ import annotations

import argparse


# ---------------------------------------------------------------------------
# Stub handlers
# ---------------------------------------------------------------------------

def _handle_serve(args: argparse.Namespace) -> None:
    print(f"Starting server on {args.host}:{args.port} ... (not yet implemented)")


def _handle_check(_args: argparse.Namespace) -> None:
    print("Preflight check not yet implemented")


def _handle_init(args: argparse.Namespace) -> None:
    print(f"Initializing skeleton in '{args.dir}' ... (not yet implemented)")


def _handle_user_create(_args: argparse.Namespace) -> None:
    print("Not yet implemented")


def _handle_user_list(_args: argparse.Namespace) -> None:
    print("Not yet implemented")


def _handle_user_info(_args: argparse.Namespace) -> None:
    print("Not yet implemented")


def _handle_user_config(_args: argparse.Namespace) -> None:
    print("Not yet implemented")


def _handle_user_disable(_args: argparse.Namespace) -> None:
    print("Not yet implemented")


def _handle_user_enable(_args: argparse.Namespace) -> None:
    print("Not yet implemented")


def _handle_user_reset_key(_args: argparse.Namespace) -> None:
    print("Not yet implemented")


def _handle_usage(_args: argparse.Namespace) -> None:
    print("Not yet implemented")


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentpod", description="AgentPod CLI")
    sub = parser.add_subparsers(dest="command")

    # serve
    p_serve = sub.add_parser("serve", help="Start the gateway server")
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")

    # check
    sub.add_parser("check", help="Run preflight checks")

    # init
    p_init = sub.add_parser("init", help="Initialize a project skeleton")
    p_init.add_argument("dir", help="Target directory")

    # user (subcommand group)
    p_user = sub.add_parser("user", help="User management commands")
    user_sub = p_user.add_subparsers(dest="user_command")
    user_sub.add_parser("create", help="Create a new user")
    user_sub.add_parser("list", help="List users")
    user_sub.add_parser("info", help="Show user info")
    user_sub.add_parser("config", help="Configure a user")
    user_sub.add_parser("disable", help="Disable a user")
    user_sub.add_parser("enable", help="Enable a user")
    user_sub.add_parser("reset-key", help="Reset a user's API key")

    # usage
    p_usage = sub.add_parser("usage", help="Show usage statistics")
    p_usage.add_argument("user_id", help="User ID to query")
    p_usage.add_argument("--all", action="store_true", help="Show all usage records")
    p_usage.add_argument("--month", help="Filter by month (YYYY-MM)")
    p_usage.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD)")
    p_usage.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD)")

    return parser


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_USER_DISPATCH: dict[str, callable] = {
    "create": _handle_user_create,
    "list": _handle_user_list,
    "info": _handle_user_info,
    "config": _handle_user_config,
    "disable": _handle_user_disable,
    "enable": _handle_user_enable,
    "reset-key": _handle_user_reset_key,
}

_COMMAND_DISPATCH: dict[str, callable] = {
    "serve": _handle_serve,
    "check": _handle_check,
    "init": _handle_init,
    "usage": _handle_usage,
}


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "user":
        handler = _USER_DISPATCH.get(args.user_command)
        if handler is None:
            parser.parse_args(["user", "--help"])
        else:
            handler(args)
        return

    handler = _COMMAND_DISPATCH.get(args.command)
    if handler is None:
        parser.print_help()
    else:
        handler(args)
