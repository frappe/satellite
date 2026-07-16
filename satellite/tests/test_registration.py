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

	def test_intent_subdomains_created_from_read_api(self) -> None:
		# Atlas records the Site/Pilot subdomain(s) on the VM; Satellite creates the
		# Subdomain from the mirror — create-only + idempotent.
		with patch("satellite.services.routing.enqueue_reconcile"):
			payload = {**PAYLOAD, "routing_subdomains": ["mysite", "mysite-pilot"]}
			with patch.object(registration.AtlasClient, "get_virtual_machine", return_value=payload):
				name = registration.register_vm(ATLAS, "vm-uuid-1")
			labels = set(frappe.get_all("Subdomain", filters={"virtual_machine": name}, pluck="subdomain"))
			self.assertEqual(labels, {"mysite", "mysite-pilot"})
			self.assertEqual(
				frappe.db.get_value("Subdomain", {"subdomain": "mysite"}, "address"), PAYLOAD["guest_ipv6"]
			)
			with patch.object(registration.AtlasClient, "get_virtual_machine", return_value=payload):
				registration.register_vm(ATLAS, "vm-uuid-1")  # a second event must not duplicate
			self.assertEqual(frappe.db.count("Subdomain", {"virtual_machine": name}), 2)
			self.assertTrue(frappe.db.get_value("Subdomain", {"subdomain": "mysite"}, "provisioner_intent"))

	def test_intent_reconcile_removes_dropped_label(self) -> None:
		# A detached Pilot drops its label from the intent list; the set reconcile deletes
		# its Subdomain (guest self-serve rows, not marked intent, are never touched).
		with patch("satellite.services.routing.enqueue_reconcile"):
			with patch.object(
				registration.AtlasClient,
				"get_virtual_machine",
				return_value={**PAYLOAD, "routing_subdomains": ["site", "site-pilot"]},
			):
				name = registration.register_vm(ATLAS, "vm-uuid-1")
			# a guest self-serve route on the same VM must survive the reconcile
			frappe.get_doc(
				{"doctype": "Subdomain", "subdomain": "guest-route", "virtual_machine": name, "active": 1}
			).insert(ignore_permissions=True)
			with patch.object(
				registration.AtlasClient,
				"get_virtual_machine",
				return_value={**PAYLOAD, "routing_subdomains": ["site"]},  # pilot detached
			):
				registration.register_vm(ATLAS, "vm-uuid-1")
			labels = set(frappe.get_all("Subdomain", filters={"virtual_machine": name}, pluck="subdomain"))
			self.assertEqual(labels, {"site", "guest-route"})

	def test_intent_skipped_until_mirror_is_addressable(self) -> None:
		# The first vm.registered can arrive before addressing; routing_address would throw,
		# so the reconcile is skipped and the next (addressed) event creates the route.
		with patch("satellite.services.routing.enqueue_reconcile"):
			with patch.object(
				registration.AtlasClient,
				"get_virtual_machine",
				return_value={**PAYLOAD, "guest_ipv6": None, "private_address": None, "routing_subdomains": ["s"]},
			):
				name = registration.register_vm(ATLAS, "vm-uuid-1")
			self.assertEqual(frappe.db.count("Subdomain", {"virtual_machine": name}), 0)
			with patch.object(
				registration.AtlasClient,
				"get_virtual_machine",
				return_value={**PAYLOAD, "routing_subdomains": ["s"]},
			):
				registration.register_vm(ATLAS, "vm-uuid-1")
			self.assertEqual(frappe.db.count("Subdomain", {"virtual_machine": name}), 1)

	def test_intent_conflict_does_not_steal_another_vms_label(self) -> None:
		# A label already held by another VM must not be re-pointed; the Site is left
		# unrouted (logged), never silently stolen.
		with patch("satellite.services.routing.enqueue_reconcile"):
			with patch.object(registration.AtlasClient, "get_virtual_machine", return_value=PAYLOAD):
				other = registration.register_vm(ATLAS, "vm-uuid-1")
			frappe.get_doc(
				{"doctype": "Subdomain", "subdomain": "taken", "virtual_machine": other, "active": 1}
			).insert(ignore_permissions=True)
			with patch.object(
				registration.AtlasClient,
				"get_virtual_machine",
				return_value={**PAYLOAD, "name": "vm-uuid-2", "routing_subdomains": ["taken"]},
			):
				mine = registration.register_vm(ATLAS, "vm-uuid-2")
			self.assertEqual(frappe.db.count("Subdomain", {"virtual_machine": mine}), 0)
			self.assertEqual(frappe.db.get_value("Subdomain", {"subdomain": "taken"}, "virtual_machine"), other)

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
