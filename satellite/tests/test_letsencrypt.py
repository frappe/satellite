"""Let's Encrypt issuer + the certbot runner — no certbot runs. The provider test
mocks the runner; the runner test mocks the subprocess and asserts the argv + that AWS
creds ride the env, never argv."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests import IntegrationTestCase

from satellite.dns.base import AuthResult as DnsAuthResult
from satellite.dns.base import DnsProvider, WildcardTargets
from satellite.tls import letsencrypt, runner
from satellite.tls.base import IssuedCert


class _StubDns(DnsProvider):
	provider_type = "Stub"

	def authenticate(self) -> DnsAuthResult:
		return DnsAuthResult(ok=True)

	def credential_env(self) -> dict[str, str]:
		return {"AWS_ACCESS_KEY_ID": "AKIA", "AWS_SECRET_ACCESS_KEY": "shh"}

	def certbot_authenticator(self) -> str:
		return "route53"

	def upsert_wildcard(self, domain: str, targets: WildcardTargets) -> list[str]:
		return []


def _provider(directory="https://acme-staging.example/dir", email="ops@example.com"):
	settings = SimpleNamespace(acme_directory_url=directory, account_email=email)
	with patch.object(letsencrypt.frappe, "get_single", return_value=settings):
		return letsencrypt.LetsEncryptProvider()


class TestLetsEncryptProvider(IntegrationTestCase):
	def test_authenticate_requires_email(self) -> None:
		self.assertTrue(_provider().authenticate().ok)
		self.assertFalse(_provider(email="").authenticate().ok)

	def test_issue_drives_the_runner_with_authenticator_and_credential_env(self) -> None:
		provider = _provider()
		issued = IssuedCert("/fc.pem", "/pk.pem", "nb", "na")
		with patch.object(letsencrypt, "issue_cert", return_value=issued) as run:
			result = provider.issue("blr1.frappe.dev", _StubDns())
		_, kwargs = run.call_args
		self.assertEqual(kwargs["domain"], "blr1.frappe.dev")
		self.assertEqual(kwargs["dns_authenticator"], "route53")
		self.assertEqual(kwargs["credential_env"]["AWS_ACCESS_KEY_ID"], "AKIA")
		self.assertEqual(kwargs["acme_directory_url"], "https://acme-staging.example/dir")
		self.assertIs(result, issued)


class TestIssueCertRunner(IntegrationTestCase):
	def test_builds_argv_merges_env_and_parses_dates(self) -> None:
		certbot = SimpleNamespace(returncode=0, stdout="", stderr="")
		openssl = SimpleNamespace(
			returncode=0, stdout="notBefore=Jun  8 00:00:00 2026 GMT\nnotAfter=Sep  6 00:00:00 2026 GMT\n"
		)
		with (
			patch.object(runner.subprocess, "run", side_effect=[certbot, openssl]) as run_mock,
			patch.object(runner.os.path, "isfile", return_value=True),
		):
			issued = runner.issue_cert(
				"blr1.frappe.dev", "https://acme/dir", "ops@x", "route53",
				{"AWS_ACCESS_KEY_ID": "AKIA"},
			)
		certbot_argv = run_mock.call_args_list[0].args[0]
		self.assertIn("--dns-route53", certbot_argv)
		self.assertEqual(certbot_argv[certbot_argv.index("-d") + 1], "*.blr1.frappe.dev")
		# AWS creds ride the env, never argv.
		self.assertEqual(run_mock.call_args_list[0].kwargs["env"]["AWS_ACCESS_KEY_ID"], "AKIA")
		self.assertNotIn("AKIA", certbot_argv)
		self.assertEqual(issued.not_after, "Sep  6 00:00:00 2026 GMT")
		self.assertTrue(issued.fullchain_path.endswith("/live/blr1.frappe.dev/fullchain.pem"))

	def test_raises_when_certbot_fails(self) -> None:
		certbot = SimpleNamespace(returncode=1, stdout="", stderr="rate limited")
		with patch.object(runner.subprocess, "run", return_value=certbot):
			with self.assertRaises(RuntimeError):
				runner.issue_cert("blr1.frappe.dev", "https://acme/dir", "ops@x", "route53", {})
