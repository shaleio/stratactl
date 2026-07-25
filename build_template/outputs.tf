output "ecr_repository_url" {
  description = "ECR repo URL. The built runtime image lands at <this>:<image_tag>."
  value       = aws_ecr_repository.runtime.repository_url
}

output "image_uri" {
  description = "Full image URI to hand to `stratactl init` (stratabi_image) for the platform install."
  value       = "${aws_ecr_repository.runtime.repository_url}:${var.image_tag}"
}

output "codebuild_project" {
  description = "CodeBuild project name. Trigger a build with: aws codebuild start-build --project-name <this>."
  value       = aws_codebuild_project.image.name
}
