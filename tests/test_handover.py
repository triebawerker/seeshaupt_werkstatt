from scripts.handover import format_records, render, site_records, validation_records

CERT = {
    "Status": "PENDING_VALIDATION",
    "DomainValidationOptions": [
        {
            "DomainName": "example.org",
            "ValidationStatus": "PENDING_VALIDATION",
            "ResourceRecord": {"Name": "_a1.example.org.", "Type": "CNAME", "Value": "_b1.acm-validations.aws."},
        },
        {
            "DomainName": "www.example.org",
            "ValidationStatus": "PENDING_VALIDATION",
            "ResourceRecord": {"Name": "_a2.www.example.org.", "Type": "CNAME", "Value": "_b2.acm-validations.aws."},
        },
        {"DomainName": "pending.example.org"},  # record not generated yet
    ],
}

OUTPUTS = {
    "SiteUrl": "https://www.example.org",
    "DistributionDomainName": "d111.cloudfront.net",
    "EditorUserName": "werkstatt-editor",
    "ConsoleSignInUrl": "https://123456789012.signin.aws.amazon.com/console",
    "EditorPasswordSecretArn": "arn:aws:secretsmanager:eu-central-1:123456789012:secret:x",
}


def test_validation_records_extracted_and_deduplicated():
    dup = {"DomainValidationOptions": CERT["DomainValidationOptions"] + [CERT["DomainValidationOptions"][0]]}
    records = validation_records(dup)
    assert [r.name for r in records] == ["_a1.example.org.", "_a2.www.example.org."]
    assert all(r.type == "CNAME" for r in records)


def test_site_records_cover_www_and_apex():
    www, apex = site_records("example.org", "d111.cloudfront.net")
    assert (www.type, www.name, www.value) == ("CNAME", "www.example.org.", "d111.cloudfront.net.")
    assert apex.name == "example.org." and "redirect" in apex.purpose


def test_format_records_has_header_and_rows():
    text = format_records(site_records("example.org", "d111.cloudfront.net"))
    assert text.splitlines()[0].startswith("Type")
    assert len(text.splitlines()) == 3


def test_render_without_domain_has_no_dns_section():
    text = render(OUTPUTS, None, None)
    assert "DNS records" not in text
    assert "werkstatt-editor" in text and "get-secret-value" in text


def test_render_with_pending_site_shows_only_validation_records():
    text = render({}, "example.org", CERT)
    assert "_b1.acm-validations.aws." in text
    assert "d111" not in text
    assert "not deployed yet" in text


def test_render_with_domain_shows_all_records():
    text = render(OUTPUTS, "example.org", CERT)
    for needle in ("_a1.example.org.", "www.example.org.", "ALIAS/ANAME", "PENDING_VALIDATION"):
        assert needle in text
