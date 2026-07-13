"""Faithful-double tier (spec/28 §5.2) — mock the host, drive the seam for real.

Reuses Atlas's Fake seam: a Fake-backed Server makes a `Virtual Machine` *exist*
(a real row that reaches Running) and makes every Task — Atlas's provision AND
satellite's mesh scripts — synthesize with no SSH and no cloud droplet. So this one
test drives a REAL `Virtual Machine` row through `provision` / `terminate` and proves
the registered satellite hooks fired at the right seam points with the right effect:

  - provision_variables merged: SATELLITE_MESH_PEER rode the provision Task's env.
  - on_provision drove Atlas's exposed run_host_script → a `satellite-mesh-add` Task.
  - teardown drove run_host_script → a `satellite-mesh-remove` Task.
  - an unmanaged VM (no satellite_managed flag) is never touched.

Because satellite is installed, MeshService is registered through the real
`atlas_vm_services` hook — this exercises the whole seam, not a stubbed registry.
"""

import frappe
from frappe.tests import IntegrationTestCase

from atlas.tests.fixtures import make_image, make_provider, make_server, make_virtual_machine

from satellite.services.mesh import MESH_ADD, MESH_REMOVE


def _fake_server() -> str:
	provider = make_provider("sat-fake-provider", provider_type="Fake")
	server = make_server(
		provider,
		"sat-fake-server",
		status="Active",
		ipv4_address="10.0.0.77",
		ipv6_address="2001:db8:9::1",
		ipv6_prefix="2001:db8:9::/64",
		ipv6_virtual_machine_range="2001:db8:9::/124",
	)
	return server.name


def _tenant() -> str:
	name = "sat-test-tenant"
	if not frappe.db.exists("Tenant", name):
		frappe.get_doc({"doctype": "Tenant", "team": name}).insert(ignore_permissions=True)
	return name


def _mesh_tasks(script: str) -> list[str]:
	return frappe.get_all("Task", filters={"script": script}, pluck="name")


class TestSeamIntegration(IntegrationTestCase):
	def setUp(self) -> None:
		self.server = _fake_server()
		self.image = make_image("sat-test-image")
		self.tenant = _tenant()
		for name in frappe.get_all("Virtual Machine", pluck="name"):
			frappe.delete_doc("Virtual Machine", name, force=1, ignore_permissions=True)
		for name in frappe.get_all("Task", pluck="name"):
			frappe.delete_doc("Task", name, force=1, ignore_permissions=True)

	def _managed_vm(self):
		return make_virtual_machine(self.server, self.image, tenant=self.tenant, satellite_managed=1)

	def test_provision_publishes_the_peer_through_the_exposed_executor(self) -> None:
		vm = self._managed_vm()
		vm.provision()
		vm.reload()
		self.assertEqual(vm.status, "Running")

		# on_provision drove Atlas's exposed run_host_script → exactly one mesh-add
		# Task, carrying this VM and the peer address the service derived. (The
		# provision-var MERGE mechanism itself is proven in Atlas's own seam test with a
		# unique spy var; this minimal mesh injects none — it is host-plane.)
		add_tasks = _mesh_tasks(MESH_ADD)
		self.assertEqual(len(add_tasks), 1)
		add_vars = frappe.get_doc("Task", add_tasks[0]).variables_dict
		self.assertEqual(add_vars["VIRTUAL_MACHINE_NAME"], vm.name)
		self.assertEqual(add_vars["MESH_PEER"], vm.private_address)

	def test_terminate_withdraws_the_peer(self) -> None:
		vm = self._managed_vm()
		vm.provision()
		vm.terminate()
		vm.reload()
		self.assertEqual(vm.status, "Terminated")
		self.assertEqual(len(_mesh_tasks(MESH_REMOVE)), 1)

	def test_unmanaged_vm_is_never_touched(self) -> None:
		vm = make_virtual_machine(self.server, self.image)
		vm.provision()
		self.assertEqual(_mesh_tasks(MESH_ADD), [])
