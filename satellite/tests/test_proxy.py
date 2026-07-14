"""Proxy guest cert ops — the real proxy SSH is unverifiable in the one-host dev setup,
so we mock run_guest and assert the commands + that the private key rides stdin, not argv."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from satellite.services.proxy import CERT_DIRECTORY, push_cert


def _region(region: str = "blr1") -> None:
	for name in frappe.get_all("Region Domain", pluck="name"):
		frappe.delete_doc("Region Domain", name, force=1, ignore_permissions=True)
	frappe.get_doc(
		{"doctype": "Region Domain", "domain": f"{region}.frappe.dev", "region": region, "is_active": 1}
	).insert(ignore_permissions=True)


class TestPushCert(IntegrationTestCase):
	def setUp(self) -> None:
		_region("blr1")

	def test_writes_both_pems_then_symlinks_and_reloads(self) -> None:
		with patch("satellite.services.proxy.run_guest", return_value=("", "", 0)) as run_guest:
			push_cert("proxy-vm", "FULLCHAIN", "PRIVKEY")
		self.assertEqual(run_guest.call_count, 3)

		fullchain, privkey, reload_ = run_guest.call_args_list
		self.assertIn(f"{CERT_DIRECTORY}/blr1/fullchain.pem", fullchain.args[1])
		self.assertIn("chmod 0644", fullchain.args[1])
		self.assertEqual(fullchain.kwargs["stdin"], "FULLCHAIN")

		self.assertIn("chmod 0600", privkey.args[1])
		self.assertEqual(privkey.kwargs["stdin"], "PRIVKEY")
		self.assertNotIn("PRIVKEY", privkey.args[1])  # the key rides stdin, never argv

		self.assertIn("ln -sfn", reload_.args[1])
		self.assertIn("nginx -s reload", reload_.args[1])

	def test_raises_on_reload_failure(self) -> None:
		def fake(vm, command, timeout=120, stdin=None):
			return ("", "boom", 1) if "reload" in command else ("", "", 0)

		with patch("satellite.services.proxy.run_guest", side_effect=fake):
			with self.assertRaises(frappe.ValidationError):
				push_cert("proxy-vm", "F", "P")
