"""The site deploy driver — a real bench VM is unavailable in the one-host dev setup, so
we mock the guest SSH and assert the deploy-site.py argv, the upload, and the result
parse. The readiness probe is mocked at _http_ok."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from satellite import bench

ATLAS = "bench-atlas"


def _vm(remote_id="dep-vm", build_mode="site", warm=0):
	if not frappe.db.exists("Atlas", ATLAS):
		frappe.get_doc({"doctype": "Atlas", "title": ATLAS, "base_url": "http://a.bench"}).insert(
			ignore_permissions=True
		)
	name = frappe.db.exists("Virtual Machine", {"atlas": ATLAS, "remote_id": remote_id})
	if name:
		frappe.delete_doc("Virtual Machine", name, force=1, ignore_permissions=True)
	return frappe.get_doc(
		{
			"doctype": "Virtual Machine",
			"atlas": ATLAS,
			"remote_id": remote_id,
			"guest_ipv6": "2001:db8::1",
			"build_mode": build_mode,
			"warm": warm,
		}
	).insert(ignore_permissions=True)


_RESULT = 'ATLAS_RESULT={"site": "app.blr1.frappe.dev", "serving": true, "login_url": "https://x/app?sid=1"}'


def _fake_guest(deploy_stdout=_RESULT, code=0):
	"""A run_guest that answers mkdir with '' and the python3 deploy with the result line."""

	def run_guest(vm, command, timeout=120, stdin=None):
		if command.startswith("python3"):
			return (deploy_stdout, "" if code == 0 else "boom", code)
		return ("", "", 0)

	return run_guest


class TestDeploySite(IntegrationTestCase):
	def _deploy_command(self, run_guest_mock) -> str:
		return next(c.args[1] for c in run_guest_mock.call_args_list if c.args[1].startswith("python3"))

	def test_default_site_mode_uploads_and_returns_result(self) -> None:
		vm = _vm()
		with (
			patch.object(bench, "_wait_for_ssh"),
			patch.object(bench, "scp_guest") as scp,
			patch.object(bench, "run_guest", side_effect=_fake_guest()) as run_guest,
		):
			result = bench.deploy_site(vm.name, "app.blr1.frappe.dev")
		command = self._deploy_command(run_guest)
		self.assertIn("--site-name app.blr1.frappe.dev", command)
		self.assertNotIn("--mode admin", command)
		self.assertNotIn("--warm-vm-uuid", command)
		scp.assert_called_once()
		self.assertTrue(scp.call_args.args[2].endswith("/deploy-site.py"))  # remote path
		self.assertEqual(result["login_url"], "https://x/app?sid=1")

	def test_admin_mode_and_warm_and_admin_domain(self) -> None:
		vm = _vm(build_mode="admin", warm=1)
		with (
			patch.object(bench, "_wait_for_ssh"),
			patch.object(bench, "scp_guest"),
			patch.object(bench, "run_guest", side_effect=_fake_guest()) as run_guest,
		):
			bench.deploy_site(vm.name, "app.blr1.frappe.dev", admin_domain="admin.blr1.frappe.dev")
		command = self._deploy_command(run_guest)
		self.assertIn("--mode admin", command)
		self.assertIn("--admin-domain admin.blr1.frappe.dev", command)
		self.assertIn("--warm-vm-uuid dep-vm", command)  # the Atlas uuid = remote_id, not the mirror name

	def test_central_params_ride_through(self) -> None:
		vm = _vm()
		with (
			patch.object(bench, "_wait_for_ssh"),
			patch.object(bench, "scp_guest"),
			patch.object(bench, "run_guest", side_effect=_fake_guest()) as run_guest,
		):
			bench.deploy_site(
				vm.name, "app.blr1.frappe.dev", central_endpoint="https://c/api", central_auth_token="tok"
			)
		command = self._deploy_command(run_guest)
		self.assertIn("--central-endpoint https://c/api", command)
		self.assertIn("--central-auth-token tok", command)

	def test_raises_on_nonzero_exit(self) -> None:
		vm = _vm()
		with (
			patch.object(bench, "_wait_for_ssh"),
			patch.object(bench, "scp_guest"),
			patch.object(bench, "run_guest", side_effect=_fake_guest(code=1)),
		):
			with self.assertRaises(frappe.ValidationError):
				bench.deploy_site(vm.name, "app.blr1.frappe.dev")

	def test_regenerate_login_passes_the_flag(self) -> None:
		vm = _vm()
		with (
			patch.object(bench, "_wait_for_ssh"),
			patch.object(bench, "scp_guest"),
			patch.object(bench, "run_guest", side_effect=_fake_guest()) as run_guest,
		):
			bench.regenerate_login(vm.name, "app.blr1.frappe.dev")
		command = self._deploy_command(run_guest)
		self.assertIn("--regenerate-login", command)


class TestReadinessHelpers(IntegrationTestCase):
	def test_readiness_path_for_mode(self) -> None:
		self.assertEqual(bench.readiness_path_for_mode("site"), "/api/method/ping")
		self.assertEqual(bench.readiness_path_for_mode("admin"), "/api/status")
		self.assertEqual(bench.readiness_path_for_mode(None), "/api/method/ping")

	def test_parse_result_takes_the_last_line(self) -> None:
		out = 'noise\nATLAS_RESULT={"a": 1}\nATLAS_RESULT={"a": 2}\n'
		self.assertEqual(bench._parse_result(out), {"a": 2})
		self.assertIsNone(bench._parse_result("no result here"))

	def test_wait_for_http_returns_on_200(self) -> None:
		with patch.object(bench, "_http_ok", return_value=True):
			bench.wait_for_http("2001:db8::1", "app.blr1.frappe.dev")  # does not raise

	def test_wait_for_http_raises_on_timeout(self) -> None:
		with patch.object(bench, "_http_ok", return_value=False):
			with self.assertRaises(frappe.ValidationError):
				bench.wait_for_http("2001:db8::1", "app.blr1.frappe.dev", timeout_seconds=0)
