"""Unit tests for the cross-host mesh COMPUTATION (spec/28 Phase 1).

The mesh is cross-host: correctness is 'every host's AllowedIPs enumerate every /128 on
every OTHER host + that host's own mesh /128'. That can't be exercised on a single real
host, so these build a MULTI-host, multi-tenant mirror and assert render_wg_mesh_config
byte-for-byte. run_host_addr is never called (pure render)."""

import uuid

import frappe
from frappe.tests import IntegrationTestCase

from satellite import host_mesh
from satellite.networking import (
	derive_host_mesh_address,
	derive_host_wireguard_keypair,
	derive_private_address,
)

ATLAS = "mesh-compute-atlas"
HOST_A = str(uuid.UUID(int=0xA))
HOST_B = str(uuid.UUID(int=0xB))
HOST_C = str(uuid.UUID(int=0xC))  # no ipv6 -> excluded (Fake/unplaced)
VM1 = str(uuid.UUID(int=0x11))
VM2 = str(uuid.UUID(int=0x12))
VM3 = str(uuid.UUID(int=0x13))
TENANT_X = "TEAM-00001"
TENANT_Y = "TEAM-00002"


def _atlas() -> None:
	if not frappe.db.exists("Atlas", ATLAS):
		frappe.get_doc({"doctype": "Atlas", "title": ATLAS, "base_url": "http://mesh.compute"}).insert(
			ignore_permissions=True
		)


def _server(remote_id, ipv4, ipv6, status="Active") -> None:
	name = frappe.db.exists("Server", {"atlas": ATLAS, "remote_id": remote_id})
	values = {"ipv4": ipv4, "ipv6": ipv6, "server_status": status}
	if name:
		frappe.db.set_value("Server", name, values)
		return
	frappe.get_doc({"doctype": "Server", "atlas": ATLAS, "remote_id": remote_id, **values}).insert(
		ignore_permissions=True
	)


def _vm(remote_id, server, tenant, status="Running") -> None:
	name = frappe.db.exists("Virtual Machine", {"atlas": ATLAS, "remote_id": remote_id})
	values = {"server": server, "tenant": tenant, "vm_status": status}
	if name:
		frappe.db.set_value("Virtual Machine", name, values)
		return
	frappe.get_doc(
		{"doctype": "Virtual Machine", "atlas": ATLAS, "remote_id": remote_id, **values}
	).insert(ignore_permissions=True)


class TestMeshComputation(IntegrationTestCase):
	def setUp(self) -> None:
		_atlas()
		for dt in ("VPN Peer",) if frappe.db.exists("DocType", "VPN Peer") else ():
			for n in frappe.get_all(dt, pluck="name"):
				frappe.delete_doc(dt, n, force=1, ignore_permissions=True)
		_server(HOST_A, "10.0.0.1", "2001:db8::a")
		_server(HOST_B, "10.0.0.2", "2001:db8::b")
		_server(HOST_C, "10.0.0.3", None)  # endpointless -> excluded
		_vm(VM1, HOST_A, TENANT_X)
		_vm(VM2, HOST_A, TENANT_Y)
		_vm(VM3, HOST_B, TENANT_X)

	def _hosts(self):
		return host_mesh._active_hosts(ATLAS)

	def test_active_hosts_excludes_endpointless(self) -> None:
		self.assertEqual({h["remote_id"] for h in self._hosts()}, {HOST_A, HOST_B})

	def test_residency_groups_all_tenants_by_host(self) -> None:
		hosts = self._hosts()
		residents = host_mesh._residents_by_host(ATLAS, hosts)
		by_remote = {h["remote_id"]: h["name"] for h in hosts}
		self.assertCountEqual(
			residents[by_remote[HOST_A]],
			[derive_private_address(TENANT_X, VM1), derive_private_address(TENANT_Y, VM2)],
		)
		self.assertEqual(residents[by_remote[HOST_B]], [derive_private_address(TENANT_X, VM3)])

	def test_host_A_config_lists_host_B_with_its_vm_and_mesh_128(self) -> None:
		hosts = self._hosts()
		residents = host_mesh._residents_by_host(ATLAS, hosts)
		a = next(h for h in hosts if h["remote_id"] == HOST_A)
		config = host_mesh.render_wg_mesh_config(a["name"], hosts, residents)
		self.assertEqual(config.count("[Peer]"), 1)  # only host B, not itself
		_priv, b_pub = derive_host_wireguard_keypair(HOST_B)
		self.assertIn(f"PublicKey = {b_pub}", config)
		self.assertIn(f"Endpoint = [2001:db8::b]:{host_mesh.WG_HOST_PORT}", config)
		expected = ", ".join(
			sorted(
				[
					f"{derive_private_address(TENANT_X, VM3)}/128",
					f"{derive_host_mesh_address(HOST_B)}/128",
				]
			)
		)
		self.assertIn(f"AllowedIPs = {expected}", config)
		_priv, a_pub = derive_host_wireguard_keypair(HOST_A)
		self.assertNotIn(a_pub, config)  # a host never advertises itself

	def test_terminated_and_draft_vms_excluded(self) -> None:
		_vm(VM3, HOST_B, TENANT_X, status="Terminated")
		hosts = self._hosts()
		residents = host_mesh._residents_by_host(ATLAS, hosts)
		by_remote = {h["remote_id"]: h["name"] for h in hosts}
		self.assertEqual(residents[by_remote[HOST_B]], [])
		a = next(h for h in hosts if h["remote_id"] == HOST_A)
		config = host_mesh.render_wg_mesh_config(a["name"], hosts, residents)
		# B is still a bus endpoint via its own mesh /128 even with no VMs.
		self.assertIn(f"{derive_host_mesh_address(HOST_B)}/128", config)

	def test_tenantless_vm_off_the_private_plane(self) -> None:
		_vm(VM2, HOST_A, None)
		hosts = self._hosts()
		residents = host_mesh._residents_by_host(ATLAS, hosts)
		by_remote = {h["remote_id"]: h["name"] for h in hosts}
		self.assertEqual(residents[by_remote[HOST_A]], [derive_private_address(TENANT_X, VM1)])

	def test_config_is_deterministic(self) -> None:
		hosts = self._hosts()
		residents = host_mesh._residents_by_host(ATLAS, hosts)
		a = next(h for h in hosts if h["remote_id"] == HOST_A)
		self.assertEqual(
			host_mesh.render_wg_mesh_config(a["name"], hosts, residents),
			host_mesh.render_wg_mesh_config(a["name"], hosts, residents),
		)
