"""Per-site deploy driver — turn a booted golden bench VM into a serving Frappe site,
and the HTTP readiness gate that proves it.

The Satellite side of the guest deploy (spec/14): upload `bench/deploy-site.py` into the
guest and run it over Satellite's own SSH (`run_guest`/`scp_guest`), then probe the
guest's public /128 :80 until it answers 200. Ported from Atlas's `deploy_site.py`;
Atlas is no longer in this path — Satellite reads the VM's `build_mode`/`warm` off the
mirror and drives the deploy itself. Central never calls here: the deploy is triggered
by a `site` Service Binding (satellite.services.site) off the mirrored VM, and the
Central handoff params ride through as opaque values.
"""

from __future__ import annotations

import http.client
import json
import shlex
import time
from pathlib import Path

import frappe

from satellite.ssh import run_guest, scp_guest

REMOTE_DEPLOY_DIRECTORY = "/tmp/atlas-deploy-site"
DEPLOY_SCRIPT_NAME = "deploy-site.py"
# The result line the in-guest script prints (its DeploySiteResult.emit() contract).
RESULT_MARKER = "ATLAS_RESULT="

# Readiness probe path is mode-aware: a site serves Frappe's `/api/method/ping`; an admin
# console is a Flask app with `/api/status` instead.
READINESS_PATH = "/api/method/ping"
_READINESS_PATH_FOR_MODE = {"site": "/api/method/ping", "admin": "/api/status"}
READINESS_TIMEOUT_SECONDS = 600
READINESS_POLL_SECONDS = 0.25
READINESS_MAX_POLL_SECONDS = 2.0


def readiness_path_for_mode(build_mode: str | None) -> str:
	"""The HTTP readiness path for a bench bake mode. Empty/None/unknown → site."""
	return _READINESS_PATH_FOR_MODE.get((build_mode or "site"), READINESS_PATH)


def _deploy_script_path() -> Path:
	return Path(frappe.get_app_path("satellite", "..")).resolve() / "bench" / DEPLOY_SCRIPT_NAME


def deploy_site(
	virtual_machine: str,
	site_name: str,
	central_endpoint: str | None = None,
	central_auth_token: str | None = None,
	mode: str | None = None,
	admin_domain: str | None = None,
) -> dict | None:
	"""Deploy one Frappe site into the (already booted) golden bench VM.

	Uploads `bench/deploy-site.py` and runs it as root over guest-SSH: it renames the
	baked `site.local` to the FQDN (Contract A), regenerates the bench nginx vhost and
	reloads, and mints a one-click `login_url`. The multitenant gunicorn resolves the
	site by Host header per request, so the rename takes effect with no restart. Fails
	loud (raises) on a non-zero exit so the binding is marked Failed. Returns the parsed
	`ATLAS_RESULT` dict (`site`, `serving`, `login_url`), or None if the guest emitted no
	result line."""
	vm = frappe.get_doc("Virtual Machine", virtual_machine)
	remote_script = f"{REMOTE_DEPLOY_DIRECTORY}/{DEPLOY_SCRIPT_NAME}"

	_wait_for_ssh(virtual_machine)
	run_guest(virtual_machine, f"mkdir -p {shlex.quote(REMOTE_DEPLOY_DIRECTORY)}", timeout=60)
	scp_guest(virtual_machine, str(_deploy_script_path()), remote_script)

	command = f"python3 {shlex.quote(remote_script)} --site-name {shlex.quote(site_name)}"
	# The deploy MODE — the mirrored bake mode, unless the caller overrides it (a
	# self-serve Pilot wires an admin console on a site-mode VM). Only pass --mode admin.
	build_mode = mode or vm.build_mode or "site"
	if build_mode == "admin":
		command += " --mode admin"
	if admin_domain:
		command += f" --admin-domain {shlex.quote(admin_domain)}"
	# A warm-restored clone gates the deploy on the in-guest identity freshen for THIS VM;
	# the in-guest identity is the Atlas VM uuid (the mirror's remote_id), not the mirror name.
	if vm.warm:
		command += f" --warm-vm-uuid {shlex.quote(vm.remote_id)}"
	# Central handoff (opaque pass-through): the bench-level callback endpoint + token
	# deploy-site.py writes into bench.toml so site->Central calls authenticate.
	if central_endpoint and central_auth_token:
		command += (
			f" --central-endpoint {shlex.quote(central_endpoint)}"
			f" --central-auth-token {shlex.quote(central_auth_token)}"
		)

	_stdout, stderr, code = run_guest(virtual_machine, command, timeout=1800)
	if code != 0:
		frappe.throw(f"Deploy of {site_name} on {virtual_machine} failed (exit {code}): {stderr[-500:]}")
	return _parse_result(_stdout)


