# AWS Portfolio Site

A static portfolio on a private S3 bucket behind CloudFront, with a serverless
backend, deployed by GitHub Actions on every push to `main`.

```
Visitor ──HTTPS──> CloudFront ──(OAC)──> private S3 bucket        /, /assets/*
                       │
                       └── /api/* ──(adds x-api-key)──> API Gateway ──> Lambda ──> DynamoDB
                                                                          ▲
                                          EventBridge rule, once a day ───┘
```

| Piece | What it does |
|---|---|
| S3 + CloudFront (OAC) | Serves `site/`. The bucket is private; only this distribution can read it. |
| Lambda (`src/api/app.py`) | Visit counter, contact form, daily stats snapshot. |
| API Gateway + API key | `/api/*` requires a key. CloudFront adds it, so the browser never holds it. |
| Parameter Store | Holds the key value (`/portfolio/api-key`); it is not in this repo. |
| EventBridge | Runs the Lambda daily to write a `STATS#<date>` snapshot to DynamoDB. |
| GitHub Actions | Tests, `sam deploy`, `s3 sync`, CloudFront invalidation. Signs in with OIDC, no stored keys. |

## First-time setup

1. Sign in to AWS in your terminal (`aws login`), region `us-east-2`.
2. `scripts/bootstrap.sh <github-user>/<repo>` creates the API key parameter and the GitHub sign-in role, and prints the role ARN.
3. In the GitHub repo, add a repository **variable** (not a secret) `AWS_ROLE_ARN` with that ARN.
4. Push to `main`. The Actions run prints the live URL in its summary.

## Local checks

```
pip install pytest boto3 cfn-lint
cfn-lint template.yaml infra/github-oidc.yaml
pytest
```

## Cleanup

Delete the `aws-portfolio` stack (empty the site bucket first), then the
`portfolio-github-oidc` stack and the `/portfolio/api-key` parameter.
