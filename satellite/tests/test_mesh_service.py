"""MeshService unit tier (spec/28 §5.1) — pure logic, milliseconds, no host.

Covers the decisions the service makes before any infra runs: which VMs it attaches
to, the validation rule it enforces, the provision var it injects, and the mesh
address it derives. The host effect (satellite-mesh-add/remove) is proven in the
faithful-double tier (`test_seam_integration`)."""

import frappe
from frappe.tests import IntegrationTestCase

from satellite.services.mesh import MeshService


class TestMeshService(IntegrationTestCase):
	def setUp(self) -> None:
		self.svc = MeshService()

	def test_applies_to_reads_the_satellite_flag(self) -> None:
		self.assertTrue(self.svc.applies_to(frappe._dict(satellite_managed=1)))
		self.assertFalse(self.svc.applies_to(frappe._dict(satellite_managed=0)))
		self.assertFalse(self.svc.applies_to(frappe._dict()))

	def test_validate_requires_a_tenant(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			self.svc.validate(frappe._dict(satellite_managed=1, tenant=None))
		# With a tenant, validate passes silently.
		self.svc.validate(frappe._dict(satellite_managed=1, tenant="tenant-1"))

	def test_provision_variables_is_empty_host_plane_only(self) -> None:
		# The minimal mesh registry is host-plane, so it injects no provision-time guest
		# var (and must not — provision-vm is a strict typed CLI). The seam still calls it.
		vm = frappe._dict(private_address="fdaa::1", ipv6_address="2001:db8::1", name="vm-1")
		self.assertEqual(self.svc.provision_variables(vm), {})

	def test_peer_address_prefers_private_then_public_then_name(self) -> None:
		self.assertEqual(self.svc.peer_address(frappe._dict(private_address="fdaa::9")), "fdaa::9")
		self.assertEqual(self.svc.peer_address(frappe._dict(ipv6_address="2001:db8::9")), "2001:db8::9")
		self.assertEqual(self.svc.peer_address(frappe._dict(name="vm-x")), "vm-x")
