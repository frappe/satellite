"""Golden-vector parity for the ported derivations (spec/28 Phase 1).

satellite/networking.py is a frozen mirror of atlas/atlas/networking.py — the mesh only
works if every derived key/address is bit-identical to the Atlas fabric. These vectors
were captured by running BOTH modules on fixed UUIDs and asserting equality; they pin the
port so a well-meaning edit that drifts a byte fails here instead of silently mis-routing.
"""

import uuid

from frappe.tests import IntegrationTestCase

from satellite import networking

HOST = str(uuid.UUID(int=0xABCD1234))
VM = str(uuid.UUID(int=0x11))
TENANT = "TEAM-00042"
PEER = str(uuid.UUID(int=0x99))


class TestDerivationParity(IntegrationTestCase):
	def test_tenant_prefix(self) -> None:
		self.assertEqual(networking.derive_tenant_prefix(TENANT), "fdaa:3d4f:3f30::/48")

	def test_private_address(self) -> None:
		self.assertEqual(
			networking.derive_private_address(TENANT, VM), "fdaa:3d4f:3f30:0:da0e:df19:4d4a:8986"
		)

	def test_host_mesh_address(self) -> None:
		self.assertEqual(networking.derive_host_mesh_address(HOST), "fdaa:0:0:22a8::1")

	def test_host_wireguard_keypair(self) -> None:
		private_key, public_key = networking.derive_host_wireguard_keypair(HOST)
		self.assertEqual(private_key, "sDrSFDuPe0DTeee7vsjcE3ZUpP9uWIAa/ZFmt3d0VWQ=")
		self.assertEqual(public_key, "FlTE9pqz+bZ4/TkmyQ1Lq3YbvDRu+Qf2eXqdOdQa70I=")

	def test_client_address(self) -> None:
		self.assertEqual(
			networking.derive_client_address(TENANT, PEER), "fdaa:3d4f:3f30:1:0:aed7:659e:d754"
		)

	def test_derivations_are_deterministic(self) -> None:
		self.assertEqual(
			networking.derive_private_address(TENANT, VM),
			networking.derive_private_address(TENANT, VM),
		)
