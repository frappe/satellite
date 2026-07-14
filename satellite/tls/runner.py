"""The certbot runner — issues a wildcard cert as a local subprocess on the Satellite.

Atlas ran certbot through a controller-local Task (scripts/issue-cert.py); the Satellite
runs it as a plain subprocess here: build the argv (`certs.certbot_command`), merge the
DNS vendor credentials into the env (never argv, so they never show in `ps`), run
certbot, then read the issued cert's validity window with openssl. The PEMs land under
`~/.satellite/certbot/<domain>`; `services.proxy.push_cert` ships them to the proxies.
"""

from __future__ import annotations

import os
import subprocess

from satellite.tls import certs
from satellite.tls.base import IssuedCert


def issue_cert(
	domain: str,
	acme_directory_url: str,
	account_email: str,
	dns_authenticator: str,
	credential_env: dict[str, str],
	timeout: int = 600,
) -> IssuedCert:
	"""Issue (or renew, idempotently) `*.<domain>` via certbot DNS-01 and return the
	on-disk PEM paths + validity window. Raises RuntimeError on a certbot failure or a
	missing cert."""
	env = {**os.environ, **credential_env}
	argv = certs.certbot_command(domain, acme_directory_url, account_email, dns_authenticator)
	proc = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
	if proc.returncode != 0:
		raise RuntimeError(f"certbot failed for *.{domain} (exit {proc.returncode}): {proc.stderr[-500:]}")

	fullchain = certs.fullchain_path(domain)
	privkey = certs.privkey_path(domain)
	if not os.path.isfile(fullchain):
		raise RuntimeError(f"certbot reported success but {fullchain} is missing")

	dates = subprocess.run(
		["openssl", "x509", "-noout", "-dates", "-in", fullchain],
		capture_output=True,
		text=True,
		timeout=30,
	)
	not_before, not_after = certs.parse_openssl_dates(dates.stdout)
	return IssuedCert(
		fullchain_path=fullchain, privkey_path=privkey, not_before=not_before, not_after=not_after
	)
