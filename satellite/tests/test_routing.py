"""Phase-2 routing: the pure computation (canonical maps, labels, ports, region) and
the fleet reconcile. The proxy SSH itself is unverifiable in the one-host dev setup, so
we mock `run_guest` and assert the reconcile's read/compare/sync-on-drift decision and
the byte-identical bodies it would push."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from satellite.routing.desired import (
	canonical_json,
	custom_domain_acme_map,
	custom_domain_sni_map,
	routing_address,
	subdomain_map,
)
from satellite.routing.labels import (
	normalize,
	normalize_domain,
	validate_custom_domain,
	validate_label,
)
from satellite.routing.ports import allocate_port, port_map
from satellite.routing.region import active_region_domain
from satellite.services.routing import (
	RoutingService,
	_proxy_vms,
	_reconcile_proxy,
	reconcile_fleet,
)

ATLAS = "routing-atlas"


def _setup_atlas() -> None:
	if not frappe.db.exists("Atlas", ATLAS):
		frappe.get_doc({"doctype": "Atlas", "title": ATLAS, "base_url": "http://a.routing"}).insert(
			ignore_permissions=True
		)


def _vm(remote_id: str, *, guest_ipv6: str | None = None, private_address: str | None = None):
	name = frappe.db.exists("Virtual Machine", {"atlas": ATLAS, "remote_id": remote_id})
	if name:
		return frappe.get_doc("Virtual Machine", name)
	return frappe.get_doc(
		{
			"doctype": "Virtual Machine",
			"atlas": ATLAS,
			"remote_id": remote_id,
			"guest_ipv6": guest_ipv6,
			"private_address": private_address,
		}
	).insert(ignore_permissions=True)


def _region(domain: str = "blr1.frappe.dev", region: str = "blr1") -> None:
	for name in frappe.get_all("Region Domain", pluck="name"):
		frappe.delete_doc("Region Domain", name, force=1, ignore_permissions=True)
	frappe.get_doc(
		{"doctype": "Region Domain", "domain": domain, "region": region, "is_active": 1}
	).insert(ignore_permissions=True)


def _clear(*doctypes: str) -> None:
	for doctype in doctypes:
		for name in frappe.get_all(doctype, pluck="name"):
			frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)


class TestCanonicalAndLabels(IntegrationTestCase):
	"""Pure functions — no DB."""

	def test_canonical_json_is_sorted_indented_and_newline_terminated(self) -> None:
		body = canonical_json({"b": "2", "a": "1"})
		self.assertEqual(body, '{\n  "a": "1",\n  "b": "2"\n}\n')

	def test_canonical_json_empty_map(self) -> None:
		self.assertEqual(canonical_json({}), "{}\n")

	def test_normalize_strips_but_preserves_case(self) -> None:
		self.assertEqual(normalize("  Acme  "), "Acme")

	def test_validate_label_rejects_dots_case_hyphens_length(self) -> None:
		for bad in ("", "a.b", "Acme", "-lead", "trail-", "x" * 64, "under_score", "sp ace"):
			with self.assertRaises(frappe.ValidationError):
				validate_label(bad)

	def test_validate_label_accepts_a_clean_label(self) -> None:
		validate_label("acme-42")  # does not raise

	def test_normalize_domain_lowercases_and_trims_trailing_dot(self) -> None:
		self.assertEqual(normalize_domain("  Shop.ACME.com.  "), "shop.acme.com")

	def test_validate_custom_domain_requires_a_dot(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			validate_custom_domain("bareword", "blr1.frappe.dev")

	def test_validate_custom_domain_rejects_a_name_under_the_wildcard(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			validate_custom_domain("app.blr1.frappe.dev", "blr1.frappe.dev")

	def test_validate_custom_domain_accepts_an_external_fqdn(self) -> None:
		validate_custom_domain("shop.acme.com", "blr1.frappe.dev")  # does not raise


class TestRegion(IntegrationTestCase):
	def setUp(self) -> None:
		_clear("Region Domain")

	def test_no_active_region_throws(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			active_region_domain()

	def test_single_active_region_resolves(self) -> None:
		_region()
		self.assertEqual(active_region_domain().domain, "blr1.frappe.dev")

	def test_several_active_regions_throw(self) -> None:
		frappe.get_doc(
			{"doctype": "Region Domain", "domain": "a.frappe.dev", "region": "a", "is_active": 1}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{"doctype": "Region Domain", "domain": "b.frappe.dev", "region": "b", "is_active": 1}
		).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			active_region_domain()


class TestRoutingAddress(IntegrationTestCase):
	def setUp(self) -> None:
		_setup_atlas()

	def test_prefers_public_guest_ipv6(self) -> None:
		vm = _vm("addr-public", guest_ipv6="2001:db8::1", private_address="fdaa::1")
		self.assertEqual(routing_address(vm.name), "2001:db8::1")

	def test_falls_back_to_private_for_a_dark_vm(self) -> None:
		vm = _vm("addr-dark", guest_ipv6=None, private_address="fdaa::9")
		self.assertEqual(routing_address(vm.name), "fdaa::9")

	def test_unaddressable_vm_throws(self) -> None:
		vm = _vm("addr-none", guest_ipv6=None, private_address=None)
		with self.assertRaises(frappe.ValidationError):
			routing_address(vm.name)


class TestDesiredMaps(IntegrationTestCase):
	def setUp(self) -> None:
		_setup_atlas()
		_region()
		_clear("Subdomain", "Custom Domain", "Port Mapping")
		self.vm = _vm("maps-vm", guest_ipv6="2001:db8::42")

	def test_subdomain_map_has_only_active_rows(self) -> None:
		frappe.get_doc(
			{"doctype": "Subdomain", "subdomain": "live", "virtual_machine": self.vm.name, "active": 1}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{"doctype": "Subdomain", "subdomain": "off", "virtual_machine": self.vm.name, "active": 0}
		).insert(ignore_permissions=True)
		self.assertEqual(subdomain_map(), {"live": "2001:db8::42"})

	def test_subdomain_denormalizes_the_vm_address(self) -> None:
		doc = frappe.get_doc(
			{"doctype": "Subdomain", "subdomain": "app", "virtual_machine": self.vm.name}
		).insert(ignore_permissions=True)
		self.assertEqual(doc.address, "2001:db8::42")

	def test_subdomain_key_and_vm_are_immutable(self) -> None:
		doc = frappe.get_doc(
			{"doctype": "Subdomain", "subdomain": "lock", "virtual_machine": self.vm.name}
		).insert(ignore_permissions=True)
		doc.subdomain = "renamed"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_custom_domain_sni_and_acme_maps(self) -> None:
		frappe.get_doc(
			{
				"doctype": "Custom Domain",
				"domain": "shop.acme.com",
				"virtual_machine": self.vm.name,
				"status": "Active",
				"active": 1,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(custom_domain_sni_map(), {"shop.acme.com": "[2001:db8::42]:443"})
		self.assertEqual(custom_domain_acme_map(), {"shop.acme.com": "[2001:db8::42]"})


class TestPortAllocation(IntegrationTestCase):
	def setUp(self) -> None:
		_setup_atlas()
		_clear("Port Mapping")
		self.vm = _vm("ports-vm", guest_ipv6="2001:db8::7")

	def test_allocates_lowest_free_port(self) -> None:
		self.assertEqual(allocate_port(), 10000)

	def _map(self, target_port: int, protocol: str = "tcp"):
		return frappe.get_doc(
			{
				"doctype": "Port Mapping",
				"protocol": protocol,
				"virtual_machine": self.vm.name,
				"target_port": target_port,
			}
		).insert(ignore_permissions=True)

	def test_second_mapping_skips_the_taken_port_and_names_by_protocol_port(self) -> None:
		first = self._map(22, "ssh")
		self.assertEqual(first.public_port, 10000)
		self.assertEqual(first.name, "ssh-10000")
		second = self._map(3306, "mariadb")
		self.assertEqual(second.public_port, 10001)

	def test_port_map_is_ready_to_dial_literals(self) -> None:
		self._map(3306, "mariadb")
		self.assertEqual(port_map(), {"10000": "[2001:db8::7]:3306"})


class TestDenylist(IntegrationTestCase):
	def test_seeded_brand_is_denylisted(self) -> None:
		from satellite.satellite.doctype.subdomain_denylist.subdomain_denylist import is_denylisted

		self.assertTrue(is_denylisted("paypal"))
		self.assertTrue(is_denylisted("PayPal"))  # case-folded
		self.assertFalse(is_denylisted("totally-fine-label"))

	def test_disabled_row_lifts_the_block(self) -> None:
		from satellite.satellite.doctype.subdomain_denylist.subdomain_denylist import is_denylisted

		frappe.db.set_value("Subdomain Denylist", "stripe", "enabled", 0)
		self.assertFalse(is_denylisted("stripe"))
		frappe.db.set_value("Subdomain Denylist", "stripe", "enabled", 1)


class TestReconcile(IntegrationTestCase):
	def setUp(self) -> None:
		_setup_atlas()
		_region()
		_clear("Subdomain", "Custom Domain", "Port Mapping", "Service Binding")
		self.vm = _vm("recon-site", guest_ipv6="2001:db8::100")
		self.proxy = _vm("recon-proxy", guest_ipv6="2001:db8::ff")

	def _bind_proxy(self):
		with patch("satellite.services.routing.run_guest", return_value=("{}\n", "", 0)):
			return frappe.get_doc(
				{"doctype": "Service Binding", "virtual_machine": self.proxy.name, "service": "routing-proxy"}
			).insert(ignore_permissions=True)

	def test_proxy_vms_are_the_applied_routing_proxy_bindings(self) -> None:
		self._bind_proxy()
		self.assertEqual(_proxy_vms(), [self.proxy.name])

	def test_reconcile_no_op_when_live_matches_desired(self) -> None:
		# Empty fleet-side maps ("{}\n") match the empty desired maps → no write.
		with patch("satellite.services.routing.run_guest", return_value=("{}\n", "", 0)) as run_guest:
			drifted = _reconcile_proxy(self.proxy.name, {"sites": "{}\n", "sni": "{}\n", "acme": "{}\n", "ports": "{}\n"})
		self.assertFalse(drifted)
		# Four reads, zero writes.
		self.assertEqual(run_guest.call_count, 4)

	def test_reconcile_syncs_on_drift(self) -> None:
		frappe.get_doc(
			{"doctype": "Subdomain", "subdomain": "app", "virtual_machine": self.vm.name}
		).insert(ignore_permissions=True)
		desired_sites = canonical_json({"app": "2001:db8::100"})

		# The guest's live `sites` map is empty → drift → one write with the desired body.
		def fake_run_guest(vm, command, timeout=120, stdin=None):
			if "POST" in command and "/sync" in command:
				return ("ok", "", 0)
			return ("{}\n", "", 0)  # every GET reads empty

		with patch("satellite.services.routing.run_guest", side_effect=fake_run_guest) as run_guest:
			drifted = _reconcile_proxy(
				self.proxy.name,
				{"sites": desired_sites, "sni": "{}\n", "acme": "{}\n", "ports": "{}\n"},
			)
		self.assertTrue(drifted)
		# The sites write carried the canonical desired body on stdin.
		writes = [c for c in run_guest.call_args_list if "/sync" in c.args[1] and "POST" in c.args[1]]
		self.assertEqual(len(writes), 1)
		self.assertEqual(writes[0].kwargs["stdin"], desired_sites)

	def test_reconcile_raises_on_rejected_stream_sync(self) -> None:
		def fake_run_guest(vm, command, timeout=120, stdin=None):
			if command.endswith("SYNC-SNI"):
				return ("error: incomplete body", "", 0)  # stream-admin exits 0 but reports an error
			return ("{}\n", "", 0)

		with patch("satellite.services.routing.run_guest", side_effect=fake_run_guest):
			with self.assertRaises(frappe.ValidationError):
				_reconcile_proxy(
					self.proxy.name,
					{"sites": "{}\n", "sni": canonical_json({"x": "[::1]:443"}), "acme": "{}\n", "ports": "{}\n"},
				)

	def test_routing_service_withdraw_tears_down_routes(self) -> None:
		frappe.get_doc(
			{"doctype": "Subdomain", "subdomain": "gone", "virtual_machine": self.vm.name}
		).insert(ignore_permissions=True)
		binding = frappe.get_doc(
			{"doctype": "Service Binding", "virtual_machine": self.vm.name, "service": "routing"}
		).insert(ignore_permissions=True)
		vm = frappe.get_doc("Virtual Machine", self.vm.name)
		with patch("satellite.services.routing.enqueue_reconcile"):
			RoutingService().withdraw(vm, binding)
		self.assertFalse(frappe.db.exists("Subdomain", {"virtual_machine": self.vm.name}))

	def test_proxy_binding_apply_reconciles_the_new_proxy(self) -> None:
		with patch("satellite.services.routing.reconcile_proxy") as reconcile_proxy:
			binding = self._bind_proxy()
		reconcile_proxy.assert_called_once_with(self.proxy.name)
		self.assertEqual(
			frappe.db.get_value("Service Binding", binding.name, "binding_status"), "Applied"
		)

	def test_reconcile_fleet_isolates_a_wedged_proxy(self) -> None:
		self._bind_proxy()
		with patch("satellite.services.routing.run_guest", side_effect=RuntimeError("wedged")):
			synced = reconcile_fleet()  # must not raise
		self.assertEqual(synced, [])
