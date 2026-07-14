"""Satellite's SSH engine (spec/28).

Unlike the old co-installed seam (which drove Atlas's `run_host_script`), Satellite is
a separate deployment with its OWN key. It reaches a VM's HOST over the host's public
IPv4 (host-plane services: the mesh, the gateway) and the GUEST over its public IPv6
(guest-plane services), both as root — Atlas injected Satellite's public key into each
box's authorized_keys at bootstrap/provision, so the private half here is all it needs.

The SSH targets come from the mirror row Satellite registered off the Atlas read API;
a handler never resolves an address or opens a socket itself.

`run_host`/`run_guest` take an optional `stdin` body so a handler can stream a config
map to a guest admin socket (proxy sync) or a file (`tee`) without ever putting it in
argv; `scp_guest` uploads a local file into the guest for the ones that ship a script.
"""

from __future__ import annotations

import subprocess

import frappe

Result = tuple[str, str, int]  # (stdout, stderr, exit_code)

_SSH_OPTS = [
	"-o",
	"StrictHostKeyChecking=no",
	"-o",
	"UserKnownHostsFile=/dev/null",
	"-o",
	"BatchMode=yes",
	"-o",
	"ConnectTimeout=15",
]


def _private_key_path() -> str:
	path = frappe.conf.get("satellite_ssh_private_key_path")
	if not path:
		frappe.throw("satellite_ssh_private_key_path is not set in site_config")
	return path


def _ssh(target: str, command: str, timeout: int, stdin: str | None = None) -> Result:
	argv = ["ssh", "-i", _private_key_path(), *_SSH_OPTS, f"root@{target}", command]
	proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout)
	return proc.stdout, proc.stderr, proc.returncode


def _target(vm: str, field: str, plane: str) -> str:
	target = frappe.db.get_value("Virtual Machine", vm, field)
	if not target:
		frappe.throw(f"Virtual Machine {vm} has no {field} ({plane} SSH target)")
	return target


def run_host(vm: str, command: str, timeout: int = 120, stdin: str | None = None) -> Result:
	"""Run a command on the VM's HOST over its public IPv4 (mesh/gateway)."""
	return _ssh(_target(vm, "server_ipv4", "host"), command, timeout, stdin)


def run_host_addr(ipv4: str, command: str, timeout: int = 120, stdin: str | None = None) -> Result:
	"""Run a command on a HOST addressed by its public IPv4 directly — the cross-host
	mesh addresses hosts, not VMs, and a host may have zero resident VMs to resolve
	through. `stdin` keeps a secret (the mesh key) out of argv."""
	if not ipv4:
		frappe.throw("run_host_addr called with no host IPv4")
	return _ssh(ipv4, command, timeout, stdin)


def run_guest(vm: str, command: str, timeout: int = 120, stdin: str | None = None) -> Result:
	"""Run a command inside the GUEST over its public IPv6 (proxy/bench/site/deploy).
	`stdin` streams a body to the command (a proxy map to a `--data-binary @-` / a file
	to `tee`), so it never rides argv."""
	return _ssh(_target(vm, "guest_ipv6", "guest"), command, timeout, stdin)


def scp_guest(vm: str, local_path: str, remote_path: str, timeout: int = 300) -> None:
	"""Upload a local file into the GUEST over its public IPv6 (e.g. a deploy script the
	handler then runs). The v6 literal is bracketed for scp's `host:path` form."""
	target = _target(vm, "guest_ipv6", "guest")
	argv = ["scp", "-i", _private_key_path(), *_SSH_OPTS, local_path, f"root@[{target}]:{remote_path}"]
	proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
	if proc.returncode != 0:
		frappe.throw(f"scp to guest {vm} failed (exit {proc.returncode}): {proc.stderr[-300:]}")
