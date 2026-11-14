"""Environment config (config/env/<env>.auto.tfvars.json), update guard, and plan guard."""

import io
import json

import pytest

from strata.errors import StrataError
from strata.install import envstore
from strata.runner import planguard


# --------------------------------------------------------------------------- fakes
class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            err = Exception("NoSuchKey")
            err.response = {"Error": {"Code": "NoSuchKey"}}
            raise err
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[(Bucket, Key)] = Body


class FakeEC2:
    VPCS = {"vpc-good"}
    SUBNETS = {
        "subnet-a": {"SubnetId": "subnet-a", "VpcId": "vpc-good", "AvailabilityZone": "us-east-1a"},
        "subnet-b": {"SubnetId": "subnet-b", "VpcId": "vpc-good", "AvailabilityZone": "us-east-1b"},
        "subnet-x": {"SubnetId": "subnet-x", "VpcId": "vpc-other", "AvailabilityZone": "us-east-1c"},
    }

    def describe_vpcs(self, Filters):
        return {"Vpcs": [{"VpcId": v} for v in Filters[0]["Values"] if v in self.VPCS]}

    def describe_subnets(self, Filters):
        return {"Subnets": [self.SUBNETS[s] for s in Filters[0]["Values"] if s in self.SUBNETS]}


class FakeSession:
    def __init__(self, s3):
        self._s3 = s3

    def client(self, name):
        return self._s3 if name == "s3" else FakeEC2()


GOOD_ENV = {"name_prefix": "stratabi", "region": "us-east-1", "vpc_id": "vpc-good",
            "public_subnet_ids": ["subnet-a", "subnet-b"], "enable_private_subnets": False,
            "private_subnet_ids": []}
BAD_RENDER = {**GOOD_ENV, "vpc_id": "vpc-elsewhere", "public_subnet_ids": ["subnet-zz1", "subnet-zz2"],
              "enable_ai": True}


# --------------------------------------------------------------------------- envstore
def test_split_parse_diff():
    env, mod = envstore.split({**GOOD_ENV, "enable_ai": True})
    assert set(env) == set(envstore.ENV_KEYS) and mod == {"enable_ai": True}
    assert envstore.parse_assignment("public_subnet_ids=subnet-a, subnet-b") == (
        "public_subnet_ids", ["subnet-a", "subnet-b"])
    assert envstore.parse_assignment("enable_private_subnets=true") == ("enable_private_subnets", True)
    with pytest.raises(ValueError):
        envstore.parse_assignment("enable_ai=true")  # module key, not environment
    changes = envstore.diff(GOOD_ENV, {"vpc_id": "vpc-new"})
    assert changes == [("vpc_id", "vpc-good", "vpc-new")]


def test_validate_network():
    assert envstore.validate_network(FakeEC2(), GOOD_ENV) == []
    problems = envstore.validate_network(FakeEC2(), {"vpc_id": "vpc-nope", "public_subnet_ids": ["subnet-a", "subnet-x", "subnet-q"]})
    text = "\n".join(problems)
    assert "vpc-nope does not exist" in text
    assert "subnet-q does not exist" in text
    assert "subnet-x is in vpc-other" in text


def test_from_state():
    state = {"resources": [
        {"mode": "managed", "type": "aws_security_group", "name": "alb",
         "instances": [{"attributes": {"vpc_id": "vpc-good"}}]},
        {"mode": "data", "type": "aws_subnet", "name": "public",
         "instances": [{"index_key": 1, "attributes": {"id": "subnet-b"}},
                       {"index_key": 0, "attributes": {"id": "subnet-a"}}]},
        {"mode": "managed", "type": "aws_ecs_cluster", "name": "stratabi",
         "instances": [{"attributes": {"name": "stratabi-cluster"}}]},
    ]}
    env = envstore.from_state(state, "us-east-1")
    assert env == {"region": "us-east-1", "vpc_id": "vpc-good", "public_subnet_ids": ["subnet-a", "subnet-b"],
                   "private_subnet_ids": [], "enable_private_subnets": False, "name_prefix": "stratabi"}


# --------------------------------------------------------------------------- plan guard
def _plan(*changes):
    return {"resource_changes": [
        {"address": a, "type": t, "mode": "managed", "change": {"actions": acts}} for a, t, acts in changes]}


def test_planguard():
    benign = _plan(("null_resource.image_build[0]", "null_resource", ["delete", "create"]),
                   ("aws_ecs_task_definition.app", "aws_ecs_task_definition", ["delete", "create"]),
                   ("aws_ecs_service.app", "aws_ecs_service", ["update"]))
    assert planguard.check(benign) == []
    bad = _plan(("aws_security_group.alb", "aws_security_group", ["delete", "create"]),
                ("aws_dynamodb_table.t", "aws_dynamodb_table", ["delete"]))
    with pytest.raises(RuntimeError) as ei:
        planguard.check(bad)
    assert "replace aws_security_group.alb" in str(ei.value)
    assert "destroy aws_dynamodb_table.t" in str(ei.value)
    assert len(planguard.check(bad, allow_replace=True)) == 2


# --------------------------------------------------------------------------- install/update flow
@pytest.fixture
def flow(monkeypatch):
    from strata.commands import _common

    s3 = FakeS3()
    rendered = {}
    monkeypatch.setattr(_common, "_resolve_tfvars", lambda module_id: dict(rendered))
    monkeypatch.setattr("builtins.input", lambda *_: "y")

    def run(action, reconfigure=False, module_id="stratabi"):
        _common._prepare_tfvars(FakeSession(s3), "bkt", action=action, module_id=module_id,
                                env_name="dev", reconfigure=reconfigure, assume_yes=False)

    def stored(key):
        raw = s3.objects.get(("bkt", key))
        return json.loads(raw) if raw is not None else None

    return run, rendered, s3, stored


def test_install_seeds_env_and_module(flow):
    run, rendered, _, stored = flow
    rendered.update({**GOOD_ENV, "enable_ai": True})
    run("install")
    assert stored(envstore.key("dev")) == GOOD_ENV
    assert stored(envstore.module_key("stratabi")) == {"enable_ai": True}


def test_update_ignores_strata_yaml(flow):
    run, rendered, _, stored = flow
    rendered.update({**GOOD_ENV, "enable_ai": True})
    run("install")
    rendered.clear(); rendered.update(BAD_RENDER)  # wrong-directory strata.yaml
    run("update")
    assert stored(envstore.key("dev")) == GOOD_ENV
    assert stored(envstore.module_key("stratabi")) == {"enable_ai": True}


def test_install_refuses_network_change_without_reconfigure(flow):
    run, rendered, _, stored = flow
    rendered.update(GOOD_ENV)
    run("install")
    rendered.update(BAD_RENDER)
    with pytest.raises(StrataError, match="refusing to change"):
        run("install", module_id="stratachat")
    assert stored(envstore.key("dev")) == GOOD_ENV


def test_reconfigure_runs_preflight(flow):
    run, rendered, _, stored = flow
    rendered.update(GOOD_ENV)
    run("install")
    rendered.update(BAD_RENDER)
    with pytest.raises(StrataError, match="network preflight failed"):
        run("update", reconfigure=True)
    assert stored(envstore.key("dev")) == GOOD_ENV  # nothing written on a failed preflight


def test_legacy_update_requires_env(flow):
    run, _, s3, _ = flow
    envstore.save_module(s3, "bkt", "stratabi", {**BAD_RENDER})  # pre-1.7.8 install: no env file
    with pytest.raises(StrataError, match="env import"):
        run("update")
