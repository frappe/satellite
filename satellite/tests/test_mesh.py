from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

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
		{
			"doctype": "Virtual Machine",
			"atlas": ATLAS,
			"remote_id": "vm-mesh",
			"server_ipv4": "9.9.9.9",
			"private_address": "fdaa::7",
		}
	).insert(ignore_permissions=True)


class TestMeshBinding(IntegrationTestCase):
	def setUp(self) -> None:
		_setup()
		for name in frappe.get_all("Service Binding", pluck="name"):
			frappe.delete_doc("Service Binding", name, force=1, ignore_permissions=True)
		self.vm = _vm()

	def _bind(self):
		return frappe.get_doc(
			{"doctype": "Service Binding", "virtual_machine": self.vm.name, "service": "mesh"}
		).insert(ignore_permissions=True)

	def test_apply_publishes_peer_on_the_host(self) -> None:
		with patch("satellite.services.mesh.run_host", return_value=("", "", 0)) as run_host:
			binding = self._bind()
		run_host.assert_called_once()
		vm_name, command = run_host.call_args[0][0], run_host.call_args[0][1]
		self.assertEqual(vm_name, self.vm.name)  # SSHes THIS VM's host
		self.assertIn(self.vm.remote_id, command)
		self.assertIn("fdaa::7", command)  # the derived private address as the peer
		self.assertEqual(frappe.db.get_value("Service Binding", binding.name, "binding_status"), "Applied")

	def test_withdraw_on_delete(self) -> None:
		with patch("satellite.services.mesh.run_host", return_value=("", "", 0)):
			binding = self._bind()
		with patch("satellite.services.mesh.run_host", return_value=("", "", 0)) as run_host:
			frappe.delete_doc("Service Binding", binding.name, force=1, ignore_permissions=True)
		run_host.assert_called_once()
		self.assertIn("sed", run_host.call_args[0][1])

	def test_apply_failure_marks_binding_failed(self) -> None:
		with patch("satellite.services.mesh.run_host", return_value=("", "boom", 1)):
			binding = self._bind()
		self.assertEqual(frappe.db.get_value("Service Binding", binding.name, "binding_status"), "Failed")
		self.assertIn("boom", frappe.db.get_value("Service Binding", binding.name, "last_error"))
