"""DNS provider seam — the registry + the Route 53 wiring (certbot flag, credential
env, wildcard UPSERT) without touching AWS. Construction reads Route53 Settings, so we
stub the Single read and the secret fetch."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from satellite.dns import WildcardTargets, for_dns_provider_type, route53


class _FakeRoute53Client:
	"""Captures change_resource_record_sets calls and serves a fixed zone list."""

	def __init__(self, zones: list[str]) -> None:
		self._zones = zones
		self.change_calls: list[dict] = []

	def get_paginator(self, _operation: str):
		zones = [{"Name": z + ".", "Id": f"/hostedzone/Z-{z}", "Config": {}} for z in self._zones]

		class _Paginator:
			def paginate(_self):
				yield {"HostedZones": zones}

		return _Paginator()

	def change_resource_record_sets(self, **kwargs) -> dict:
		self.change_calls.append(kwargs)
		return {"ChangeInfo": {"Id": "/change/C1", "Status": "PENDING"}}


def _provider(access="AKIA123", secret="topsecret", region="us-east-1") -> route53.Route53DnsProvider:
	settings = SimpleNamespace(
		access_key_id=access, region=region, get_password=lambda *a, **k: secret
	)
	with patch.object(route53.frappe, "get_single", return_value=settings):
		return route53.Route53DnsProvider()


class TestDnsRegistry(IntegrationTestCase):
	def test_resolves_route53(self) -> None:
		with patch.object(route53.frappe, "get_single", return_value=SimpleNamespace(
			access_key_id="A", region="", get_password=lambda *a, **k: "s"
		)):
			provider = for_dns_provider_type("Route53")
		self.assertIsInstance(provider, route53.Route53DnsProvider)

	def test_unknown_type_throws(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			for_dns_provider_type("Cloudflare")


class TestRoute53DnsProvider(IntegrationTestCase):
	def test_certbot_authenticator_is_route53(self) -> None:
		self.assertEqual(_provider().certbot_authenticator(), "route53")

	def test_credential_env_carries_aws_keys(self) -> None:
		env = _provider(access="AKIAEXAMPLE", secret="shh", region="eu-west-1").credential_env()
		self.assertEqual(env["AWS_ACCESS_KEY_ID"], "AKIAEXAMPLE")
		self.assertEqual(env["AWS_SECRET_ACCESS_KEY"], "shh")
		self.assertEqual(env["AWS_DEFAULT_REGION"], "eu-west-1")

	def test_region_defaults_when_blank(self) -> None:
		self.assertEqual(_provider(region="").region, "us-east-1")

	def test_authenticate_reports_boto3_missing(self) -> None:
		provider = _provider()
		with patch.dict("sys.modules", {"boto3": None}):
			result = provider.authenticate()
		self.assertFalse(result.ok)
		self.assertIn("boto3", result.error)

	def test_upsert_wildcard_writes_a_and_aaaa_longest_zone_wins(self) -> None:
		provider = _provider()
		fake = _FakeRoute53Client(zones=["x.frappe.dev", "frappe.dev"])
		with patch.object(provider, "_client", return_value=fake):
			records = provider.upsert_wildcard(
				"blr1.x.frappe.dev", WildcardTargets(ipv4=["1.2.3.4"], ipv6=["2400:abcd::1"])
			)
		self.assertEqual(records, ["A *.blr1.x.frappe.dev", "AAAA *.blr1.x.frappe.dev"])
		(call,) = fake.change_calls
		self.assertEqual(call["HostedZoneId"], "/hostedzone/Z-x.frappe.dev")
		by_type = {
			c["ResourceRecordSet"]["Type"]: c["ResourceRecordSet"]
			for c in call["ChangeBatch"]["Changes"]
		}
		self.assertEqual([r["Value"] for r in by_type["A"]["ResourceRecords"]], ["1.2.3.4"])
		self.assertEqual([r["Value"] for r in by_type["AAAA"]["ResourceRecords"]], ["2400:abcd::1"])
		self.assertEqual(by_type["A"]["TTL"], route53.WILDCARD_TTL_SECONDS)

	def test_upsert_wildcard_skips_empty_family(self) -> None:
		provider = _provider()
		fake = _FakeRoute53Client(zones=["x.frappe.dev"])
		with patch.object(provider, "_client", return_value=fake):
			records = provider.upsert_wildcard("blr1.x.frappe.dev", WildcardTargets(ipv4=["1.2.3.4"], ipv6=[]))
		self.assertEqual(records, ["A *.blr1.x.frappe.dev"])

	def test_upsert_wildcard_throws_when_no_targets(self) -> None:
		provider = _provider()
		fake = _FakeRoute53Client(zones=["x.frappe.dev"])
		with patch.object(provider, "_client", return_value=fake):
			with self.assertRaises(frappe.ValidationError):
				provider.upsert_wildcard("blr1.x.frappe.dev", WildcardTargets(ipv4=[], ipv6=[]))
		self.assertEqual(fake.change_calls, [])

	def test_upsert_wildcard_throws_when_no_zone(self) -> None:
		provider = _provider()
		fake = _FakeRoute53Client(zones=["unrelated.example.com"])
		with patch.object(provider, "_client", return_value=fake):
			with self.assertRaises(frappe.ValidationError):
				provider.upsert_wildcard("blr1.x.frappe.dev", WildcardTargets(ipv4=["1.2.3.4"], ipv6=[]))
