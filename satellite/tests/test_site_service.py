"""The `site` service handler — binding it deploys the site (mocked) and gates Applied on
readiness; unbinding is a clean no-op."""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

ATLAS = "site-svc-atlas"


def _vm(build_mode="site"):
	if not frappe.db.exists("Atlas", ATLAS):
		frappe.get_doc({"doctype": "Atlas", "title": ATLAS, "base_url": "http://a.site"}).insert(
			ignore_permissions=True
		)
	name = frappe.db.exists("Virtual Machine", {"atlas": ATLAS, "remote_id": "svc-vm"})
	if name:
		frappe.delete_doc("Virtual Machine", name, force=1, ignore_permissions=True)
	return frappe.get_doc(
		{
			"doctype": "Virtual Machine",
			"atlas": ATLAS,
			"remote_id": "svc-vm",
			"guest_ipv6": "2001:db8::1",
			"build_mode": build_mode,
		}
	).insert(ignore_permissions=True)


class TestSiteService(IntegrationTestCase):
	def setUp(self) -> None:
		for name in frappe.get_all("Service Binding", pluck="name"):
			frappe.delete_doc("Service Binding", name, force=1, ignore_permissions=True)
		self.vm = _vm()

	def _bind(self, config: dict):
		return frappe.get_doc(
			{
				"doctype": "Service Binding",
				"virtual_machine": self.vm.name,
				"service": "site",
				"config": json.dumps(config),
			}
		).insert(ignore_permissions=True)

	def test_apply_deploys_then_waits_for_readiness(self) -> None:
		with (
			patch("satellite.services.site.deploy_site") as deploy,
			patch("satellite.services.site.wait_for_http") as wait,
		):
			binding = self._bind({"fqdn": "app.blr1.frappe.dev"})
		deploy.assert_called_once()
		self.assertEqual(deploy.call_args.args, (self.vm.name, "app.blr1.frappe.dev"))
		# Readiness probed on the guest /128 with the site path.
		self.assertEqual(wait.call_args.args, (self.vm.guest_ipv6, "app.blr1.frappe.dev"))
		self.assertEqual(wait.call_args.kwargs["path"], "/api/method/ping")
		self.assertEqual(
			frappe.db.get_value("Service Binding", binding.name, "binding_status"), "Applied"
		)

	def test_apply_forwards_central_and_admin_params(self) -> None:
		with (
			patch("satellite.services.site.deploy_site") as deploy,
			patch("satellite.services.site.wait_for_http"),
		):
			self._bind(
				{
					"fqdn": "app.blr1.frappe.dev",
					"mode": "admin",
					"admin_domain": "admin.blr1.frappe.dev",
					"central_endpoint": "https://c/api",
					"central_auth_token": "tok",
				}
			)
		kwargs = deploy.call_args.kwargs
		self.assertEqual(kwargs["mode"], "admin")
		self.assertEqual(kwargs["admin_domain"], "admin.blr1.frappe.dev")
		self.assertEqual(kwargs["central_endpoint"], "https://c/api")
		self.assertEqual(kwargs["central_auth_token"], "tok")

	def test_apply_without_fqdn_marks_failed(self) -> None:
		with (
			patch("satellite.services.site.deploy_site") as deploy,
			patch("satellite.services.site.wait_for_http"),
		):
			binding = self._bind({})
		deploy.assert_not_called()
		self.assertEqual(
			frappe.db.get_value("Service Binding", binding.name, "binding_status"), "Failed"
		)
		self.assertIn("fqdn", frappe.db.get_value("Service Binding", binding.name, "last_error"))

	def test_withdraw_is_a_noop(self) -> None:
		with (
			patch("satellite.services.site.deploy_site"),
			patch("satellite.services.site.wait_for_http"),
		):
			binding = self._bind({"fqdn": "app.blr1.frappe.dev"})
		# Deleting the binding fires withdraw; it must not raise.
		frappe.delete_doc("Service Binding", binding.name, force=1, ignore_permissions=True)
		self.assertFalse(frappe.db.exists("Service Binding", binding.name))