def regenerate_login(virtual_machine: str, site_name: str, mode: str | None = None) -> dict | None:
	"""Re-mint the one-click login URL for an already-deployed FQDN. Same guest-SSH path
	as `deploy_site` but with `--regenerate-login` (sign a new session only, no rename).
	Fails loud; returns the parsed `ATLAS_RESULT` dict or None."""
	vm = frappe.get_doc("Virtual Machine", virtual_machine)
	remote_script = f"{REMOTE_DEPLOY_DIRECTORY}/{DEPLOY_SCRIPT_NAME}"

	_wait_for_ssh(virtual_machine)
	run_guest(virtual_machine, f"mkdir -p {shlex.quote(REMOTE_DEPLOY_DIRECTORY)}", timeout=60)
	scp_guest(virtual_machine, str(_deploy_script_path()), remote_script)

	command = (
		f"python3 {shlex.quote(remote_script)} --site-name {shlex.quote(site_name)} --regenerate-login"
	)
	if (mode or vm.build_mode or "site") == "admin":
		command += " --mode admin"
	_stdout, stderr, code = run_guest(virtual_machine, command, timeout=600)
	if code != 0:
		frappe.throw(
			f"Regenerate login for {site_name} on {virtual_machine} failed (exit {code}): {stderr[-500:]}"
		)
	return _parse_result(_stdout)


def _wait_for_ssh(virtual_machine: str, timeout_seconds: int = 300) -> None:
	"""Poll until the guest's sshd answers. A site VM is a CLONE that boots into a load
	storm (baked MariaDB/Redis/supervisor all auto-start + CoW thrash), so for the first
	~minute sshd's port is open but the exchange times out — going straight to scp would
	fail on that transient."""
	deadline = time.monotonic() + timeout_seconds
	while True:
		try:
			_out, _err, code = run_guest(virtual_machine, "true", timeout=20)
			if code == 0:
				return
		except Exception:
			pass
		if time.monotonic() >= deadline:
			frappe.throw(f"guest {virtual_machine} sshd did not answer within {timeout_seconds}s")
		time.sleep(2)


def _parse_result(stdout: str) -> dict | None:
	"""Parse the guest script's last `ATLAS_RESULT={json}` line (`site`, `serving`,
	`login_url`). None if absent (defensive; every real run emits exactly one)."""
	for line in reversed(stdout.splitlines()):
		if line.startswith(RESULT_MARKER):
			return json.loads(line[len(RESULT_MARKER) :])
	return None


def wait_for_http(
	ipv6_address: str,
	host_header: str,
	*,
	port: int = 80,
	path: str = READINESS_PATH,
	timeout_seconds: int = READINESS_TIMEOUT_SECONDS,
	poll_seconds: float = READINESS_POLL_SECONDS,
	max_poll_seconds: float = READINESS_MAX_POLL_SECONDS,
) -> None:
	"""Block until the guest answers HTTP 200 on :80 — the readiness gate (Contract B).
	Probes the VM's public /128 with the FQDN Host header (the same Host the edge proxy
	forwards). Poll starts tight and backs off geometrically. Raises on timeout."""
	deadline = time.monotonic() + timeout_seconds
	poll = poll_seconds
	while True:
		if _http_ok(ipv6_address, host_header, port, path):
			return
		if time.monotonic() >= deadline:
			raise frappe.ValidationError(
				f"HTTP 200 from {host_header} ([{ipv6_address}]:{port}{path}) not seen after {timeout_seconds}s"
			)
		time.sleep(poll)
		poll = min(poll * 1.5, max_poll_seconds)


def _http_ok(ipv6_address: str, host_header: str, port: int, path: str) -> bool:
	"""One probe: GET path over IPv6 with the FQDN Host header; True iff 200. A
	pre-serving guest refuses/resets/502s — every transport/HTTP error is a normal 'not
	ready yet', swallowed so the loop keeps trying. Only a clean 200 ends the wait."""
	conn = None
	try:
		conn = http.client.HTTPConnection(ipv6_address, port, timeout=10)
		conn.request("GET", path, headers={"Host": host_header})
		return conn.getresponse().status == 200
	except (OSError, http.client.HTTPException):
		return False
	finally:
		if conn is not None:
			conn.close()
