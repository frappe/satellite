"""TLS service — the issuance flow (DNS upsert → issue → record → push) and the renew
sweep, all without certbot/AWS (the providers, the fleet, and the disk reads are mocked)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from satellite.services import tls as tls_service
from satellite.tls.base import IssuedCert


def _region(domain="blr1.frappe.dev") -> str:
	for name in frappe.get_all("Region Domain", pluck="name"):
		frappe.delete_doc("Region Domain", name, force=1, ignore_permissions=True)
	for name in frappe.get_all("TLS Certificate", pluck="name"):
		frappe.delete_doc("TLS Certificate", name, force=1, ignore_permissions=True)
	return frappe.get_doc(
		{
			"doctype": "Region Domain",
			"domain": domain,
			"region": "blr1",
			"is_active": 1,
			"tls_provider_type": "Let's Encrypt",
			"dns_provider_type": "Route53",
		}
	).insert(ignore_permissions=True).name


class _FakeDns:
	def __init__(self):
		self.upserts = []

	def upsert_wildcard(self, domain, targets):
		self.upserts.append((domain, targets))
		return ["A *." + domain]


class TestIssueAndPush(IntegrationTestCase):
	def setUp(self) -> None:
		self.region = _region()

	def test_upserts_dns_issues_records_and_pushes(self) -> None:
		dns = _FakeDns()
		issued = IssuedCert("/fc.pem", "/pk.pem", "2026-06-08 00:00:00", "2026-09-06 00:00:00")
		tls = SimpleNamespace(issue=lambda domain, dns_provider: issued)
		with (
			patch.object(tls_service, "for_dns_provider_type", return_value=dns),
			patch.object(tls_service, "for_tls_provider_type", return_value=tls),
			patch.object(tls_service, "wildcard_targets", return_value=(["1.2.3.4"], ["2001:db8::ff"])),
			patch.object(tls_service, "_proxy_vms", return_value=["proxy-vm"]),
			patch.object(tls_service, "_read_pem", return_value="PEM"),
			patch.object(tls_service, "push_cert") as push_cert,
		):
			tls_service.issue_and_push(self.region)

		# DNS wildcard published at the fleet addresses.
		(domain, targets), = dns.upserts
		self.assertEqual(domain, "blr1.frappe.dev")
		self.assertEqual((targets.ipv4, targets.ipv6), (["1.2.3.4"], ["2001:db8::ff"]))
		# Cert recorded Active with the parsed window.
		cert = frappe.get_doc("TLS Certificate", "blr1.frappe.dev")
		self.assertEqual(cert.status, "Active")
		self.assertEqual(cert.fullchain_path, "/fc.pem")
		self.assertEqual(str(cert.not_after), "2026-09-06 00:00:00")
		# Pushed the PEMs to every proxy.
		push_cert.assert_called_once_with("proxy-vm", "PEM", "PEM")


class TestRenewExpiring(IntegrationTestCase):
	def setUp(self) -> None:
		self.region = _region()

	def test_issues_when_no_cert_exists(self) -> None:
		with patch.object(tls_service, "issue_and_push") as issue:
			renewed = tls_service.renew_expiring()
		issue.assert_called_once_with(self.region)
		self.assertEqual(renewed, ["blr1.frappe.dev"])

	def test_skips_a_cert_far_from_expiry(self) -> None:
		frappe.get_doc(
			{
				"doctype": "TLS Certificate",
				"domain": "blr1.frappe.dev",
				"status": "Active",
				"not_after": add_to_date(now_datetime(), days=365),
			}
		).insert(ignore_permissions=True)
		with patch.object(tls_service, "issue_and_push") as issue:
			renewed = tls_service.renew_expiring()
		issue.assert_not_called()
		self.assertEqual(renewed, [])

	def test_renews_a_cert_inside_the_window(self) -> None:
		frappe.get_doc(
			{
				"doctype": "TLS Certificate",
				"domain": "blr1.frappe.dev",
				"status": "Active",
				"not_after": add_to_date(now_datetime(), days=5),
			}
		).insert(ignore_permissions=True)
		with patch.object(tls_service, "issue_and_push") as issue:
			tls_service.renew_expiring()
		issue.assert_called_once_with(self.region)
