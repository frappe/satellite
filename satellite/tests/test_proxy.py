"""Proxy guest cert ops — the real proxy SSH is unverifiable in the one-host dev setup,
so we mock run_guest and assert the commands + that the private key rides stdin, not argv."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from satellite.services.proxy import (
	CERT_DIRECTORY,
	PLACEHOLDER_CERT_SUBJECT,
	push_cert,
	regenerate_placeholder_cert,
)

ATLAS = "proxy-atlas"


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


def _proxy_vm(remote_id: str = "regen-proxy"):
	if not frappe.db.exists("Atlas", ATLAS):
		frappe.get_doc({"doctype": "Atlas", "title": ATLAS, "base_url": "http://a.proxy"}).insert(
			ignore_permissions=True
		)
	name = frappe.db.exists("Virtual Machine", {"atlas": ATLAS, "remote_id": remote_id})
	vm = (
		frappe.get_doc("Virtual Machine", name)
		if name
		else frappe.get_doc(
			{"doctype": "Virtual Machine", "atlas": ATLAS, "remote_id": remote_id, "guest_ipv6": "2001:db8::ff"}
		).insert(ignore_permissions=True)
	)
	with patch("satellite.services.routing.run_guest", return_value=("{}\n", "", 0)):
		frappe.get_doc(
			{"doctype": "Service Binding", "virtual_machine": vm.name, "service": "routing-proxy"}
		).insert(ignore_permissions=True)
	return vm


class TestRegeneratePlaceholderCert(IntegrationTestCase):
	def setUp(self) -> None:
		for name in frappe.get_all("Service Binding", pluck="name"):
			frappe.delete_doc("Service Binding", name, force=1, ignore_permissions=True)

	def test_regens_with_the_byte_identical_subject_and_reloads(self) -> None:
		vm = _proxy_vm()
		with patch("satellite.services.proxy.run_guest", return_value=("", "", 0)) as run_guest:
			regenerate_placeholder_cert(vm.name)
		command = run_guest.call_args.args[1]
		self.assertIn("openssl req -x509 -newkey rsa:2048 -nodes -days 3650", command)
		self.assertIn(PLACEHOLDER_CERT_SUBJECT, command)
		self.assertIn("nginx -s reload", command)

	def test_non_proxy_is_rejected(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			regenerate_placeholder_cert("not-a-proxy")
