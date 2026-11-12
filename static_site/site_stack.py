"""Private S3 bucket + CloudFront (OAC) + a least-privilege editor IAM user."""

from __future__ import annotations

from typing import Optional

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from .config import SiteConfig

# Rewrites /about/ and /about to /about/index.html, so sites from static site
# generators work without a website-endpoint bucket (which would have to be public).
DIRECTORY_INDEX_FUNCTION = """
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri.endsWith('/')) {
    request.uri = uri + 'index.html';
  } else if (uri.split('/').pop().indexOf('.') === -1) {
    request.uri = uri + '/index.html';
  }
  return request;
}
""".strip()


class SiteStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: SiteConfig,
        certificate: Optional[acm.ICertificate] = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if config.has_domain and certificate is None:
            raise ValueError("A certificate is required when domain_name is set")

        self.bucket = s3.Bucket(
            self,
            "ContentBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            # Versioning lets an accidental delete or overwrite be undone.
            versioned=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    noncurrent_version_expiration=Duration.days(30),
                    abort_incomplete_multipart_upload_after=Duration.days(7),
                )
            ],
            removal_policy=RemovalPolicy.RETAIN,
        )

        directory_index = cloudfront.Function(
            self,
            "DirectoryIndexFunction",
            code=cloudfront.FunctionCode.from_inline(DIRECTORY_INDEX_FUNCTION),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
            comment="Serve index.html for directory-style URLs",
        )

        self.distribution = cloudfront.Distribution(
            self,
            "Distribution",
            comment=f"{config.site_name} static site",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
                compress=True,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=directory_index,
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    )
                ],
            ),
            # Without s3:ListBucket for CloudFront, S3 answers 403 for missing keys.
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=status,
                    response_http_status=404,
                    response_page_path="/404.html",
                    ttl=Duration.minutes(1),
                )
                for status in (403, 404)
            ],
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,  # EU + North America edges
            http_version=cloudfront.HttpVersion.HTTP2_AND_3,
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021 if certificate else None,
            domain_names=config.alternate_domain_names or None,
            certificate=certificate,
        )

        self.editor = self._create_editor(config)
        self._outputs(config)

    def _create_editor(self, config: SiteConfig) -> iam.User:
        password_secret = None
        if config.create_console_password:
            password_secret = secretsmanager.Secret(
                self,
                "EditorInitialPassword",
                description=f"Initial console password for {config.editor_name} (must be changed at first login)",
                generate_secret_string=secretsmanager.SecretStringGenerator(
                    password_length=24,
                    exclude_characters="\"'`\\ /@",
                ),
                removal_policy=RemovalPolicy.DESTROY,
            )
            self.password_secret = password_secret

        user = iam.User(
            self,
            "EditorUser",
            user_name=config.editor_name,
            password=password_secret.secret_value if password_secret else None,
            password_reset_required=True if password_secret else None,
        )
        if password_secret:
            user.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("IAMUserChangePassword"))

        distribution_arn = self.format_arn(
            service="cloudfront",
            region="",
            resource="distribution",
            resource_name=self.distribution.distribution_id,
        )

        policy = iam.Policy(
            self,
            "EditorPolicy",
            statements=[
                iam.PolicyStatement(
                    sid="ListSiteBucket",
                    actions=["s3:ListBucket", "s3:ListBucketVersions", "s3:GetBucketLocation"],
                    resources=[self.bucket.bucket_arn],
                ),
                iam.PolicyStatement(
                    sid="EditSiteObjects",
                    actions=[
                        "s3:GetObject",
                        "s3:GetObjectVersion",
                        "s3:PutObject",
                        "s3:DeleteObject",
                    ],
                    resources=[self.bucket.arn_for_objects("*")],
                ),
                iam.PolicyStatement(
                    sid="InvalidateSiteCache",
                    actions=[
                        "cloudfront:CreateInvalidation",
                        "cloudfront:GetInvalidation",
                        "cloudfront:ListInvalidations",
                        "cloudfront:GetDistribution",
                    ],
                    resources=[distribution_arn],
                ),
            ],
        )
        user.attach_inline_policy(policy)
        return user

    def _outputs(self, config: SiteConfig) -> None:
        d = self.distribution
        CfnOutput(self, "BucketName", value=self.bucket.bucket_name)
        CfnOutput(self, "DistributionId", value=d.distribution_id)
        CfnOutput(self, "DistributionDomainName", value=d.distribution_domain_name)
        CfnOutput(
            self,
            "SiteUrl",
            value=f"https://{config.www_domain}" if config.has_domain else f"https://{d.distribution_domain_name}",
        )
        CfnOutput(self, "EditorUserName", value=self.editor.user_name)
        CfnOutput(self, "ConsoleSignInUrl", value=f"https://{self.account}.signin.aws.amazon.com/console")
        CfnOutput(
            self,
            "S3ConsoleUrl",
            value=f"https://s3.console.aws.amazon.com/s3/buckets/{self.bucket.bucket_name}?region={self.region}",
        )
        CfnOutput(
            self,
            "SyncCommand",
            value=f"aws s3 sync ./site s3://{self.bucket.bucket_name} --delete",
        )
        CfnOutput(
            self,
            "InvalidateCommand",
            value=f"aws cloudfront create-invalidation --distribution-id {d.distribution_id} --paths '/*'",
        )
        if config.create_console_password:
            CfnOutput(self, "EditorPasswordSecretArn", value=self.password_secret.secret_arn)
        if config.has_domain:
            CfnOutput(
                self,
                "DnsWwwRecord",
                value=f"CNAME {config.www_domain} -> {d.distribution_domain_name}",
            )
            CfnOutput(
                self,
                "DnsApexRecord",
                value=(
                    f"ALIAS/ANAME {config.domain_name} -> {d.distribution_domain_name} "
                    f"(or a provider redirect to https://{config.www_domain})"
                ),
            )
