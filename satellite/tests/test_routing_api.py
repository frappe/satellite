"""Guest-callable routing API (satellite.routing.api) — the controller side end to end
with no host. Caller resolution is by the request source /128 vs the mirror's
`guest_ipv6`; the endpoints are @rate_limit-decorated (a request context the unit
harness lacks), so we call the undecorated `.__wrapped__` impl and set
`frappe.local.request_ip` per test (the trusted edge is a host fact; the unit boundary
is "given this source, the right VM or a clean reject")."""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from satellite.routing import api

ATLAS = "api-atlas"
REGION_DOMAIN = "blr1.frappe.dev"


def _region() -> None:
	for name in frappe.get_all("Region Domain", pluck="name"):
		frappe.delete_doc("Region Domain", name, force=1, ignore_permissions=True)
	frappe.get_doc(
		{"doctype": "Region Domain", "domain": REGION_DOMAIN, "region": "blr1", "is_active": 1}
	).insert(ignore_permissions=True)


def _atlas() -> None:
	if not frappe.db.exists("Atlas", ATLAS):
		frappe.get_doc({"doctype": "Atlas", "title": ATLAS, "base_url": "http://a.api"}).insert(
			ignore_permissions=True
		)


def _vm(remote_id: str, guest_ipv6: str, *, status: str = "Running"):
	name = frappe.db.exists("Virtual Machine", {"atlas": ATLAS, "remote_id": remote_id})
	if name:
		return frappe.get_doc("Virtual Machine", name)
	return frappe.get_doc(
		{
			"doctype": "Virtual Machine",
			"atlas": ATLAS,
			"remote_id": remote_id,
			"guest_ipv6": guest_ipv6,
			"vm_status": status,
		}
	).insert(ignore_permissions=True)


def _purge() -> None:
	for doctype in ("Custom Domain", "Subdomain", "Port Mapping", "Service Binding", "Virtual Machine"):
		for name in frappe.get_all(doctype, pluck="name"):
			frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)
	# MyISAM audit rows autocommit outside the test transaction — clear them so counts are
	# per-test.
	for name in frappe.get_all("Bench Routing Audit", pluck="name"):
		frappe.delete_doc("Bench Routing Audit", name, force=1, ignore_permissions=True)


class _ApiTestCase(IntegrationTestCase):
	def setUp(self) -> None:
		_atlas()
		_region()
		_purge()
		self.addCleanup(self._clear_request_ip)

	def _clear_request_ip(self) -> None:
		frappe.local.request_ip = None

	def _as(self, source_ip, endpoint, **kwargs):
		frappe.local.request_ip = source_ip
		return endpoint.__wrapped__(**kwargs)

	def _bind_proxy(self, vm_name: str, public_ipv4: str = "1.2.3.4"):
		with patch("satellite.services.routing.run_guest", return_value=("{}\n", "", 0)):
			return frappe.get_doc(
				{
					"doctype": "Service Binding",
					"virtual_machine": vm_name,
					"service": "routing-proxy",
					"config": json.dumps({"public_ipv4": public_ipv4}),
				}
			).insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# Caller resolution
# ---------------------------------------------------------------------------


