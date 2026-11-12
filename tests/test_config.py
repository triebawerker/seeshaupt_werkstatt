import pytest

from static_site.config import ConfigError, SiteConfig


def cfg(**ctx):
    return SiteConfig.from_context({"site_name": "werkstatt", **ctx}.get)


def test_domain_is_optional():
    c = cfg()
    assert not c.has_domain
    assert c.alternate_domain_names == []
    assert c.region == "eu-central-1"


def test_domain_normalised_and_www_derived():
    c = cfg(domain_name=" Example.ORG. ")
    assert c.domain_name == "example.org"
    assert c.alternate_domain_names == ["example.org", "www.example.org"]


@pytest.mark.parametrize("bad", ["www.example.org", "not a domain", "example", "-x.org"])
def test_invalid_domains_rejected(bad):
    with pytest.raises(ConfigError):
        cfg(domain_name=bad)


@pytest.mark.parametrize("bad", ["", "W", "Has Spaces", "1abc", "abc-"])
def test_invalid_site_name_rejected(bad):
    with pytest.raises(ConfigError):
        SiteConfig.from_context({"site_name": bad}.get)


def test_editor_name_default_and_override():
    assert cfg().editor_name == "werkstatt-editor"
    assert cfg(editor_user_name="anna").editor_name == "anna"


@pytest.mark.parametrize("raw,expected", [(None, True), ("false", False), (False, False), ("true", True)])
def test_console_password_flag(raw, expected):
    assert cfg(create_console_password=raw).create_console_password is expected
