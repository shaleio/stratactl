"""`strata init` — interactive installation & initialization wizard.

Collects the configuration a StrataBI deployment (or an official module) needs,
persists it to the YAML installation config (the source of truth), and generates
the Terraform/OpenTofu variable files. Re-running reuses saved values and prompts
only for what is missing; `--update` re-asks everything.

  strata init                      initialize / refresh the StrataBI deployment
  strata init module <id>          initialize a module's variables
  strata init --show-config        print the resolved StrataCTL settings (legacy view)

The wizard itself lives in strata.install (schema / prompt / store / tfvars /
modules); this command only orchestrates them, so `deploy`, `upgrade`, and
`module install/update` can reuse the same pieces later.
"""

from __future__ import annotations

from pathlib import Path

from strata.errors import ConfigError
from strata.install import modules as mod_discovery
from strata.install import store, tfvars
from strata.install.prompt import Wizard
from strata.install.schema import stratabi_schema


def _legacy_show(settings) -> int:
    print("Resolved StrataCTL configuration")
    print("================================")
    print(f"  source            : {settings.source}")
    print(f"  region            : {settings.region}")
    print(f"  profile           : {settings.profile or '(default credentials)'}")
    print(f"  HQ endpoint       : {settings.hq.endpoint}")
    print(f"  HQ artifact bucket: {settings.hq.artifact_bucket}")
    print(f"  HQ token set      : {'yes' if settings.hq.token else 'no'}")
    return 0


def _out_dir(args) -> Path:
    if getattr(args, "out", None):
        return Path(args.out).expanduser()
    # Default to ./infra when it looks like a Terraform module lives there.
    infra = Path.cwd() / "infra"
    return infra if infra.is_dir() else Path.cwd()


def _interactive(args) -> bool:
    return not getattr(args, "non_interactive", False)


def _public_ip() -> str | None:
    """Best-effort: the caller's public IP, for a sensible default inbound rule (E4)."""
    try:
        import urllib.request
        with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=5) as r:
            ip = r.read().decode().strip()
        return ip or None
    except Exception:
        return None


