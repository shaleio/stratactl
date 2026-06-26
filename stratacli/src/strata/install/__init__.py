"""StrataCLI installation wizard.

A modular pipeline that turns a series of questions into a persisted YAML
installation config (the source of truth) and generated Terraform/OpenTofu
variable files. The pieces are intentionally separate so future commands
(`deploy`, `upgrade`, `module install`, ...) can consume the same config object
without duplicating parsing:

  schema      -- declarative description of the variables an installation needs
  validators  -- pure input-validation functions referenced by schema fields
  store       -- load/save the YAML installation config (source of truth)
  prompt      -- interactive (and non-interactive) prompting engine
  tfvars      -- render terraform.tfvars / *.auto.tfvars from the config
  modules     -- discover module-declared install schemas (no hardcoded names)
"""

from strata.install import modules, prompt, schema, store, tfvars, validators  # noqa: F401

__all__ = ["schema", "validators", "store", "prompt", "tfvars", "modules"]
