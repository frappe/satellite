from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from satellite.services import mesh

ATLAS = "mesh-atlas"


def _setup() -> None:
	if not frappe.db.exists("Atlas", ATLAS):
		frappe.get_doc({"doctype": "Atlas", "title": ATLAS, "base_url": "http://a.mesh"}).insert(
			ignore_permissions=True
		)
	if not frappe.db.exists("Service", "mesh"):
		frappe.get_doc(
			{
				"doctype": "Service",
				"service_key": "mesh",
				"handler_path": "satellite.services.mesh.MeshService",
			}
		).insert(ignore_permissions=True)


def _vm():
	name = frappe.db.exists("Virtual Machine", {"atlas": ATLAS, "remote_id": "vm-mesh"})
	if name:
		return frappe.get_doc("Virtual Machine", name)
	return frappe.get_doc(
		{"doctype": "Virtual Machine", "atlas": ATLAS, "remote_id": "vm-mesh", "server_ipv4": "9.9.9.9"}
	).insert(ignore_permissions=True)


class TestMeshBinding(IntegrationTestCase):
	"""The mesh handler now drives the REAL cross-host reconcile (host_mesh) for the VM's
	Atlas. Binding a VM reconciles the whole fabric; unbinding reconciles again (the row is
	gone, so residency excludes it). The reconcile itself is unit-tested in test_host_mesh."""

	def setUp(self) -> None:
		_setup()
		for name in frappe.get_all("Service Binding", pluck="name"):
			frappe.delete_doc("Service Binding", name, force=1, ignore_permissions=True)
		self.vm = _vm()

	def _bind(self):
		return frappe.get_doc(
			{"doctype": "Service Binding", "virtual_machine": self.vm.name, "service": "mesh"}
		).insert(ignore_permissions=True)

	def test_apply_reconciles_the_atlas_mesh(self) -> None:
		with patch("satellite.services.mesh.reconcile_host_mesh", return_value=[]) as reconcile:
			binding = self._bind()
		reconcile.assert_called_once_with(ATLAS)
		self.assertEqual(frappe.db.get_value("Service Binding", binding.name, "binding_status"), "Applied")

	def test_withdraw_reconciles_on_delete(self) -> None:
		with patch("satellite.services.mesh.reconcile_host_mesh", return_value=[]):
			binding = self._bind()
		with patch("satellite.services.mesh.reconcile_host_mesh", return_value=[]) as reconcile:
			frappe.delete_doc("Service Binding", binding.name, force=1, ignore_permissions=True)
		reconcile.assert_called_once_with(ATLAS)

	def test_apply_failure_marks_binding_failed(self) -> None:
		with patch("satellite.services.mesh.reconcile_host_mesh", side_effect=RuntimeError("partition")):
			binding = self._bind()
		self.assertEqual(frappe.db.get_value("Service Binding", binding.name, "binding_status"), "Failed")
		self.assertIn("partition", frappe.db.get_value("Service Binding", binding.name, "last_error"))
