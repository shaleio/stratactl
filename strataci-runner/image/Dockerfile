# Optional prebaked runner image (recommended for reproducible runs).
#
# Build + push to ECR, then point the CodeBuild project at it
# (terraform var runner_image = "<acct>.dkr.ecr.<region>.amazonaws.com/strataci-runner:latest").
# With this image the buildspec's install phase is a no-op (tofu + strata present).

FROM python:3.12-slim

ARG TOFU_VERSION=1.8.5

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip ca-certificates \
    && curl -sSL "https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}/tofu_${TOFU_VERSION}_linux_amd64.zip" -o /tmp/tofu.zip \
    && unzip -o /tmp/tofu.zip -d /usr/local/bin tofu \
    && rm -rf /tmp/tofu.zip /var/lib/apt/lists/*

# Install the strata package (CLI + runner share one codebase). The [runner]
# extra adds cryptography for offline bundle-signature verification.
COPY ../../stratacli /opt/stratacli
RUN pip install --no-cache-dir "/opt/stratacli[runner]"

# CodeBuild overrides the command via buildspec; this is the default for local runs.
ENTRYPOINT ["python", "-m", "strata.runner"]
