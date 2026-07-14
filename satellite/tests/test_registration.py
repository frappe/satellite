from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from satellite import registration

ATLAS = "reg-test-atlas"
PAYLOAD = {
	"name": "vm-uuid-1",
	"status": "Running",
	"server": "srv-1",
	"server_ipv4": "1.2.3.4",
	"guest_ipv6": "2001:db8::1",
	"tenant": "t1",
	"private_address": "fdaa::1",
	"modified": "2026-07-14 00:00:00",
}


def _ensure_atlas() -> None:
	if not frappe.db.exists("Atlas", ATLAS):
		frappe.get_doc(
			{"doctype": "Atlas", "title": ATLAS, "base_url": "http://atlas.reg", "api_key": "k"}
		).insert(ignore_permissions=True)


class TestRegistration(IntegrationTestCase):
	def setUp(self) -> None:
		_ensure_atlas()
		for name in frappe.get_all("Virtual Machine", pluck="name"):
			frappe.delete_doc("Virtual Machine", name, force=1, ignore_permissions=True)

	def test_register_upserts_mirror(self) -> None:
		with patch.object(registration.AtlasClient, "get_virtual_machine", return_value=PAYLOAD):
			name = registration.register_vm(ATLAS, "vm-uuid-1")
		doc = frappe.get_doc("Virtual Machine", name)
		self.assertEqual(doc.atlas, ATLAS)
		self.assertEqual(doc.remote_id, "vm-uuid-1")
		self.assertEqual(doc.server_ipv4, "1.2.3.4")  # the host SSH target
		self.assertEqual(doc.guest_ipv6, "2001:db8::1")  # the guest SSH target

		# A second event for the same VM updates in place (idempotent, same name).
		with patch.object(
			registration.AtlasClient, "get_virtual_machine", return_value={**PAYLOAD, "status": "Stopped"}
		):
			again = registration.register_vm(ATLAS, "vm-uuid-1")
		self.assertEqual(again, name)
		self.assertEqual(frappe.db.get_value("Virtual Machine", name, "vm_status"), "Stopped")

	def test_deregister_removes_mirror(self) -> None:
		with patch.object(registration.AtlasClient, "get_virtual_machine", return_value=PAYLOAD):
			name = registration.register_vm(ATLAS, "vm-uuid-1")
		registration.deregister_vm(ATLAS, "vm-uuid-1")
		self.assertFalse(frappe.db.exists("Virtual Machine", name))
