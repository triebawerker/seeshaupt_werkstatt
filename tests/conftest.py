import pytest
from aws_cdk import App
from aws_cdk.assertions import Template

from static_site.app_builder import build

ACCOUNT = "123456789012"
BASE_CONTEXT = {"site_name": "werkstatt", "region": "eu-central-1", "domain_name": ""}


def synth(**context):
    app = App(context={**BASE_CONTEXT, **context})
    built = build(app, account=ACCOUNT)
    site = Template.from_stack(built.site)
    cert = Template.from_stack(built.certificate) if built.certificate else None
    return built, site, cert


@pytest.fixture(scope="module")
def no_domain():
    return synth()


@pytest.fixture(scope="module")
def with_domain():
    return synth(domain_name="example.org")