def _confirm(msg: str) -> bool:
    try:
        return input(f"{msg} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _pick_subnets(subs: list) -> list:
    """Interactive subnet chooser: a numbered list with 2 public subnets in distinct AZs
    pre-suggested; accept Enter (use the suggestion), comma-separated numbers (click), or
    paste your own subnet-ids."""
    rows = sorted(subs, key=lambda s: (not s.get("MapPublicIpOnLaunch"), s.get("AvailabilityZone", "")))
    ids = [s["SubnetId"] for s in rows]
    suggestion, seen = [], set()
    for s in rows:
        if s.get("MapPublicIpOnLaunch") and s["AvailabilityZone"] not in seen:
            suggestion.append(s["SubnetId"])
            seen.add(s["AvailabilityZone"])
        if len(suggestion) == 2:
            break
    print("  Subnets in this VPC (* = suggested for the ALB):")
    for i, s in enumerate(rows, 1):
        kind = "public" if s.get("MapPublicIpOnLaunch") else "private"
        star = " *" if s["SubnetId"] in suggestion else ""
        print(f"    {i}) {s['SubnetId']}  {s['AvailabilityZone']}  {kind}{star}")
    default_nums = ",".join(str(ids.index(x) + 1) for x in suggestion) if suggestion else ""
    try:
        raw = input(f"  ALB subnets — numbers e.g. 1,2 (Enter = suggested), or paste subnet-ids [{default_nums}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        raw = ""
    if not raw:
        return suggestion
    if "subnet-" in raw:
        return [t for t in raw.replace(",", " ").split() if t.startswith("subnet-")]
    out = []
    for tok in raw.replace(",", " ").split():
        if tok.isdigit() and 1 <= int(tok) <= len(ids):
            out.append(ids[int(tok) - 1])
    return out or suggestion


def _preflight_network(settings, config) -> None:
    """Discover VPCs/subnets + the caller's public IP and pre-seed the network defaults so
    the wizard offers real choices, and offer to create a default VPC if the account has
    none (E1/E4). Best-effort — never blocks init on a discovery failure."""
    from strata.install import store
    try:
        from strata.aws import session as aws_session
        ec2 = aws_session.make_session(settings).client("ec2")
    except Exception as exc:  # noqa: BLE001
        print(f"  (skipping network discovery: {exc})")
        return

    print("Network preflight…")

    # E4: default inbound to the caller's /32 instead of the world.
    if store.get(config, "network", "allowed_ip_cidrs", None) in (None, [], ""):
        ip = _public_ip()
        if ip:
            store.set_(config, "network", "allowed_ip_cidrs", [f"{ip}/32"])
            print(f"  inbound default set to your IP {ip}/32 (widen it in the wizard if needed)")

    # E1: VPC + subnet discovery.
    try:
        vpcs = ec2.describe_vpcs().get("Vpcs", [])
    except Exception as exc:  # noqa: BLE001
        print(f"  (could not list VPCs: {exc})")
        return
    if not vpcs:
        print("  No VPC in this account/region — StrataBI needs a VPC with >=2 public subnets in different AZs.")
        if _confirm("  Create a default VPC now?"):
            try:
                ec2.create_default_vpc()
                vpcs = ec2.describe_vpcs().get("Vpcs", [])
                print("  created a default VPC.")
            except Exception as exc:  # noqa: BLE001
                print(f"  create-default-vpc failed: {exc}. Create one manually, then re-run init.")
                return
        else:
            return

    vpc = next((v for v in vpcs if v.get("IsDefault")), vpcs[0])
    vpc_id = vpc["VpcId"]
    subs = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get("Subnets", [])
    public = [s["SubnetId"] for s in subs if s.get("MapPublicIpOnLaunch")]
    azs = {s["AvailabilityZone"] for s in subs}
    label = "default" if vpc.get("IsDefault") else "first"
    print(f"  VPC {vpc_id} ({label}) — {len(subs)} subnet(s) across {len(azs)} AZ(s)")

    if store.get(config, "network", "vpc_id", None) is None:
        store.set_(config, "network", "vpc_id", vpc_id)

    # Subnet picker: pick by number (click) or paste your own ids (E1 UX).
    if store.get(config, "network", "public_subnet_ids", None) in (None, [], ""):
        chosen = _pick_subnets(subs)
        if len(chosen) >= 2:
            store.set_(config, "network", "public_subnet_ids", chosen)
            print(f"  ALB subnets: {', '.join(chosen)}")
        else:
            print("  ! need >=2 subnets in different AZs for the ALB — the wizard will ask.")


def run(settings, args) -> int:
    if getattr(args, "show_config", False):
        return _legacy_show(settings)

    what = getattr(args, "what", "stratabi") or "stratabi"
    if what == "module":
        return _run_module(settings, args)
    return _run_stratabi(settings, args)


# --------------------------------------------------------------------------- #
# StrataBI deployment
# --------------------------------------------------------------------------- #
def _run_stratabi(settings, args) -> int:
    schema = stratabi_schema()
    path = store.config_path(getattr(args, "config", None))
    config = store.load(path)
    existed = store.exists(path)

    # Seed sensible defaults from resolved CLI settings / flags on first run.
    if not existed:
        store.set_(config, "installation", "aws_region", settings.region)
    if getattr(args, "name", None):
        store.set_(config, "installation", "name", args.name)

    if _interactive(args):
        print(f"StrataBI installation wizard  ({'updating' if existed else 'new'}: {path.name})")
        print("Answer the prompts; press Enter to accept the [default]. Ctrl-C to abort.")
        # Enterprise deploys an ALB/Fargate stack into a VPC — discover networking and
        # a sensible inbound default before prompting (E1/E4). Dev edition needs none.
        edition = store.get(config, "installation", "edition", "enterprise")
        if edition == "enterprise":
            _preflight_network(settings, config)

    wizard = Wizard(schema, config, interactive=_interactive(args),
                    update=getattr(args, "update", False))
    # E3: the wizard mutates `config` in place as each field is answered, so on abort we
    # still have everything answered so far — persist it so re-running resumes.
    try:
        config = wizard.run()
    except KeyboardInterrupt:
        store.save(path, config)
        print(f"\nAborted — answers so far saved to {path}. Re-run `stratactl init` to continue.")
        return 130

    store.save(path, config)
    out = _out_dir(args)
    tf = tfvars.write(schema, config, out)

    _summary(path, [tf])
    print("\nNext:")
    edition = store.get(config, "installation", "edition", "enterprise")
    cmd = "install-stratabi" if edition == "enterprise" else "install-stratabi-dev"
    print(f"  review {tf}")
    print(f"  stratactl {cmd}        # deploy with the generated variables")
    return 0


# --------------------------------------------------------------------------- #
# Module
# --------------------------------------------------------------------------- #
def _run_module(settings, args) -> int:
    module_id = getattr(args, "module_id", None)
    if not module_id:
        raise ConfigError("usage: strata init module <module_id>")

    schema = mod_discovery.discover(
        module_id,
        explicit=getattr(args, "schema", None),
        search_dirs=[Path.cwd(), _out_dir(args)],
    )
    if schema is None:
        print(f"Module '{module_id}' declares no installation variables — nothing to collect.")
        print("(A module ships an install.schema.json or an install_schema block in module.json.)")
        return 0

    path = store.config_path(getattr(args, "config", None))
    config = store.load(path)

    if _interactive(args):
        print(f"Module '{module_id}' installation wizard  ({path.name})")

    wizard = Wizard(schema, config, interactive=_interactive(args),
                    update=getattr(args, "update", False))
    config = wizard.run()

    store.save(path, config)

    out = _out_dir(args)
    body = tfvars.render_module(schema, config, module_id)
    written: list[Path] = []
    if body.strip():
        tf = out / f"{module_id}.auto.tfvars"
        out.mkdir(parents=True, exist_ok=True)
        tf.write_text(body, encoding="utf-8")
        written.append(tf)

    _summary(path, written)
    print("\nNext:")
    print(f"  stratactl install {module_id}        # install the module")
    return 0


def _summary(config_path: Path, generated: list[Path]) -> None:
    print("\nSaved:")
    print(f"  config : {config_path}")
    for g in generated:
        print(f"  tfvars : {g}")
