"""Pure helpers for cert issuance — the certbot argv and the on-disk PEM layout.

Kept separate from `runner.py` so the argv construction and the on-disk layout are
unit-testable with no certbot. Satellite's controller-local state sits under
`~/.satellite/certbot/<domain>`, per-domain so accounts/renewal state never collide
across regions. Ported from Atlas's scripts/lib/atlas/certs.py (argv is a list here,
not an auto-quoted string, since the runner calls subprocess directly).
"""

from __future__ import annotations

import os


def satellite_home() -> str:
	"""The Satellite node's controller home, `~/.satellite`."""
	return os.path.join(os.path.expanduser("~"), ".satellite")


def certbot_config_dir(domain: str) -> str:
	"""certbot's --config-dir for this domain. Per-domain so accounts/renewal state
	never collide across regions."""
	return os.path.join(satellite_home(), "certbot", domain)


def live_dir(domain: str) -> str:
	"""Where certbot writes the live symlinks for `*.<domain>`. certbot names the lineage
	after the first -d, with the leading `*.` stripped to `<domain>`."""
	return os.path.join(certbot_config_dir(domain), "live", domain)


def fullchain_path(domain: str) -> str:
	return os.path.join(live_dir(domain), "fullchain.pem")


def privkey_path(domain: str) -> str:
	return os.path.join(live_dir(domain), "privkey.pem")


def certbot_command(
	domain: str, acme_directory_url: str, account_email: str, dns_authenticator: str
) -> list[str]:
	"""The certbot argv to issue (or renew) `*.<domain>` non-interactively over DNS-01.
	`dns_authenticator` is the DNS plugin name (e.g. `route53`), rendered as the
	`--dns-<name>` flag. Credentials travel via the environment, never argv, so they never
	appear in `ps`. Idempotent: certbot renews-or-skips a still-valid lineage."""
	config = certbot_config_dir(domain)
	return [
		"certbot",
		"certonly",
		"--non-interactive",
		"--agree-tos",
		"-m",
		account_email,
		"--server",
		acme_directory_url,
		f"--dns-{dns_authenticator}",
		"-d",
		f"*.{domain}",
		"--config-dir",
		config,
		"--work-dir",
		os.path.join(config, "work"),
		"--logs-dir",
		os.path.join(config, "logs"),
		"--keep-until-expiring",
	]


def parse_openssl_dates(stdout: str) -> tuple[str, str]:
	"""Parse `openssl x509 -noout -dates` output into (not_before, not_after) as the raw
	OpenSSL date strings (e.g. `Jun  8 00:00:00 2026 GMT`). The controller normalizes
	these via frappe.utils.get_datetime. Raises ValueError if either line is missing."""
	not_before = not_after = None
	for line in stdout.splitlines():
		if line.startswith("notBefore="):
			not_before = line[len("notBefore=") :].strip()
		elif line.startswith("notAfter="):
			not_after = line[len("notAfter=") :].strip()
	if not_before is None or not_after is None:
		raise ValueError(f"could not parse notBefore/notAfter from openssl output: {stdout!r}")
	return not_before, not_after
