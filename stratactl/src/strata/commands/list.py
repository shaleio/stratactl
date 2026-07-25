from strata.aws import session as aws_session
from strata.registry import installs


def run(settings, args) -> int:
    sess = aws_session.make_session(settings)
    items = installs.list_all(sess, settings.customer.install_table)
    if not items:
        print("no modules installed.")
        return 0

    rows = sorted(items, key=lambda i: i.get("module_id", ""))
    width = max(len(r.get("module_id", "")) for r in rows)
    print(f"{'MODULE'.ljust(width)}  {'VERSION':10}  {'STATUS':10}  LAST JOB")
    for r in rows:
        print(f"{r.get('module_id', '').ljust(width)}  "
              f"{str(r.get('version', '-')):10}  "
              f"{str(r.get('status', '-')):10}  "
              f"{r.get('last_job', '-')}")
    return 0
