from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
from datetime import date, datetime
from pathlib import Path

from agentpod.config import load_provider_configs, load_server_config
from agentpod.db import Database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_config() -> "agentpod.config.ServerConfig":  # noqa: F821
    return load_server_config()


def _get_db(cfg=None):
    if cfg is None:
        cfg = _get_config()
    db_path = os.path.join(cfg.data_dir, "registry.db")
    return Database(db_path)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_serve(args: argparse.Namespace) -> None:
    import uvicorn

    cfg = _get_config()
    host = args.host or cfg.host
    port = args.port or cfg.port
    print(f"Starting AgentPod server on {host}:{port}")
    uvicorn.run("agentpod.gateway.app:app", host=host, port=port, log_level=cfg.log_level)


def _handle_check(_args: argparse.Namespace) -> None:
    cfg = _get_config()
    data_dir = Path(cfg.data_dir)
    ok = True

    # 1. Create data dir + users/
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "users").mkdir(exist_ok=True)
    print(f"  data dir {data_dir} ready")

    # 2. Init registry.db
    db = _get_db(cfg)
    try:
        db.init_db()
        print(f"  registry.db initialized")
    finally:
        db.close()

    # 3. Check provider API keys
    providers = load_provider_configs()
    if providers:
        for name in providers:
            print(f"  provider {name} configured")
    else:
        print("  WARNING: no LLM provider API keys configured")
        ok = False

    # 4. Port availability
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((cfg.host, cfg.port))
        print(f"  port {cfg.port} available")
    except OSError:
        print(f"  WARNING: port {cfg.port} is in use")
        ok = False

    # 5. Template check
    template_dir = data_dir / "template"
    if template_dir.is_dir() and (template_dir / "AGENTS.md").is_file():
        print(f"  template/ valid")
    else:
        print(f"  WARNING: template/ missing or no AGENTS.md (user create will fail)")

    if ok:
        print("Preflight check passed.")
    else:
        print("Preflight check completed with warnings.")


def _handle_init(args: argparse.Namespace) -> None:
    target = Path(args.dir)
    target.mkdir(parents=True, exist_ok=True)
    # AGENTS.md
    agents_md = target / "AGENTS.md"
    if not agents_md.exists():
        agents_md.write_text("# Agent Definition\n\nDescribe your agent here.\n", encoding="utf-8")
    # .agents/skills/
    (target / ".agents" / "skills").mkdir(parents=True, exist_ok=True)
    # sessions/
    (target / "sessions").mkdir(exist_ok=True)
    # version
    version_file = target / "version"
    if not version_file.exists():
        version_file.write_text("1.0.0\n", encoding="utf-8")
    print(f"Initialized CWD skeleton in {target}")


def _handle_user_create(args: argparse.Namespace) -> None:
    cfg = _get_config()
    data_dir = Path(cfg.data_dir)
    template_dir = data_dir / "template"

    if not template_dir.is_dir() or not (template_dir / "AGENTS.md").is_file():
        print("ERROR: template/ directory missing or has no AGENTS.md. Run 'agentpod check' first.", file=sys.stderr)
        sys.exit(1)

    user_id = args.user_id
    user_dir = data_dir / "users" / user_id

    if user_dir.exists():
        print(f"ERROR: user directory already exists: {user_dir}", file=sys.stderr)
        sys.exit(1)

    # 1. Copy template/ -> users/{id}/
    shutil.copytree(str(template_dir), str(user_dir))
    # 2. Create sessions/
    (user_dir / "sessions").mkdir(exist_ok=True)

    # 3-4. Generate API key + write to registry
    cwd_path = str(user_dir.resolve())
    db = _get_db(cfg)
    try:
        db.init_db()
        api_key = db.create_user(user_id, cwd_path)
    finally:
        db.close()

    # 5. Output
    print(f"User created: {user_id}")
    print(f"  CWD: {cwd_path}")
    print(f"  API Key: {api_key}")
    print("  Save this API key -- it will not be shown again.")


def _handle_user_list(_args: argparse.Namespace) -> None:
    cfg = _get_config()
    db = _get_db(cfg)
    try:
        db.init_db()
        users = db.list_users()
    finally:
        db.close()
    if not users:
        print("No users found.")
        return
    # Column widths
    id_w = max(len("ID"), max(len(u["id"]) for u in users))
    cwd_w = max(len("CWD"), max(len(u["cwd_path"]) for u in users))
    header = f"  {'ID':<{id_w}}  {'Status':<8}  {'API Key':<12}  {'CWD':<{cwd_w}}  Created"
    print(header)
    for u in users:
        status = "active" if u["is_active"] else "disabled"
        key_prefix = u["api_key"][:7] + "..." if u["api_key"] else "n/a"
        created = u["created_at"][:16].replace("T", " ")
        print(f"  {u['id']:<{id_w}}  {status:<8}  {key_prefix:<12}  {u['cwd_path']:<{cwd_w}}  {created}")


