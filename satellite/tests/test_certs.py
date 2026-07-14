"""Pure cert helpers — certbot argv, PEM layout, openssl date parsing (no certbot)."""

from __future__ import annotations

import unittest

from satellite.tls import certs


class TestCertbotCommand(unittest.TestCase):
	def test_argv_shape_and_wildcard_and_dns_flag(self) -> None:
		argv = certs.certbot_command(
			domain="blr1.frappe.dev",
			acme_directory_url="https://acme.example/dir",
			account_email="ops@frappe.io",
			dns_authenticator="route53",
		)
		self.assertEqual(argv[:2], ["certbot", "certonly"])
		self.assertIn("--dns-route53", argv)
		# The wildcard is the -d value, and the account email is never a --flag.
		self.assertEqual(argv[argv.index("-d") + 1], "*.blr1.frappe.dev")
		self.assertEqual(argv[argv.index("-m") + 1], "ops@frappe.io")
		self.assertIn("--keep-until-expiring", argv)
		# Per-domain config dir keeps regions from colliding.
		self.assertTrue(argv[argv.index("--config-dir") + 1].endswith("/certbot/blr1.frappe.dev"))

	def test_paths_live_under_the_per_domain_config_dir(self) -> None:
		self.assertTrue(certs.fullchain_path("blr1.frappe.dev").endswith("/live/blr1.frappe.dev/fullchain.pem"))
		self.assertTrue(certs.privkey_path("blr1.frappe.dev").endswith("/live/blr1.frappe.dev/privkey.pem"))
		self.assertIn("/.satellite/certbot/blr1.frappe.dev", certs.fullchain_path("blr1.frappe.dev"))


class TestParseOpensslDates(unittest.TestCase):
	def test_parses_both_dates(self) -> None:
		out = "notBefore=Jun  8 00:00:00 2026 GMT\nnotAfter=Sep  6 00:00:00 2026 GMT\n"
		self.assertEqual(
			certs.parse_openssl_dates(out), ("Jun  8 00:00:00 2026 GMT", "Sep  6 00:00:00 2026 GMT")
		)

	def test_raises_when_a_line_is_missing(self) -> None:
		with self.assertRaises(ValueError):
			certs.parse_openssl_dates("notBefore=Jun  8 00:00:00 2026 GMT\n")
