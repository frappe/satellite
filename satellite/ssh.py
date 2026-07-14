"""Satellite's SSH engine (spec/28).

Unlike the old co-installed seam (which drove Atlas's `run_host_script`), Satellite is
a separate deployment with its OWN key. It reaches a VM's HOST over the host's public
IPv4 (host-plane services: the mesh, the gateway) and the GUEST over its public IPv6
(guest-plane services), both as root — Atlas injected Satellite's public key into each
box's authorized_keys at bootstrap/provision, so the private half here is all it needs.

The SSH targets come from the mirror row Satellite registered off the Atlas read API;
a handler never resolves an address or opens a socket itself.
"""

from __future__ import annotations

import subprocess

import frappe

Result = tuple[str, str, int]  # (stdout, stderr, exit_code)


def _private_key_path() -> str:
	path = frappe.conf.get("satellite_ssh_private_key_path")
	if not path:
		frappe.throw("satellite_ssh_private_key_path is not set in site_config")
	return path


def _ssh(target: str, command: str, timeout: int) -> Result:
	argv = [
		"ssh",
		"-i",
		_private_key_path(),
		"-o",
		"StrictHostKeyChecking=no",
		"-o",
		"UserKnownHostsFile=/dev/null",
		"-o",
		"BatchMode=yes",
		"-o",
		"ConnectTimeout=15",
		f"root@{target}",
		command,
	]
	proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
	return proc.stdout, proc.stderr, proc.returncode


def _target(vm: str, field: str, plane: str) -> str:
	target = frappe.db.get_value("Virtual Machine", vm, field)
	if not target:
		frappe.throw(f"Virtual Machine {vm} has no {field} ({plane} SSH target)")
	return target


def run_host(vm: str, command: str, timeout: int = 120) -> Result:
	"""Run a command on the VM's HOST over its public IPv4 (mesh/gateway)."""
	return _ssh(_target(vm, "server_ipv4", "host"), command, timeout)


def run_guest(vm: str, command: str, timeout: int = 120) -> Result:
	"""Run a command inside the GUEST over its public IPv6 (proxy/bench/site/deploy)."""
	return _ssh(_target(vm, "guest_ipv6", "guest"), command, timeout)
