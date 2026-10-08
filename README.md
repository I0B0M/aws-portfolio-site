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
| GitHub Actions | Tests, `sam deploy`, `s3 sync`, CloudFront invalidation. Signs in with repository secrets. |

## First-time setup

1. Sign in to AWS (`aws login`), region `us-east-2`.
2. Store the API key value once: `aws ssm put-parameter --name /portfolio/api-key --type String --value "$(openssl rand -hex 24)"`
3. Deploy once by hand: `sam build && sam deploy --guided`, then `aws s3 sync site/ s3://<SiteBucketName>/ --delete`.
4. Create an IAM user for deployments and add its keys as GitHub repository secrets
   `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (`gh secret set <NAME>` prompts for the value).
5. From then on, every push to `main` tests, deploys, syncs, and invalidates the cache.
   The live URL appears in the run summary.

## Local checks

```
pip install pytest boto3 cfn-lint
cfn-lint template.yaml
pytest
```

## Cleanup

Empty the site bucket, delete the `aws-portfolio` stack, then delete the
`/portfolio/api-key` parameter and the deploy IAM user.
