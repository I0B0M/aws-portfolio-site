#!/usr/bin/env bash
# One-time setup, run from your own terminal while signed in to AWS.
#   1. Stores a random API key in Parameter Store (the repo never sees the value).
#   2. Deploys infra/github-oidc.yaml so GitHub Actions can sign in without stored keys.
# Usage: scripts/bootstrap.sh <github-user>/<repo>
set -euo pipefail

REPO="${1:?usage: scripts/bootstrap.sh <github-user>/<repo>}"
REGION="${AWS_REGION:-us-east-2}"
PARAM="/portfolio/api-key"

if aws ssm get-parameter --name "$PARAM" --region "$REGION" >/dev/null 2>&1; then
  echo "Parameter $PARAM already exists; leaving it alone."
else
  aws ssm put-parameter --name "$PARAM" --type String --region "$REGION" \
    --description "API key value for the portfolio API Gateway" \
    --value "$(openssl rand -hex 24)" >/dev/null
  echo "Created $PARAM."
fi

aws cloudformation deploy \
  --region "$REGION" \
  --stack-name portfolio-github-oidc \
  --template-file "$(dirname "$0")/../infra/github-oidc.yaml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides GitHubRepo="$REPO"

aws cloudformation describe-stacks --region "$REGION" --stack-name portfolio-github-oidc \
  --query "Stacks[0].Outputs[?OutputKey=='RoleArn'].OutputValue" --output text
