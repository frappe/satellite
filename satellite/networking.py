"""Satellite's copy of the private-network derivations (spec/28 Phase 1).

Ported VERBATIM from atlas/atlas/networking.py. Satellite never imports Atlas, but the
WireGuard mesh reconcile is CROSS-HOST: it must recompute every host's wg keypair + mesh
address and every VM's private /128 from the mirror. These derivations are pure functions
of resource UUIDs (which the mirror carries byte-for-byte as `remote_id` / `server` /
`tenant`), so re-deriving here reproduces bit-identical keys/addresses to the Atlas fabric.

EVERY constant, label, and bit-offset below is load-bearing: it must match
atlas/atlas/networking.py exactly, or a derived key/address diverges from the live device
and the mesh silently mis-routes. This is a frozen mirror — do not "improve" it. A golden
-vector test (test_networking.py) pins a few outputs against values captured from Atlas.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import uuid

PRIVATE_NETWORK_ULA = "fdaa::/16"
INFRA_PREFIX = "fdaa:0:0::/48"

TENANT_PREFIX_LENGTH = 48
TENANT_ID_BITS = TENANT_PREFIX_LENGTH - 16  # 32
REGION_BITS_OFFSET = 128 - 64  # 64
REGION_ID_BITS = 16
VM_HOST_PART_BITS = 64
CLIENT_HEXTET = 0x0001
CLIENT_HOST_PART_BITS = 48

WIREGUARD_MTU = 1420
WG_HOST_PORT = 51820
WG_GATEWAY_PORT = WG_HOST_PORT

_INFO_TENANT_PREFIX = b"atlas-private-tenant-prefix-v1"
_INFO_VM_HOST_PART = b"atlas-private-vm-host-part-v1"
_INFO_HOST_WIREGUARD_KEY = b"atlas-host-wg-v1"
_INFO_HOST_MESH_INDEX = b"atlas-host-mesh-index-v1"
_INFO_CLIENT_HOST_PART = b"atlas-vpc-client-host-part-v1"


def _hkdf(seed: bytes, info: bytes, length: int) -> bytes:
	if length > 32:
		raise ValueError("this minimal HKDF emits at most one SHA256 block (32 bytes)")
	pseudorandom_key = hmac.new(b"atlas-private-network", seed, hashlib.sha256).digest()
	block = hmac.new(pseudorandom_key, info + b"\x01", hashlib.sha256).digest()
	return block[:length]


def _name_seed(name: str) -> bytes:
	"""UUID -> 16 raw bytes; a non-UUID id (a Tenant id like TEAM-00001) -> its UTF-8
	bytes. Identical to Atlas so a tenant id seeds the same /48 on both sides."""
	try:
		return uuid.UUID(name).bytes
	except (ValueError, AttributeError, TypeError):
		return name.encode("utf-8")


def derive_tenant_prefix(tenant_name: str) -> str:
	tenant_id = int.from_bytes(_hkdf(_name_seed(tenant_name), _INFO_TENANT_PREFIX, 4), "big")
	tenant_id &= (1 << TENANT_ID_BITS) - 1
	ula = ipaddress.IPv6Network(PRIVATE_NETWORK_ULA)
	base = int(ula.network_address) | (tenant_id << (128 - TENANT_PREFIX_LENGTH))
	return str(ipaddress.IPv6Network((base, TENANT_PREFIX_LENGTH)))


def derive_private_address(tenant_name: str, virtual_machine_name: str, region_index: int = 0) -> str:
	if not 0 <= region_index < (1 << REGION_ID_BITS):
		raise ValueError(f"region_index {region_index} out of range for {REGION_ID_BITS} bits")
	prefix = ipaddress.IPv6Network(derive_tenant_prefix(tenant_name))
	host_part = int.from_bytes(_hkdf(_name_seed(virtual_machine_name), _INFO_VM_HOST_PART, 8), "big")
	host_part &= (1 << VM_HOST_PART_BITS) - 1
	region_bits = region_index << REGION_BITS_OFFSET
	address = int(prefix.network_address) | region_bits | host_part
	candidate = ipaddress.IPv6Address(address)
	if candidate not in prefix:
		raise ValueError(f"{candidate} fell outside tenant prefix {prefix}")
	return str(candidate)


def _clamp_curve25519_scalar(scalar: bytearray) -> bytearray:
	scalar[0] &= 248
	scalar[31] &= 127
	scalar[31] |= 64
	return scalar


def derive_host_wireguard_keypair(server_name: str) -> tuple[str, str]:
	"""(private_key_b64, public_key_b64) for a host's wg-mesh device, keyed on the Server
	UUID. The public key is the real Curve25519 base-point multiply — byte-identical to
	`wg pubkey` and to Atlas's derivation."""
	from cryptography.hazmat.primitives import serialization
	from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

	seed = _hkdf(uuid.UUID(server_name).bytes, _INFO_HOST_WIREGUARD_KEY, 32)
	private_scalar = bytes(_clamp_curve25519_scalar(bytearray(seed)))
	private_key = X25519PrivateKey.from_private_bytes(private_scalar)
	public_raw = private_key.public_key().public_bytes(
		serialization.Encoding.Raw, serialization.PublicFormat.Raw
	)
	return base64.b64encode(private_scalar).decode(), base64.b64encode(public_raw).decode()


def derive_host_mesh_address(server_name: str) -> str:
	infra = ipaddress.IPv6Network(INFRA_PREFIX)
	host_index = int.from_bytes(_hkdf(uuid.UUID(server_name).bytes, _INFO_HOST_MESH_INDEX, 2), "big")
	host_index &= (1 << REGION_ID_BITS) - 1
	address = int(infra.network_address) | (host_index << REGION_BITS_OFFSET) | 1
	return str(ipaddress.IPv6Address(address))


def derive_client_address(tenant_name: str, client_peer_name: str) -> str:
	prefix = ipaddress.IPv6Network(derive_tenant_prefix(tenant_name))
	host_part = int.from_bytes(_hkdf(_name_seed(client_peer_name), _INFO_CLIENT_HOST_PART, 6), "big")
	host_part &= (1 << CLIENT_HOST_PART_BITS) - 1
	address = int(prefix.network_address) | (CLIENT_HEXTET << REGION_BITS_OFFSET) | host_part
	candidate = ipaddress.IPv6Address(address)
	if candidate not in prefix:
		raise ValueError(f"{candidate} fell outside tenant prefix {prefix}")
	return str(candidate)