class TestCallerResolution(_ApiTestCase):
	def test_register_resolves_vm_from_source_ip(self) -> None:
		vm = _vm("caller", "2001:db8::1")
		result = self._as(vm.guest_ipv6, api.register, label="acme")
		self.assertEqual(result["status"], "ok")
		self.assertEqual(frappe.get_doc("Subdomain", "acme").virtual_machine, vm.name)

	def test_unknown_source_is_a_clean_reject(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			self._as("2001:db8:dead::ffff", api.register, label="acme")
		self.assertEqual(frappe.db.count("Subdomain"), 0)

	def test_no_source_ip_is_a_clean_reject(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			self._as(None, api.register, label="acme")

	def test_terminated_vm_source_is_rejected(self) -> None:
		vm = _vm("term", "2001:db8::2", status="Terminated")
		with self.assertRaises(frappe.ValidationError):
			self._as(vm.guest_ipv6, api.register, label="acme")

	def test_proxy_vm_source_is_rejected(self) -> None:
		vm = _vm("proxy", "2001:db8::3")
		self._bind_proxy(vm.name)
		with self.assertRaises(frappe.ValidationError):
			self._as(vm.guest_ipv6, api.register, label="acme")

	def test_ambiguous_shared_ipv6_fails_closed(self) -> None:
		one = _vm("dup-1", "2001:db8::9")
		_vm("dup-2", "2001:db8::9")
		with self.assertRaises(frappe.ValidationError):
			self._as(one.guest_ipv6, api.register, label="acme")


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


class TestRegister(_ApiTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.vm = _vm("reg", "2001:db8::10")

	def test_ok_inserts_active_and_echoes_suffix(self) -> None:
		result = self._as(self.vm.guest_ipv6, api.register, label="acme")
		self.assertEqual(result, {"status": "ok", "suffix": REGION_DOMAIN})
		self.assertEqual(frappe.get_doc("Subdomain", "acme").active, 1)

	def test_reserved_label_rejected(self) -> None:
		self.assertEqual(self._as(self.vm.guest_ipv6, api.register, label="admin")["status"], "reserved")

	def test_denylisted_label_rejected(self) -> None:
		# `paypal` is a seeded brand denylist row (setup._seed_denylist).
		self.assertEqual(self._as(self.vm.guest_ipv6, api.register, label="paypal")["status"], "reserved")

	def test_invalid_label_rejected_with_reason(self) -> None:
		result = self._as(self.vm.guest_ipv6, api.register, label="Bad.Label")
		self.assertEqual(result["status"], "invalid")
		self.assertIn("dot", result["reason"].lower())

	def test_label_owned_by_another_vm_is_taken(self) -> None:
		other = _vm("other", "2001:db8::11")
		self._as(other.guest_ipv6, api.register, label="acme")
		self.assertEqual(self._as(self.vm.guest_ipv6, api.register, label="acme")["status"], "taken")

	def test_reregister_of_owned_label_is_idempotent_ok(self) -> None:
		self._as(self.vm.guest_ipv6, api.register, label="acme")
		self.assertEqual(self._as(self.vm.guest_ipv6, api.register, label="acme")["status"], "ok")
		self.assertEqual(frappe.db.count("Subdomain", {"subdomain": "acme"}), 1)

	def test_at_limit_when_cap_reached(self) -> None:
		with patch("satellite.routing.api.cap_for_vm", return_value=1):
			self.assertEqual(self._as(self.vm.guest_ipv6, api.register, label="one")["status"], "ok")
			self.assertEqual(self._as(self.vm.guest_ipv6, api.register, label="two")["status"], "at_limit")


# ---------------------------------------------------------------------------
# deregister / check_label / list
# ---------------------------------------------------------------------------


class TestDeregisterCheckList(_ApiTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.vm = _vm("d", "2001:db8::20")

	def test_deregister_deletes_own_row(self) -> None:
		self._as(self.vm.guest_ipv6, api.register, label="acme")
		self.assertEqual(self._as(self.vm.guest_ipv6, api.deregister, label="acme")["status"], "ok")
		self.assertFalse(frappe.db.exists("Subdomain", "acme"))

	def test_deregister_another_vms_row_is_a_noop(self) -> None:
		other = _vm("d2", "2001:db8::21")
		self._as(other.guest_ipv6, api.register, label="acme")
		self._as(self.vm.guest_ipv6, api.deregister, label="acme")
		self.assertTrue(frappe.db.exists("Subdomain", "acme"))  # untouched

	def test_deregister_absent_row_is_idempotent(self) -> None:
		self.assertEqual(self._as(self.vm.guest_ipv6, api.deregister, label="nope")["status"], "ok")

	def test_check_label_status_and_suffix(self) -> None:
		self.assertEqual(
			self._as(self.vm.guest_ipv6, api.check_label, label="free"),
			{"status": "ok", "suffix": REGION_DOMAIN},
		)
		self._as(self.vm.guest_ipv6, api.register, label="free")
		self.assertEqual(self._as(self.vm.guest_ipv6, api.check_label, label="free")["status"], "taken")

	def test_list_returns_own_rows_with_controller_built_fqdn(self) -> None:
		self._as(self.vm.guest_ipv6, api.register, label="acme")
		result = self._as(self.vm.guest_ipv6, api.list)
		self.assertEqual(result, {"domains": [{"label": "acme", "fqdn": f"acme.{REGION_DOMAIN}", "active": True}]})

	def test_list_empty_inventory_is_typed_empty(self) -> None:
		self.assertEqual(self._as(self.vm.guest_ipv6, api.list), {"domains": []})


# ---------------------------------------------------------------------------
# Custom domains + host-level queries
# ---------------------------------------------------------------------------


class TestCustomDomainAndHost(_ApiTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.vm = _vm("cd", "2001:db8::30")

	def test_register_custom_domain_ok(self) -> None:
		result = self._as(self.vm.guest_ipv6, api.register_custom_domain, domain="shop.acme.com")
		self.assertEqual(result["status"], "ok")
		self.assertEqual(frappe.get_doc("Custom Domain", "shop.acme.com").virtual_machine, self.vm.name)

	def test_register_custom_domain_under_wildcard_is_invalid(self) -> None:
		result = self._as(self.vm.guest_ipv6, api.register_custom_domain, domain=f"app.{REGION_DOMAIN}")
		self.assertEqual(result["status"], "invalid")

	def test_register_custom_domain_taken_by_another_vm(self) -> None:
		other = _vm("cd2", "2001:db8::31")
		self._as(other.guest_ipv6, api.register_custom_domain, domain="shop.acme.com")
		result = self._as(self.vm.guest_ipv6, api.register_custom_domain, domain="shop.acme.com")
		self.assertEqual(result["status"], "taken")

	def test_deregister_custom_domain(self) -> None:
		self._as(self.vm.guest_ipv6, api.register_custom_domain, domain="shop.acme.com")
		self._as(self.vm.guest_ipv6, api.deregister_custom_domain, domain="shop.acme.com")
		self.assertFalse(frappe.db.exists("Custom Domain", "shop.acme.com"))

	def test_dns_records_for_an_owned_site(self) -> None:
		self._bind_proxy(_vm("edge", "2001:db8::ff").name, public_ipv4="9.9.9.9")
		self._as(self.vm.guest_ipv6, api.register, label="acme")
		result = self._as(
			self.vm.guest_ipv6, api.dns_records, domain="shop.acme.com", site=f"acme.{REGION_DOMAIN}"
		)
		types = {r["type"]: r["value"] for r in result["records"]}
		self.assertEqual(types["CNAME"], f"acme.{REGION_DOMAIN}")
		self.assertEqual(types["A"], "9.9.9.9")
		self.assertEqual(types["AAAA"], "2001:db8::ff")

	def test_dns_records_for_an_unowned_site_is_rejected(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			self._as(self.vm.guest_ipv6, api.dns_records, domain="shop.acme.com", site=f"nope.{REGION_DOMAIN}")

	def test_wildcard_domains(self) -> None:
		self.assertEqual(
			self._as(self.vm.guest_ipv6, api.wildcard_domains), {"domains": [f"*.{REGION_DOMAIN}"]}
		)

	def test_proxy_servers_lists_the_fleet_addresses(self) -> None:
		self._bind_proxy(_vm("edge", "2001:db8::ff").name, public_ipv4="9.9.9.9")
		self.assertEqual(
			self._as(self.vm.guest_ipv6, api.proxy_servers), {"ips": ["9.9.9.9", "2001:db8::ff"]}
		)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAudit(_ApiTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.vm = _vm("aud", "2001:db8::40")

	def test_ok_path_writes_an_audit_row(self) -> None:
		self._as(self.vm.guest_ipv6, api.register, label="acme")
		row = frappe.get_all(
			"Bench Routing Audit",
			filters={"endpoint": "register", "label": "acme"},
			fields=["status", "business_reject", "vm", "source_ip"],
		)
		self.assertEqual(len(row), 1)
		self.assertEqual(row[0]["status"], "ok")
		self.assertEqual(row[0]["business_reject"], 0)
		self.assertEqual(row[0]["vm"], self.vm.name)

	def test_unresolved_source_writes_blank_vm_and_the_spoofing_source(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			self._as("2001:db8:dead::1", api.register, label="acme")
		row = frappe.get_all(
			"Bench Routing Audit",
			filters={"status": "unresolved"},
			fields=["vm", "source_ip", "business_reject"],
		)
		self.assertEqual(len(row), 1)
		self.assertEqual(row[0]["vm"], "")
		self.assertEqual(row[0]["source_ip"], "2001:db8:dead::1")
		self.assertEqual(row[0]["business_reject"], 1)
