"""ACM certificate for the custom domain. Must live in us-east-1 for CloudFront."""

from __future__ import annotations

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_certificatemanager as acm
from constructs import Construct

from .config import SiteConfig


class CertificateStack(Stack):
    """Requests a DNS-validated certificate for `domain` and `www.domain`.

    The domain's DNS is hosted at an external provider, so CDK cannot create the
    validation records itself. CloudFormation waits (CREATE_IN_PROGRESS) until the
    CNAMEs are added at the provider and ACM has issued the certificate. Run
    `python -m scripts.handover` in a second terminal to print the records.
    """

    def __init__(self, scope: Construct, construct_id: str, *, config: SiteConfig, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if not config.has_domain:
            raise ValueError("CertificateStack needs a domain_name")

        self.certificate = acm.Certificate(
            self,
            "Certificate",
            domain_name=config.domain_name,
            subject_alternative_names=[config.www_domain],
            validation=acm.CertificateValidation.from_dns(),  # no hosted zone: records are added by hand
        )

        CfnOutput(self, "CertificateArn", value=self.certificate.certificate_arn)
