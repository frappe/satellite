from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from satellite import ssh


def _atlas() -> str:
	if not frappe.db.exists("Atlas", "ssh-atlas"):
		frappe.get_doc({"doctype": "Atlas", "title": "ssh-atlas", "base_url": "http://a.ssh"}).insert(
			ignore_permissions=True
		)
	return "ssh-atlas"


class TestSsh(IntegrationTestCase):
	def setUp(self) -> None:
		atlas = _atlas()
		self.vm = frappe.db.exists("Virtual Machine", {"atlas": atlas, "remote_id": "vm-ssh"})
		if not self.vm:
			self.vm = frappe.get_doc(
				{
					"doctype": "Virtual Machine",
					"atlas": atlas,
					"remote_id": "vm-ssh",
					"server_ipv4": "10.1.2.3",
					"guest_ipv6": "2001:db8::9",
				}
			).insert(ignore_permissions=True).name

	def test_run_host_targets_the_host_ipv4(self) -> None:
		proc = MagicMock(stdout="ok", stderr="", returncode=0)
		with (
			patch.object(ssh, "_private_key_path", return_value="/tmp/sat_key"),
			patch("subprocess.run", return_value=proc) as run,
		):
			out, _err, code = ssh.run_host(self.vm, "echo hi")
		argv = run.call_args[0][0]
		self.assertIn("root@10.1.2.3", argv)
		self.assertIn("/tmp/sat_key", argv)
		self.assertEqual((out, code), ("ok", 0))

	def test_run_guest_targets_the_guest_ipv6(self) -> None:
		proc = MagicMock(stdout="", stderr="", returncode=0)
		with (
			patch.object(ssh, "_private_key_path", return_value="/tmp/sat_key"),
			patch("subprocess.run", return_value=proc) as run,
		):
			ssh.run_guest(self.vm, "echo hi")
		self.assertIn("root@2001:db8::9", run.call_args[0][0])

	def test_missing_target_raises(self) -> None:
		bare = frappe.get_doc(
			{"doctype": "Virtual Machine", "atlas": _atlas(), "remote_id": "vm-no-host"}
		).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			ssh.run_host(bare.name, "echo hi")
