output "bucket" {
  value = aws_s3_bucket.strataci.bucket
}

output "install_registry_table" {
  value = aws_dynamodb_table.install_registry.name
}

output "jobs_table" {
  value = aws_dynamodb_table.jobs.name
}

output "lock_table" {
  value = aws_dynamodb_table.tf_locks.name
}

output "log_group" {
  value = aws_cloudwatch_log_group.runner.name
}

output "codebuild_project" {
  value = aws_codebuild_project.runner.name
}

output "runner_role_arn" {
  value = aws_iam_role.runner.arn
}

output "deploy_role_arn" {
  description = "Broad role the runner assumes WITH a module-scoped session policy (Decision A)."
  value       = aws_iam_role.deploy.arn
}

output "operator_policy_arn" {
  description = "Attach to the engineer/identity that runs StrataCLI."
  value       = aws_iam_policy.operator.arn
}

# Ready-to-paste local config for StrataCLI (~/.stratacli/config.toml [customer]).
output "stratacli_customer_config" {
  value = <<-EOT
    [customer]
    bucket            = "${aws_s3_bucket.strataci.bucket}"
    install_table     = "${aws_dynamodb_table.install_registry.name}"
    jobs_table        = "${aws_dynamodb_table.jobs.name}"
    lock_table        = "${aws_dynamodb_table.tf_locks.name}"
    log_group         = "${aws_cloudwatch_log_group.runner.name}"
    codebuild_project = "${aws_codebuild_project.runner.name}"
  EOT
}
