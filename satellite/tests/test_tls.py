"""TLS provider registry + the Self-Managed / ZeroSSL stubs (no certbot)."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from satellite.tls import for_tls_provider_type
from satellite.tls.self_managed import SelfManagedTlsProvider
from satellite.tls.zerossl import ZeroSslProvider


class TestTlsRegistry(IntegrationTestCase):
	def test_resolves_self_managed_and_zerossl(self) -> None:
		self.assertIsInstance(for_tls_provider_type("Self-Managed"), SelfManagedTlsProvider)
		self.assertIsInstance(for_tls_provider_type("ZeroSSL"), ZeroSslProvider)

	def test_unknown_type_throws(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			for_tls_provider_type("Vault")


class TestStubProviders(IntegrationTestCase):
	def test_self_managed_authenticates_but_refuses_to_issue(self) -> None:
		provider = SelfManagedTlsProvider()
		self.assertTrue(provider.authenticate().ok)
		with self.assertRaises(frappe.ValidationError):
			provider.issue("blr1.frappe.dev", dns_provider=None)

	def test_zerossl_is_not_implemented(self) -> None:
		provider = ZeroSslProvider()
		self.assertFalse(provider.authenticate().ok)
		self.assertEqual(provider.caa_issuer, "sectigo.com")
		with self.assertRaises(frappe.ValidationError):
			provider.issue("blr1.frappe.dev", dns_provider=None)
