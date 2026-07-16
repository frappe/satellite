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
	"build_mode": "site",
	"warm": True,
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
		for doctype in ("Subdomain", "Custom Domain", "Port Mapping", "Virtual Machine"):
			for name in frappe.get_all(doctype, pluck="name"):
				frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)

	def test_register_upserts_mirror(self) -> None:
		with patch.object(registration.AtlasClient, "get_virtual_machine", return_value=PAYLOAD):
			name = registration.register_vm(ATLAS, "vm-uuid-1")
		doc = frappe.get_doc("Virtual Machine", name)
		self.assertEqual(doc.atlas, ATLAS)
		self.assertEqual(doc.remote_id, "vm-uuid-1")
		self.assertEqual(doc.server_ipv4, "1.2.3.4")  # the host SSH target
		self.assertEqual(doc.guest_ipv6, "2001:db8::1")  # the guest SSH target
		self.assertEqual(doc.build_mode, "site")  # provisioner fact for the site deploy
		self.assertEqual(doc.warm, 1)

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

	def test_terminated_status_tears_down_routes(self) -> None:
		# A mirror flipping to Terminated must delete the VM's routing rows so the proxy
		# stops targeting a dead /128 — driven off the status, so the sweep heals it too.
		with patch("satellite.services.routing.enqueue_reconcile"):
			with patch.object(registration.AtlasClient, "get_virtual_machine", return_value=PAYLOAD):
				name = registration.register_vm(ATLAS, "vm-uuid-1")
			sub = frappe.get_doc(
				{"doctype": "Subdomain", "subdomain": "route-x", "virtual_machine": name, "active": 1}
			).insert(ignore_permissions=True)
			self.assertTrue(frappe.db.exists("Subdomain", sub.name))
			with patch.object(
				registration.AtlasClient,
				"get_virtual_machine",
				return_value={**PAYLOAD, "status": "Terminated"},
			):
				registration.register_vm(ATLAS, "vm-uuid-1")
			self.assertFalse(frappe.db.exists("Subdomain", sub.name))

	def test_address_change_rederives_routes(self) -> None:
		# A migration re-addresses the guest; the VM's routing rows must re-derive their
		# denormalized address to follow the mirror (replacing Atlas's _repoint_routes).
		with patch("satellite.services.routing.enqueue_reconcile"):
			with patch.object(registration.AtlasClient, "get_virtual_machine", return_value=PAYLOAD):
				name = registration.register_vm(ATLAS, "vm-uuid-1")
			sub = frappe.get_doc(
				{"doctype": "Subdomain", "subdomain": "route-z", "virtual_machine": name, "active": 1}
			).insert(ignore_permissions=True)
			self.assertEqual(sub.address, PAYLOAD["guest_ipv6"])
			with patch.object(
				registration.AtlasClient,
				"get_virtual_machine",
				return_value={**PAYLOAD, "guest_ipv6": "2001:db8::99"},
			):
				registration.register_vm(ATLAS, "vm-uuid-1")
			self.assertEqual(frappe.db.get_value("Subdomain", sub.name, "address"), "2001:db8::99")

	def test_deregister_tears_down_routes_before_deleting_mirror(self) -> None:
		with patch("satellite.services.routing.enqueue_reconcile"):
			with patch.object(registration.AtlasClient, "get_virtual_machine", return_value=PAYLOAD):
				name = registration.register_vm(ATLAS, "vm-uuid-1")
			sub = frappe.get_doc(
				{"doctype": "Subdomain", "subdomain": "route-y", "virtual_machine": name, "active": 1}
			).insert(ignore_permissions=True)
			registration.deregister_vm(ATLAS, "vm-uuid-1")
			self.assertFalse(frappe.db.exists("Subdomain", sub.name))
			self.assertFalse(frappe.db.exists("Virtual Machine", name))
