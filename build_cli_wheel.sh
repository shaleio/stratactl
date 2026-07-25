#!/usr/bin/env bash
# Build the StrataCTL wheel (strata_cli-<ver>-py3-none-any.whl).
#
# The StrataCI runner installs this inside CodeBuild to run `python -m strata.runner`.
# Upload it to your strataci bucket and pass its URI as
#   -var strata_pkg=s3://<strataci-bucket>/cli/<wheel>
# when applying strataci-runner/infra (the runner's S3 policy is scoped to that bucket).
set -euo pipefail

cd "$(dirname "$0")/stratactl"   # the package dir (pyproject.toml lives here)

python3 -m pip install --quiet --upgrade build >/dev/null
rm -rf dist build ./*.egg-info strata_cli.egg-info
python3 -m build --wheel

WHEEL="$(ls dist/*.whl)"
echo
echo "✅ built: $WHEEL"
echo
echo "next:"
echo "  aws s3 cp $WHEEL s3://<strataci-bucket>/cli/$(basename "$WHEEL")"
echo "  terraform apply ... -var strata_pkg=s3://<strataci-bucket>/cli/$(basename "$WHEEL")"
