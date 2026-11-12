import json

from aws_cdk.assertions import Match

from tests.conftest import synth


def policy_statements(template):
    policies = template.find_resources("AWS::IAM::Policy")
    assert len(policies) == 1, "editor should have exactly one inline policy"
    return next(iter(policies.values()))["Properties"]["PolicyDocument"]["Statement"]


# --- bucket -------------------------------------------------------------------

def test_bucket_is_private_encrypted_and_versioned(no_domain):
    _, t, _ = no_domain
    t.resource_count_is("AWS::S3::Bucket", 1)
    t.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "VersioningConfiguration": {"Status": "Enabled"},
            "BucketEncryption": Match.any_value(),
        },
    )
    assert "WebsiteConfiguration" not in json.dumps(t.find_resources("AWS::S3::Bucket"))


def test_bucket_is_retained_on_stack_delete(no_domain):
    _, t, _ = no_domain
    t.has_resource("AWS::S3::Bucket", {"DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain"})


def test_bucket_policy_only_grants_cloudfront_and_denies_plain_http(no_domain):
    _, t, _ = no_domain
    policy = next(iter(t.find_resources("AWS::S3::BucketPolicy").values()))
    statements = policy["Properties"]["PolicyDocument"]["Statement"]
    allows = [s for s in statements if s["Effect"] == "Allow"]
    assert allows, "expected an allow for CloudFront"
    for s in allows:
        assert s["Principal"] == {"Service": "cloudfront.amazonaws.com"}
        assert "AWS:SourceArn" in json.dumps(s["Condition"])
    assert any(
        s["Effect"] == "Deny" and s["Condition"] == {"Bool": {"aws:SecureTransport": "false"}} for s in statements
    )


# --- cloudfront ---------------------------------------------------------------

def test_distribution_uses_oac_and_https(no_domain):
    _, t, _ = no_domain
    t.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)
    t.has_resource_properties(
        "AWS::CloudFront::OriginAccessControl",
        {"OriginAccessControlConfig": Match.object_like({"OriginAccessControlOriginType": "s3", "SigningBehavior": "always"})},
    )
    t.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": Match.object_like(
                {
                    "DefaultRootObject": "index.html",
                    "DefaultCacheBehavior": Match.object_like({"ViewerProtocolPolicy": "redirect-to-https"}),
                    "Origins": [Match.object_like({"OriginAccessControlId": Match.any_value()})],
                }
            )
        },
    )


def test_no_domain_means_default_cloudfront_certificate(no_domain):
    built, t, cert = no_domain
    assert cert is None and built.certificate is None
    config = next(iter(t.find_resources("AWS::CloudFront::Distribution").values()))["Properties"]["DistributionConfig"]
    assert "Aliases" not in config
    assert "ViewerCertificate" not in config
    t.has_output("SiteUrl", {"Value": {"Fn::Join": ["", ["https://", Match.any_value()]]}})
    assert not t.find_outputs("DnsWwwRecord")


def test_missing_pages_serve_404_page(no_domain):
    _, t, _ = no_domain
    t.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": Match.object_like(
                {
                    "CustomErrorResponses": Match.array_with(
                        [Match.object_like({"ErrorCode": 403, "ResponseCode": 404, "ResponsePagePath": "/404.html"})]
                    )
                }
            )
        },
    )


def test_directory_index_function_attached(no_domain):
    _, t, _ = no_domain
    t.resource_count_is("AWS::CloudFront::Function", 1)
    t.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": Match.object_like(
                {
                    "DefaultCacheBehavior": Match.object_like(
                        {"FunctionAssociations": [Match.object_like({"EventType": "viewer-request"})]}
                    )
                }
            )
        },
    )


# --- custom domain ------------------------------------------------------------

def test_domain_adds_us_east_1_certificate_with_dns_validation(with_domain):
    built, _, cert = with_domain
    assert built.certificate.region == "us-east-1"
    cert.has_resource_properties(
        "AWS::CertificateManager::Certificate",
        {
            "DomainName": "example.org",
            "SubjectAlternativeNames": ["www.example.org"],
            "ValidationMethod": "DNS",
        },
    )


def test_domain_sets_aliases_and_sni_certificate(with_domain):
    _, t, _ = with_domain
    t.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": Match.object_like(
                {
                    "Aliases": ["example.org", "www.example.org"],
                    "ViewerCertificate": Match.object_like(
                        {"SslSupportMethod": "sni-only", "MinimumProtocolVersion": "TLSv1.2_2021"}
                    ),
                }
            )
        },
    )
    t.has_output("SiteUrl", {"Value": "https://www.example.org"})
    t.has_output("DnsWwwRecord", {"Value": Match.any_value()})
    t.has_output("DnsApexRecord", {"Value": Match.any_value()})


def test_site_in_us_east_1_needs_no_cross_region_references():
    built, t, _ = synth(domain_name="example.org", region="us-east-1")
    assert built.site.region == "us-east-1"
    assert not t.find_resources("Custom::CrossRegionExportReader")


# --- editor user --------------------------------------------------------------

def test_editor_user_has_console_login_with_forced_reset(no_domain):
    _, t, _ = no_domain
    t.has_resource_properties(
        "AWS::IAM::User",
        {"UserName": "werkstatt-editor", "LoginProfile": Match.object_like({"PasswordResetRequired": True})},
    )
    t.resource_count_is("AWS::SecretsManager::Secret", 1)
    t.resource_count_is("AWS::IAM::AccessKey", 0)  # keys are created by hand, never stored in the template


def test_editor_without_console_password():
    _, t, _ = synth(create_console_password="false")
    user = next(iter(t.find_resources("AWS::IAM::User").values()))["Properties"]
    assert "LoginProfile" not in user
    t.resource_count_is("AWS::SecretsManager::Secret", 0)


def test_editor_policy_is_least_privilege(no_domain):
    _, t, _ = no_domain
    statements = policy_statements(t)
    by_sid = {s["Sid"]: s for s in statements}
    assert set(by_sid) == {"ListSiteBucket", "EditSiteObjects", "InvalidateSiteCache"}

    for s in statements:
        assert s["Effect"] == "Allow"
        actions = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
        assert all("*" not in a for a in actions), f"wildcard action in {s['Sid']}"
        assert s["Resource"] != "*", f"wildcard resource in {s['Sid']}"

    # object actions are scoped to this bucket's objects
    assert "/*" in json.dumps(by_sid["EditSiteObjects"]["Resource"])
    assert "ContentBucket" in json.dumps(by_sid["EditSiteObjects"]["Resource"])
    # cloudfront actions are scoped to this distribution
    assert "Distribution" in json.dumps(by_sid["InvalidateSiteCache"]["Resource"])
    assert "cloudfront:CreateInvalidation" in by_sid["InvalidateSiteCache"]["Action"]
    # no IAM or bucket-config powers
    flat = json.dumps(statements)
    for forbidden in ("s3:PutBucketPolicy", "s3:DeleteBucket", "iam:", "cloudfront:UpdateDistribution"):
        assert forbidden not in flat


def test_handover_outputs_present(no_domain):
    _, t, _ = no_domain
    for name in (
        "BucketName",
        "DistributionId",
        "DistributionDomainName",
        "EditorUserName",
        "ConsoleSignInUrl",
        "S3ConsoleUrl",
        "SyncCommand",
        "InvalidateCommand",
        "EditorPasswordSecretArn",
    ):
        assert t.find_outputs(name), f"missing output {name}"
