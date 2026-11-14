"""`stratactl env` — the account's environment config (network + identity).

    stratactl env show     [--env dev]
    stratactl env set      KEY=VALUE [KEY=VALUE ...] [--env dev] [--yes]
    stratactl env import   [--env dev] [--module stratabi] [--yes]
    stratactl env validate [--env dev]
    stratactl env export   --out env.auto.tfvars.json [--env dev]

Stored at s3://<strataci-bucket>/config/env/<env>.auto.tfvars.json (versioned bucket).
"""

from __future__ import annotations

import json

from strata.aws import session as aws_session
from strata.errors import StrataError
from strata.install import envstore


def add_parser(sub) -> None:
    ep = sub.add_parser("env", help="Show/set the environment config (network + identity) every module uses.")
    esub = ep.add_subparsers(dest="env_command", required=True)

    def common(p):
        p.add_argument("--env", default="dev", help="Environment label (default: dev).")
        return p

    common(esub.add_parser("show", help="Print the stored environment config."))
    sp = common(esub.add_parser("set", help="Set environment keys (KEY=VALUE; lists comma-separated)."))
    sp.add_argument("assignments", nargs="+", metavar="KEY=VALUE")
    sp.add_argument("--yes", action="store_true", help="Don't ask before writing.")
    ip = common(esub.add_parser("import", help="Read the environment from a deployed module's tofu state."))
    ip.add_argument("--module", default="stratabi", help="Installed module to read (default: stratabi).")
    ip.add_argument("--yes", action="store_true", help="Don't ask before writing.")
    common(esub.add_parser("validate", help="Check the stored network values against the account."))
    xp = common(esub.add_parser("export", help="Write the environment as a tfvars JSON file (BYO CI/CD)."))
    xp.add_argument("--out", required=True, help="Output path, e.g. env.auto.tfvars.json.")


def _ctx(settings):
    sess = aws_session.make_session(settings)
    account = aws_session.account_id(sess)
    return sess, account, settings.resolved_bucket(account)


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _print_cfg(cfg: dict) -> None:
    width = max((len(k) for k in envstore.ENV_KEYS), default=10)
    for k in envstore.ENV_KEYS:
        if k in cfg:
            print(f"  {k:<{width}}  {json.dumps(cfg[k])}")


def _validate(sess, cfg: dict) -> bool:
    try:
        problems = envstore.validate_network(sess.client("ec2"), cfg)
    except PermissionError:
        print("warning: no ec2:Describe* permission — network not validated.")
        return True
    for p in problems:
        print(f"  ✗ {p}")
    if not problems:
        print("  ✓ network checks out against this account")
    return not problems


def run(settings, args) -> int:
    sess, account, bucket = _ctx(settings)
    s3 = sess.client("s3")
    env = args.env
    where = f"s3://{bucket}/{envstore.key(env)}"
    current = envstore.load(s3, bucket, env)

    if args.env_command == "show":
        if current is None:
            print(f"no environment config at {where}")
            print(f"create one: stratactl env import --env {env}   (from the deployed platform)")
            print(f"        or: stratactl env set --env {env} vpc_id=... public_subnet_ids=a,b ...")
            return 1
        print(f"environment '{env}'  ({where}, account {account})")
        _print_cfg(current)
        return 0

    if args.env_command == "validate":
        if current is None:
            raise StrataError(f"no environment config at {where}")
        print(f"validating environment '{env}' ({where})")
        return 0 if _validate(sess, current) else 1

    if args.env_command == "export":
        if current is None:
            raise StrataError(f"no environment config at {where}")
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        print(f"wrote {args.out} from {where}")
        return 0

    if args.env_command == "set":
        try:
            updates = dict(envstore.parse_assignment(a) for a in args.assignments)
        except ValueError as exc:
            raise StrataError(str(exc)) from exc
        new = {**(current or {}), **updates}
    elif args.env_command == "import":
        from strata.registry import installs

        rec = installs.get(sess, settings.customer.install_table, args.module)
        if not rec or rec.get("status") == installs.REMOVED or not rec.get("state_key"):
            raise StrataError(f"{args.module} is not installed in this account (no state to read).")
        try:
            raw = s3.get_object(Bucket=bucket, Key=rec["state_key"])["Body"].read()
        except Exception as exc:  # noqa: BLE001
            raise StrataError(f"could not read state s3://{bucket}/{rec['state_key']}: {exc}") from exc
        new = envstore.from_state(json.loads(raw), settings.region)
        if not new.get("vpc_id"):
            raise StrataError(f"no VPC found in {args.module}'s state — use `stratactl env set` instead.")
        print(f"read from deployed {args.module} (s3://{bucket}/{rec['state_key']})")
    else:
        raise StrataError(f"unknown env command {args.env_command}")

    changes = envstore.diff(current or {}, new)
    if current is not None and not changes:
        print(f"environment '{env}' already matches — nothing to write.")
        _print_cfg(current)
        return 0
    print(f"environment '{env}' ({where}) will be:")
    _print_cfg(new)
    if current is not None:
        print("changes:\n" + envstore.format_diff(changes))
    if not _validate(sess, new):
        raise StrataError("not writing: the network values don't check out (see above).")
    if not _confirm("Write this environment config?", getattr(args, "yes", False)):
        print("aborted — nothing written.")
        return 1
    envstore.save(s3, bucket, env, new)
    print(f"wrote {where}")
    return 0


# ---------------------------------------------------------------------------
# plan-guard (BYO CI/CD)
# ---------------------------------------------------------------------------
def add_planguard_parser(sub) -> None:
    pg = sub.add_parser("plan-guard",
                        help="Fail if a tofu/terraform plan destroys or replaces protected infrastructure.")
    pg.add_argument("plan_json", help="Output of `tofu show -json plan.bin` (or terraform show -json).")
    pg.add_argument("--allow-replace", action="store_true", help="Report, but don't fail.")


def run_planguard(settings, args) -> int:
    from strata.runner import planguard

    try:
        hits = planguard.check_file(args.plan_json, allow_replace=args.allow_replace)
    except RuntimeError as exc:
        raise StrataError(str(exc)) from exc
    if hits:
        print("plan guard: --allow-replace given; the plan destroys/replaces:\n" + planguard.format_report(hits))
    else:
        print("plan guard: OK — no protected resources destroyed or replaced")
    return 0