def _handle_user_info(args: argparse.Namespace) -> None:
    cfg = _get_config()
    db = _get_db(cfg)
    try:
        db.init_db()
        user = db.get_user_by_id(args.user_id)
    finally:
        db.close()
    if not user:
        print(f"User not found: {args.user_id}", file=sys.stderr)
        sys.exit(1)
    status = "active" if user["is_active"] else "disabled"
    print(f"User: {user['id']}")
    print(f"  Status:     {status}")
    print(f"  API Key:    {user['api_key']}")
    print(f"  CWD:        {user['cwd_path']}")
    print(f"  Config:     {user['config']}")
    print(f"  Created:    {user['created_at']}")
    print(f"  Updated:    {user['updated_at']}")


def _handle_user_config(args: argparse.Namespace) -> None:
    cfg = _get_config()
    db = _get_db(cfg)
    try:
        db.init_db()
        user = db.get_user_by_id(args.user_id)
        if not user:
            print(f"User not found: {args.user_id}", file=sys.stderr)
            sys.exit(1)
        # Merge mode: load existing config, update with new keys
        existing = json.loads(user["config"])
        incoming = json.loads(args.config_json)
        existing.update(incoming)
        db.update_config(args.user_id, json.dumps(existing, ensure_ascii=False))
    finally:
        db.close()
    print(f"Config updated for {args.user_id}")
    print(f"  {json.dumps(existing, ensure_ascii=False)}")


def _handle_user_disable(args: argparse.Namespace) -> None:
    cfg = _get_config()
    db = _get_db(cfg)
    try:
        db.init_db()
        db.disable_user(args.user_id)
    finally:
        db.close()
    print(f"User {args.user_id} disabled.")


def _handle_user_enable(args: argparse.Namespace) -> None:
    cfg = _get_config()
    db = _get_db(cfg)
    try:
        db.init_db()
        db.enable_user(args.user_id)
    finally:
        db.close()
    print(f"User {args.user_id} enabled.")


def _handle_user_reset_key(args: argparse.Namespace) -> None:
    cfg = _get_config()
    db = _get_db(cfg)
    try:
        db.init_db()
        new_key = db.reset_api_key(args.user_id)
    finally:
        db.close()
    print(f"API key reset for {args.user_id}")
    print(f"  New API Key: {new_key}")
    print("  Save this API key -- it will not be shown again.")


def _handle_usage(args: argparse.Namespace) -> None:
    cfg = _get_config()
    db = _get_db(cfg)
    try:
        db.init_db()
        from_date = args.from_date
        to_date = args.to_date

        if args.month:
            # --month YYYY-MM  ->  from=YYYY-MM-01, to=YYYY-MM+1-01
            from_date = args.month + "-01"
            year, month = map(int, args.month.split("-"))
            if month == 12:
                to_date = f"{year + 1}-01-01"
            else:
                to_date = f"{year}-{month + 1:02d}-01"
        elif not from_date and not to_date and not getattr(args, "all", False):
            # Default: today
            today = date.today().isoformat()
            from_date = today
            to_date = None  # will match today* via prefix

        if getattr(args, "all", False):
            from_date = None
            to_date = None

        rows = db.get_usage(args.user_id, from_date=from_date, to_date=to_date)
    finally:
        db.close()

    if not rows:
        print("No usage records found.")
        return

    total_cost = 0.0
    total_input = 0
    total_output = 0
    for r in rows:
        total_cost += r["cost_amount"]
        total_input += r["input_tokens"]
        total_output += r["output_tokens"]
        print(
            f"  {r['created_at']}  model={r['model']}  turns={r['turns']}  "
            f"in={r['input_tokens']}  out={r['output_tokens']}  "
            f"cost={r['cost_amount']:.4f}"
        )
    print(f"  --- Total: {len(rows)} records, "
          f"input={total_input}, output={total_output}, cost={total_cost:.4f}")


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentpod", description="AgentPod CLI")
    sub = parser.add_subparsers(dest="command")

    # serve
    p_serve = sub.add_parser("serve", help="Start the gateway server")
    p_serve.add_argument("--host", default=None, help="Bind address")
    p_serve.add_argument("--port", type=int, default=None, help="Bind port")

    # check
    sub.add_parser("check", help="Run preflight checks")

    # init
    p_init = sub.add_parser("init", help="Initialize a CWD skeleton")
    p_init.add_argument("dir", help="Target directory")

    # user (subcommand group)
    p_user = sub.add_parser("user", help="User management commands")
    user_sub = p_user.add_subparsers(dest="user_command")

    p_uc = user_sub.add_parser("create", help="Create a new user")
    p_uc.add_argument("user_id", help="User ID")

    user_sub.add_parser("list", help="List users")

    p_ui = user_sub.add_parser("info", help="Show user info")
    p_ui.add_argument("user_id", help="User ID")

    p_ucfg = user_sub.add_parser("config", help="Update user config (merge)")
    p_ucfg.add_argument("user_id", help="User ID")
    p_ucfg.add_argument("config_json", help="JSON config to merge")

    p_ud = user_sub.add_parser("disable", help="Disable a user")
    p_ud.add_argument("user_id", help="User ID")

    p_ue = user_sub.add_parser("enable", help="Enable a user")
    p_ue.add_argument("user_id", help="User ID")

    p_urk = user_sub.add_parser("reset-key", help="Reset API key")
    p_urk.add_argument("user_id", help="User ID")

    # usage
    p_usage = sub.add_parser("usage", help="Show usage statistics")
    p_usage.add_argument("user_id", help="User ID to query")
    p_usage.add_argument("--all", action="store_true", help="Show all records")
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
