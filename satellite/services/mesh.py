"""MeshService — the host-plane private mesh, as a Satellite service handler (spec/28).

The overlay that carries each tenant's private plane is host-plane: it lives on the
firecracker HOST, not the guest. So this handler reaches the host itself over
Satellite's SSH (`run_host`) and maintains a per-host registry file listing the mesh
address of every VM resident there (`/etc/satellite/mesh/peers`). A binding's apply
adds this VM's line; its withdraw removes it. Both are idempotent.

This is the walking-skeleton reduction of the full WireGuard reconcile (that comes in a
later phase); it proves the whole new model end to end — register a VM off an Atlas,
bind a service, and have Satellite drive a real host effect over its own SSH.
"""

from __future__ import annotations

import shlex

from satellite.ssh import run_host

PEERS = "/etc/satellite/mesh/peers"


class MeshService:
	def _peer(self, vm) -> str:
		"""The address this VM advertises: its derived private /128 when it has one,
		else its public /128, else its id — a stable identifier either way."""
		return vm.private_address or vm.guest_ipv6 or vm.remote_id

	def apply(self, vm, binding) -> None:
		"""Publish this VM's peer into its host's registry. Idempotent."""
		line = f"{vm.remote_id} {self._peer(vm)}"
		command = (
			f"mkdir -p /etc/satellite/mesh && touch {PEERS} && "
			f"grep -qxF {shlex.quote(line)} {PEERS} || echo {shlex.quote(line)} >> {PEERS}"
		)
		_out, err, code = run_host(vm.name, command)
		if code != 0:
			raise RuntimeError(f"mesh apply failed (exit {code}): {err[-200:]}")

	def withdraw(self, vm, binding) -> None:
		"""Withdraw this VM's peer from its host's registry. Idempotent (no-op if the
		line is already gone, keyed on the VM id)."""
		script = f"/^{vm.remote_id} /d"
		command = f"[ -f {PEERS} ] && sed -i {shlex.quote(script)} {PEERS}; true"
		_out, err, code = run_host(vm.name, command)
		if code != 0:
			raise RuntimeError(f"mesh withdraw failed (exit {code}): {err[-200:]}")
