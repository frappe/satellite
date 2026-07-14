"""Proxy guest cert ops (spec/12 §5.3), driven over Satellite's SSH.

The proxy fleet reconcile lives in `services.routing`; this module holds the cert
side — pushing the regional wildcard cert into a proxy guest and reloading nginx. The
cert is pushed, never baked, so one proxy image serves any region and a renewed cert is
a re-push. Ported from Atlas's `proxy.py` (the guest-plane half).
"""

from __future__ import annotations

import shlex

import frappe

from satellite.routing.region import active_region_domain
from satellite.services.routing import is_proxy
from satellite.ssh import run_guest

# Mirrors the stock Ubuntu nginx layout on the proxy guest.
CERT_DIRECTORY = "/var/lib/nginx/certs"
# The self-signed cert the :8446 unconfigured-domain terminator presents. Kept
# byte-identical to build.sh's `-subj` so a targeted regen and a full re-bake write the
# SAME cert. The `\/` is OpenSSL's escape for a literal slash in the URL.
PLACEHOLDER_DIRECTORY = f"{CERT_DIRECTORY}/_placeholder"
PLACEHOLDER_CERT_SUBJECT = (
	"/CN=This domain is not connected to a site yet"
	"/O=Frappe Cloud"
	"/OU=Connect it in your dashboard: frappe.dev\\/domains"
)


def push_cert(virtual_machine: str, fullchain: str, privkey: str) -> None:
	"""Push the regional wildcard cert into a proxy guest and reload nginx.

	Writes fullchain.pem/privkey.pem into the guest's per-region cert dir, points the
	flat cert symlink nginx reads at it, and reloads — all over Satellite's guest SSH.
	The private key rides stdin (`tee`), never argv, so `ps` can't read it."""
	region = active_region_domain().region
	cert_dir = f"{CERT_DIRECTORY}/{region}"
	_write_guest_file(virtual_machine, f"{cert_dir}/fullchain.pem", fullchain, "0644", make_dir=cert_dir)
	_write_guest_file(virtual_machine, f"{cert_dir}/privkey.pem", privkey, "0600")
	command = f"{_point_cert_symlink_command(region)} && /usr/sbin/nginx -s reload"
	_stdout, stderr, code = run_guest(virtual_machine, command, timeout=60)
	if code != 0:
		frappe.throw(f"Cert push/reload to {virtual_machine} failed (exit {code}): {stderr[-500:]}")


def regenerate_placeholder_cert(virtual_machine: str) -> None:
	"""Regenerate the :8446 unconfigured-domain placeholder cert on a live proxy and
	reload, without a full re-bake. Touches only certs/_placeholder/; the wildcard cert
	push_cert installed is untouched. Keep this openssl invocation in lockstep with
	build.sh's (rsa:2048, 3650d, -nodes, the byte-identical subject)."""
	if not is_proxy(virtual_machine):
		frappe.throw(f"Virtual Machine {virtual_machine} is not a proxy")
	fullchain = f"{PLACEHOLDER_DIRECTORY}/fullchain.pem"
	privkey = f"{PLACEHOLDER_DIRECTORY}/privkey.pem"
	command = (
		f"install -d -m 0750 {shlex.quote(PLACEHOLDER_DIRECTORY)} && "
		f"openssl req -x509 -newkey rsa:2048 -nodes -days 3650 "
		f"-keyout {shlex.quote(privkey)} -out {shlex.quote(fullchain)} "
		f"-subj {shlex.quote(PLACEHOLDER_CERT_SUBJECT)} && "
		f"chmod 0640 {shlex.quote(privkey)} && /usr/sbin/nginx -s reload"
	)
	_stdout, stderr, code = run_guest(virtual_machine, command, timeout=60)
	if code != 0:
		frappe.throw(f"Placeholder cert regen/reload to {virtual_machine} failed (exit {code}): {stderr[-500:]}")


def _write_guest_file(
	virtual_machine: str, path: str, content: str, mode: str, make_dir: str | None = None
) -> None:
	"""Write `content` to `path` in the guest via `tee` (content on stdin, never argv),
	then chmod. Optionally mkdir -p the parent first."""
	command = f"mkdir -p {shlex.quote(make_dir)} && " if make_dir else ""
	command += f"tee {shlex.quote(path)} >/dev/null && chmod {mode} {shlex.quote(path)}"
	_stdout, stderr, code = run_guest(virtual_machine, command, timeout=60, stdin=content)
	if code != 0:
		frappe.throw(f"Writing {path} to {virtual_machine} failed (exit {code}): {stderr[-300:]}")


def _point_cert_symlink_command(region: str) -> str:
	"""Repoint the flat cert path nginx reads at this region's cert dir. nginx can't
	interpolate the region into ssl_certificate, so it reads a flat symlink; this moves
	it to certs/<region>/. Relative targets, `-n` so we replace the link on a re-run."""
	return (
		f"ln -sfn {shlex.quote(f'{region}/fullchain.pem')} {shlex.quote(f'{CERT_DIRECTORY}/fullchain.pem')} && "
		f"ln -sfn {shlex.quote(f'{region}/privkey.pem')} {shlex.quote(f'{CERT_DIRECTORY}/privkey.pem')}"
	)
