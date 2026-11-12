"""Print everything the site owner needs: DNS records, URLs, login and upload commands.

Usage (with AWS credentials of the deploying account):
    python -m scripts.handover                # reads site_name/region from cdk.json
    python -m scripts.handover --site-name werkstatt --region eu-central-1

Read-only: it calls cloudformation:DescribeStacks and acm:DescribeCertificate.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

CERTIFICATE_REGION = "us-east-1"


@dataclass(frozen=True)
class DnsRecord:
    type: str
    name: str
    value: str
    purpose: str


def validation_records(certificate: dict) -> list[DnsRecord]:
    """Extract ACM DNS validation CNAMEs from a DescribeCertificate response."""
    seen: set[tuple[str, str]] = set()
    records = []
    for option in certificate.get("DomainValidationOptions", []):
        rr = option.get("ResourceRecord")
        if not rr:
            continue  # ACM fills this in a few seconds after the request
        key = (rr["Name"], rr["Value"])
        if key in seen:
            continue
        seen.add(key)
        status = option.get("ValidationStatus", "UNKNOWN")
        records.append(
            DnsRecord(rr["Type"], rr["Name"], rr["Value"], f"certificate validation for {option['DomainName']} ({status})")
        )
    return records


def site_records(domain: str, distribution_domain: str) -> list[DnsRecord]:
    return [
        DnsRecord("CNAME", f"www.{domain}.", f"{distribution_domain}.", "points www at the website"),
        DnsRecord(
            "ALIAS/ANAME",
            f"{domain}.",
            f"{distribution_domain}.",
            "bare domain; if the provider has no ALIAS/ANAME, set up a redirect to https://www." + domain,
        ),
    ]


def format_records(records: Iterable[DnsRecord]) -> str:
    rows = [("Type", "Name", "Value", "Purpose")] + [(r.type, r.name, r.value, r.purpose) for r in records]
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    lines = []
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row[:3])) + "  " + row[3])
    return "\n".join(lines)


def outputs_to_dict(stack: dict) -> dict[str, str]:
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def render(site_outputs: dict[str, str], domain: Optional[str], certificate: Optional[dict]) -> str:
    out = ["== Website =="]
    out.append(f"URL:              {site_outputs.get('SiteUrl', '(not deployed yet)')}")
    out.append(f"CloudFront URL:   https://{site_outputs.get('DistributionDomainName', '?')}")
    out.append("")
    out.append("== Login for the editor ==")
    out.append(f"Sign-in URL:      {site_outputs.get('ConsoleSignInUrl', '?')}")
    out.append(f"User name:        {site_outputs.get('EditorUserName', '?')}")
    if "EditorPasswordSecretArn" in site_outputs:
        out.append(
            "Initial password: aws secretsmanager get-secret-value --query SecretString --output text "
            f"--secret-id {site_outputs['EditorPasswordSecretArn']}"
        )
    out.append(f"Files (console):  {site_outputs.get('S3ConsoleUrl', '?')}")
    out.append(f"Upload (CLI):     {site_outputs.get('SyncCommand', '?')}")
    out.append(f"Refresh cache:    {site_outputs.get('InvalidateCommand', '?')}")

    if domain:
        out.append("")
        out.append(f"== DNS records for {domain} (enter these at the domain provider) ==")
        records: list[DnsRecord] = []
        if certificate:
            records += validation_records(certificate)
            out.append(f"Certificate status: {certificate.get('Status', '?')}")
        if "DistributionDomainName" in site_outputs:
            records += site_records(domain, site_outputs["DistributionDomainName"])
        else:
            out.append("(Site stack not deployed yet: www/apex records follow once the certificate is issued.)")
        out.append(format_records(records))
    return "\n".join(out)


def _context() -> dict:
    path = Path(__file__).resolve().parent.parent / "cdk.json"
    return json.loads(path.read_text()).get("context", {})


def main(argv: Optional[list[str]] = None) -> None:
    import boto3  # imported lazily so the pure functions above test without AWS
    from botocore.exceptions import ClientError

    ctx = _context()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site-name", default=ctx.get("site_name"))
    parser.add_argument("--region", default=ctx.get("region", "eu-central-1"))
    parser.add_argument("--domain-name", default=ctx.get("domain_name") or None)
    args = parser.parse_args(argv)

    prefix = "".join(p.capitalize() for p in args.site_name.split("-"))
    cfn = boto3.client("cloudformation", region_name=args.region)

    def stack_outputs(client, name: str) -> dict[str, str]:
        try:
            return outputs_to_dict(client.describe_stacks(StackName=name)["Stacks"][0])
        except ClientError:  # stack does not exist (yet)
            return {}

    site_outputs = stack_outputs(cfn, f"{prefix}Site")

    certificate = None
    if args.domain_name:
        acm = boto3.client("acm", region_name=CERTIFICATE_REGION)
        # The certificate stack may still be waiting for validation, so find the cert via ACM directly.
        for page in acm.get_paginator("list_certificates").paginate(
            CertificateStatuses=["PENDING_VALIDATION", "ISSUED"]
        ):
            for summary in page["CertificateSummaryList"]:
                if summary["DomainName"] == args.domain_name:
                    certificate = acm.describe_certificate(CertificateArn=summary["CertificateArn"])["Certificate"]
                    break
            if certificate:
                break

    print(render(site_outputs, args.domain_name, certificate))


if __name__ == "__main__":
    main()
