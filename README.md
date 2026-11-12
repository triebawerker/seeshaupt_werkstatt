# Static site hosting (S3 + CloudFront, AWS CDK in Python)

Hosts a static website from a **private** S3 bucket behind CloudFront (Origin Access
Control, HTTPS only), and creates a least-privilege IAM user so the site owner can
upload files herself, in the AWS console or with `aws s3 sync`.

The custom domain is **optional**. Without it, the site runs on the
`dxxxx.cloudfront.net` URL. Add the domain later and redeploy; the stack then adds an
ACM certificate and the scripts print the DNS records to enter at the domain provider.

```
 visitor ──HTTPS──▶ CloudFront ──OAC (signed)──▶ S3 bucket (private, versioned)
                      │  ▲                           ▲
         index.html   │  └── invalidation            │ PutObject / DeleteObject
         rewrite fn ──┘                              │
                                          editor IAM user (only this bucket
                                          + this distribution)
```

## What gets created

| Stack | Region | Resources |
|---|---|---|
| `<Name>Site` | `region` (default `eu-central-1`) | S3 bucket (Block Public Access, SSE-S3, TLS only, versioned, old versions expire after 30 days, **retained** on stack delete), CloudFront distribution with OAC, security headers, HTTP/2+3, `PRICE_CLASS_100`, a CloudFront Function that maps `/about/` to `/about/index.html`, 403/404 → `/404.html`, the editor IAM user and a Secrets Manager secret with her initial password |
| `<Name>Certificate` | `us-east-1` (only with a domain) | ACM certificate for `domain` and `www.domain`, DNS-validated. CloudFront only accepts certificates from us-east-1. |

### The editor's permissions

| Statement | Actions | Resource |
|---|---|---|
| `ListSiteBucket` | `s3:ListBucket`, `s3:ListBucketVersions`, `s3:GetBucketLocation` | this bucket |
| `EditSiteObjects` | `s3:GetObject`, `s3:GetObjectVersion`, `s3:PutObject`, `s3:DeleteObject` | objects in this bucket |
| `InvalidateSiteCache` | `cloudfront:CreateInvalidation`, `GetInvalidation`, `ListInvalidations`, `GetDistribution` | this distribution |
| AWS managed `IAMUserChangePassword` | change her own password | herself |

She cannot change the bucket policy, the distribution, or anything else in the account.
Because she has no `s3:ListAllMyBuckets`, the S3 console's bucket list shows an error
for her; she uses the direct bucket link (`S3ConsoleUrl` output) instead, which works.

## Does she need an email address?

**No.** A plain IAM user has a user name and a password, and optionally access keys.
No email is involved, which is why this project uses one.

The alternative is **IAM Identity Center** (SSO). It needs her email, sends her an
invitation, and lets her reset a forgotten password and set up MFA herself. It's the
better choice for several people or several accounts, but it has to be enabled
per organization/account and is more setup for a single editor. With a plain IAM user,
a forgotten password means you reset it for her
(`aws iam update-login-profile --user-name <user> --password ... --password-reset-required`).

## Prerequisites

- Python 3.11+, Node.js 18+ (for the CDK CLI, used via `npx`)
- AWS credentials for the target account with admin-ish rights
- CDK bootstrap in the site region, and in `us-east-1` once you use a domain:

```bash
npx aws-cdk@2.1148.1 bootstrap aws://<ACCOUNT_ID>/eu-central-1 aws://<ACCOUNT_ID>/us-east-1
```

