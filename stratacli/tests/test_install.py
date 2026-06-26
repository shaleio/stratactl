"""Unit tests for the StrataCLI installation wizard.

Run: python -m unittest discover -s tests  (from the stratacli package root)
These avoid any network/AWS and use a scripted IO so the wizard is exercised
deterministically.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from strata.install import store, tfvars, validators
from strata.install.modules import schema_from_dict
from strata.install.prompt import ScriptedIO, Wizard
from strata.install.schema import Field, InstallSchema, stratabi_schema


class TestValidators(unittest.TestCase):
    def test_region(self):
        validators.aws_region("us-east-1")  # ok
        with self.assertRaises(ValueError):
            validators.aws_region("useast1")

    def test_vpc_and_subnet(self):
        validators.vpc_id("vpc-0123abcd")
        validators.subnet_id("subnet-0a1b2c3d4e")
        with self.assertRaises(ValueError):
            validators.vpc_id("vpc-xyz")
        with self.assertRaises(ValueError):
            validators.subnet_id("net-0123abcd")

    def test_cidr(self):
        validators.cidr("203.0.113.0/24")
        with self.assertRaises(ValueError):
            validators.cidr("999.0.0.0/24")
        with self.assertRaises(ValueError):
            validators.cidr("10.0.0.0/40")

    def test_name_prefix(self):
        validators.name_prefix("stratabi")
        with self.assertRaises(ValueError):
            validators.name_prefix("1bad")
        with self.assertRaises(ValueError):
            validators.name_prefix("WAY-too-long-name-prefix-value")

    def test_ecr_image(self):
        validators.ecr_image("123.dkr.ecr.us-east-1.amazonaws.com/stratabi:latest")
        with self.assertRaises(ValueError):
            validators.ecr_image("stratabi")  # no registry/tag


class TestTfvarsRendering(unittest.TestCase):
    def test_types_and_skip_empty(self):
        schema = InstallSchema("t", "t", [
            Field("installation", "name", "name", tfvar="name_prefix", default="stratabi"),
            Field("network", "vpc_id", "vpc", tfvar="vpc_id", required=True),
            Field("network", "subnets", "subnets", tfvar="public_subnet_ids", type="list"),
            Field("features", "flag", "flag", tfvar="enable_x", type="bool", default=False),
            Field("installation", "domain", "domain", tfvar="domain_name", default=""),
        ])
        cfg = {
            "installation": {"name": "prod"},
            "network": {"vpc_id": "vpc-0123abcd", "subnets": ["subnet-1", "subnet-2"]},
            "features": {"flag": True},
        }
        out = tfvars.render(schema, cfg)
        self.assertIn('name_prefix = "prod"', out)
        self.assertIn('vpc_id = "vpc-0123abcd"', out)
        self.assertIn('public_subnet_ids = ["subnet-1", "subnet-2"]', out)
        self.assertIn("enable_x = true", out)
        # empty optional string is skipped
        self.assertNotIn("domain_name", out)
        # section headers present
        self.assertIn("# ---- network ----", out)


class TestStoreRoundTrip(unittest.TestCase):
    def test_save_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "strata.yaml"
            data = {"installation": {"name": "prod", "aws_region": "us-east-1"},
                    "network": {"public_subnet_ids": ["subnet-1"]}}
            store.save(p, data)
            self.assertTrue(p.is_file())
            self.assertIn("source of truth", p.read_text())
            back = store.load(p)
            self.assertEqual(back["installation"]["name"], "prod")
            self.assertEqual(back["network"]["public_subnet_ids"], ["subnet-1"])

    def test_flatten(self):
        flat = store.flatten({"a": {"x": 1}, "b": {"y": 2}})
        self.assertEqual(flat, {"x": 1, "y": 2})


class TestWizard(unittest.TestCase):
    def _schema(self):
        return InstallSchema("t", "t", [
            Field("installation", "name", "name", tfvar="name_prefix",
                  default="stratabi", required=True, validator="name_prefix"),
            Field("network", "vpc_id", "vpc", tfvar="vpc_id",
                  required=True, validator="vpc_id"),
            Field("tls", "enable_https", "https?", tfvar="enable_https",
                  type="bool", default=False),
            Field("tls", "acm", "acm arn", tfvar="acm_certificate_arn",
                  default="", when=("enable_https", True)),
        ])

    def test_prompts_only_missing(self):
        # name already present -> only vpc_id + https are asked.
        cfg = {"installation": {"name": "prod"}}
        io = ScriptedIO(answers=["vpc-0123abcd", "n"])
        Wizard(self._schema(), cfg, io=io, interactive=True).run()
        self.assertEqual(cfg["network"]["vpc_id"], "vpc-0123abcd")
        self.assertEqual(cfg["installation"]["name"], "prod")  # untouched
        prompts = [p for p in io.log if not p.startswith("SAY")]
        self.assertTrue(any("vpc" in p for p in prompts))
        self.assertFalse(any(p == "name" for p in prompts))

    def test_when_gate(self):
        # https=yes -> acm prompt appears
        cfg = {"installation": {"name": "prod"}, "network": {"vpc_id": "vpc-0123abcd"}}
        io = ScriptedIO(answers=["y", "arn:aws:acm:us-east-1:1:certificate/x"])
        Wizard(self._schema(), cfg, io=io, interactive=True).run()
        self.assertEqual(cfg["tls"]["enable_https"], True)
        self.assertEqual(cfg["tls"]["acm"], "arn:aws:acm:us-east-1:1:certificate/x")

    def test_validation_retries(self):
        cfg = {"installation": {"name": "prod"}}
        # first vpc answer invalid, then valid; then https=n
        io = ScriptedIO(answers=["nope", "vpc-0123abcd", "n"])
        Wizard(self._schema(), cfg, io=io, interactive=True).run()
        self.assertEqual(cfg["network"]["vpc_id"], "vpc-0123abcd")
        self.assertTrue(any("! must look like" in m for m in io.log))

    def test_non_interactive_missing_required(self):
        from strata.errors import ConfigError
        cfg = {"installation": {"name": "prod"}}  # vpc_id missing
        with self.assertRaises(ConfigError):
            Wizard(self._schema(), cfg, interactive=False).run()

    def test_non_interactive_ok(self):
        cfg = {"installation": {"name": "prod"}, "network": {"vpc_id": "vpc-0123abcd"}}
        Wizard(self._schema(), cfg, interactive=False).run()
        # bool default applied
        self.assertEqual(cfg["tls"]["enable_https"], False)


class TestModuleSchema(unittest.TestCase):
    def test_schema_from_dict_namespaced(self):
        doc = {"title": "FinOps", "fields": [
            {"key": "granularity", "prompt": "granularity", "tfvar": "ce_granularity",
             "type": "enum", "choices": ["DAILY", "MONTHLY"], "default": "DAILY"},
        ]}
        schema = schema_from_dict("finops", doc)
        self.assertEqual(schema.fields[0].section, "module:finops")
        self.assertEqual(schema.fields[0].tfvar, "ce_granularity")

    def test_module_tfvars(self):
        doc = {"fields": [{"key": "threshold", "prompt": "t",
                           "tfvar": "budget_threshold", "type": "int", "default": 1000}]}
        schema = schema_from_dict("finops", doc)
        cfg = {"module:finops": {"threshold": 500}}
        out = tfvars.render_module(schema, cfg, "finops")
        self.assertIn("budget_threshold = 500", out)


class TestStrataBISchema(unittest.TestCase):
    def test_required_fields_present(self):
        keys = {f.key for f in stratabi_schema().fields}
        for required in ("vpc_id", "public_subnet_ids", "private_subnet_ids", "stratabi_image"):
            self.assertIn(required, keys)

    def test_security_flags_map_to_tfvars(self):
        tfvar_names = {f.tfvar for f in stratabi_schema().tfvar_fields()}
        for v in ("enable_guardduty", "enable_inspector", "enable_securityhub"):
            self.assertIn(v, tfvar_names)


if __name__ == "__main__":
    unittest.main()
