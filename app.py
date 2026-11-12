#!/usr/bin/env python3
from aws_cdk import App

from static_site.app_builder import build

app = App()
build(app)
app.synth()