## Setup and tests

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q          # CDK assertion tests + handover script tests, no AWS needed
```

## Configuration (`cdk.json` → `context`, or `-c key=value`)

| Key | Default | Meaning |
|---|---|---|
| `site_name` | `werkstatt` | Prefix for stack names (`WerkstattSite`) and the editor (`werkstatt-editor`) |
| `region` | `eu-central-1` | Region of the bucket and the site stack |
| `domain_name` | empty | Bare domain, e.g. `example.org`. Empty = CloudFront URL only |
| `editor_user_name` | `<site_name>-editor` | IAM user name for her |
| `create_console_password` | `true` | Give her a console login (initial password in Secrets Manager) |

## Step 1: deploy without a domain (prototype)

```bash
npx aws-cdk@2.1148.1 deploy WerkstattSite
aws s3 sync ./site s3://<BucketName>      # the placeholder pages in ./site
python -m scripts.handover                 # prints URL, login, commands
```

Open the `SiteUrl` output. You should see the "Coming soon" page.

## Step 2: add the domain

Set `domain_name` in `cdk.json` (or pass `-c domain_name=example.org`) and run:

```bash
npx aws-cdk@2.1148.1 deploy --all
```

The certificate stack now **waits** (`CREATE_IN_PROGRESS`) until the domain is validated.
That's expected. In a second terminal:

```bash
python -m scripts.handover --domain-name example.org
```

This prints two **CNAME** records (`_xxxx.example.org` → `_yyyy.acm-validations.aws`).
She (or you, with her provider login) adds them at the domain provider. Once DNS has
propagated, usually minutes, ACM issues the certificate, the first deploy continues and
the site stack is updated with `example.org` and `www.example.org`. Leave the
validation records in place forever: ACM uses them for automatic renewal.

If validation takes too long and the deploy times out, run `deploy --all` again and
re-run `scripts.handover` to check the records (for the same domain in the same
account they are normally unchanged).

Then run `python -m scripts.handover` again. It now also prints the records that point
the domain at the site:

| Record | Name | Value |
|---|---|---|
| `CNAME` | `www.example.org` | `dxxxx.cloudfront.net` |
| `ALIAS`/`ANAME` (if supported) | `example.org` | `dxxxx.cloudfront.net` |

**The bare domain (`example.org`)** can't be a CNAME. Options, best first, depending on
the provider:

1. **ALIAS / ANAME / CNAME flattening** at the provider, pointing at the CloudFront hostname.
2. **Redirect** `example.org` → `https://www.example.org` using the provider's web-forwarding
   feature. Many German providers (IONOS, Strato, all-inkl, ...) offer this. Check that
   it redirects HTTPS too, not only HTTP.
3. **Delegate the domain to Route 53**: create a hosted zone, enter its 4 NS records at the
   provider. Cleanest technically, but then her DNS (including email MX records!) lives in
   your AWS account and costs $0.50/month. Not built here; ask if you want it.

## Step 3: hand over to her

`python -m scripts.handover` prints everything; give her:

- **Sign-in URL** (`ConsoleSignInUrl`), **user name**, and the **initial password**
  (`aws secretsmanager get-secret-value --secret-id <EditorPasswordSecretArn> --query SecretString --output text`).
  She must set a new password at first login. Send password and user name over
  different channels.
- **Her bucket link** (`S3ConsoleUrl`): upload/replace files there. `index.html` is the start page.
- **Refresh**: changes show up within 24 h by default. To see them immediately, run
  the invalidation (CLI below) or, in the console, CloudFront → her distribution →
  Invalidations → Create → `/*`.

The initial-password secret costs $0.40/month. Keep it: the stack references it, so
deleting it by hand breaks later deploys. Her real password is the one she sets at
first login, not the one in the secret.

### Optional: access keys for `aws s3 sync`

Access keys are never put in the CloudFormation template. Create them by hand and give
them to her securely:

```bash
aws iam create-access-key --user-name werkstatt-editor
```

Her workflow then:

```bash
aws configure --profile website     # key, secret, region eu-central-1
aws s3 sync ./my-site s3://<BucketName> --delete --profile website
aws cloudfront create-invalidation --distribution-id <DistributionId> --paths '/*' --profile website
```

## Costs (rough, small site)

- S3: cents per month
- CloudFront: free tier covers 1 TB transfer and 10 M requests per month
- Secrets Manager: $0.40/month while the initial-password secret exists
- ACM certificate: free

## Notes and recommendations

- Consider deploying into a dedicated AWS account (AWS Organizations) for the site, so
  even a mistake in IAM can't touch your own resources.
- The bucket is **retained** if you delete the stack, so content is never lost by accident.
  Delete it by hand if you really want it gone.
- Versioning is on: if she deletes or overwrites a file, you can restore the previous
  version for 30 days (console: bucket → "Show versions").
- MFA for her user isn't set up here; to let her enrol MFA herself, add the AWS-documented
  "manage own MFA" policy.

## Project layout

```
app.py                         CDK entry point
static_site/config.py          context parsing and validation
static_site/app_builder.py     wires the stacks (used by app.py and tests)
static_site/certificate_stack.py  ACM cert in us-east-1
static_site/site_stack.py      bucket, CloudFront, editor user, outputs
scripts/handover.py            prints DNS records, URLs, login info
site/                          placeholder index.html and 404.html
tests/                         pytest + CDK assertions
```
