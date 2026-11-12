"""Wires the stacks together. Kept separate from app.py so tests can build the app."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from aws_cdk import App, Environment, Tags

from .certificate_stack import CertificateStack
from .config import CERTIFICATE_REGION, SiteConfig
from .site_stack import SiteStack


@dataclass
class BuiltApp:
    config: SiteConfig
    site: SiteStack
    certificate: Optional[CertificateStack]


def stack_prefix(config: SiteConfig) -> str:
    return "".join(part.capitalize() for part in config.site_name.split("-"))


def build(app: App, account: Optional[str] = None) -> BuiltApp:
    config = SiteConfig.from_context(app.node.try_get_context)
    account = account or os.environ.get("CDK_DEFAULT_ACCOUNT")
    prefix = stack_prefix(config)

    cert_stack = None
    if config.has_domain:
        cert_stack = CertificateStack(
            app,
            f"{prefix}Certificate",
            config=config,
            env=Environment(account=account, region=CERTIFICATE_REGION),
            cross_region_references=config.region != CERTIFICATE_REGION,
        )

    site = SiteStack(
        app,
        f"{prefix}Site",
        config=config,
        certificate=cert_stack.certificate if cert_stack else None,
        env=Environment(account=account, region=config.region),
        cross_region_references=bool(cert_stack) and config.region != CERTIFICATE_REGION,
    )
    Tags.of(app).add("project", config.site_name)
    return BuiltApp(config=config, site=site, certificate=cert_stack)
