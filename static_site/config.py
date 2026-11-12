"""Deployment configuration, read from CDK context (cdk.json or `-c key=value`)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

# CloudFront only accepts ACM certificates from this region.
CERTIFICATE_REGION = "us-east-1"

_SITE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")
_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class ConfigError(ValueError):
    """Raised when the CDK context holds an invalid value."""


def _as_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SiteConfig:
    site_name: str
    region: str
    domain_name: Optional[str] = None
    editor_user_name: Optional[str] = None
    create_console_password: bool = True

    @property
    def has_domain(self) -> bool:
        return self.domain_name is not None

    @property
    def www_domain(self) -> Optional[str]:
        return f"www.{self.domain_name}" if self.domain_name else None

    @property
    def alternate_domain_names(self) -> list[str]:
        """Hostnames CloudFront answers for: apex and www."""
        return [self.domain_name, self.www_domain] if self.domain_name else []

    @property
    def editor_name(self) -> str:
        return self.editor_user_name or f"{self.site_name}-editor"

    @classmethod
    def from_context(cls, get: Callable[[str], Any]) -> "SiteConfig":
        """Build config from a context getter such as `app.node.try_get_context`."""
        site_name = (get("site_name") or "").strip().lower()
        if not _SITE_NAME_RE.match(site_name):
            raise ConfigError(
                "site_name must be 3-32 chars, lowercase letters, digits and dashes, "
                f"starting with a letter; got {site_name!r}"
            )

        region = (get("region") or "eu-central-1").strip()

        domain = (get("domain_name") or "").strip().lower().rstrip(".")
        if domain.startswith("www."):
            raise ConfigError("domain_name must be the bare domain (example.org), not www.example.org")
        if domain and not _DOMAIN_RE.match(domain):
            raise ConfigError(f"domain_name is not a valid domain: {domain!r}")

        editor = (get("editor_user_name") or "").strip() or None

        return cls(
            site_name=site_name,
            region=region,
            domain_name=domain or None,
            editor_user_name=editor,
            create_console_password=_as_bool(get("create_console_password"), True),
        )
