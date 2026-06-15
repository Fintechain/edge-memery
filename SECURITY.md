# Security Policy

## Supported Versions

Security fixes are applied to the latest release and the current default branch. Older releases may not receive patches.

## Reporting a Vulnerability

Please use GitHub's **Private vulnerability reporting** or **Security advisory** feature for the repository. Do not open a public issue for vulnerabilities, exposed secrets, unsafe deserialization, path traversal, database corruption, authentication bypass, or private-memory disclosure.

Include:

- A concise description of the issue and its impact.
- Reproduction steps or a minimal proof of concept.
- Affected versions and environment details.
- Any known mitigations.

Do not include real private memory databases, credentials, API keys, or personal information. Use synthetic data.

Maintainers will acknowledge a valid report when available, investigate it, and coordinate disclosure after a fix or mitigation is ready. Response times are best-effort because this is a community-maintained project.

## Operational Security

Memery stores sensitive context locally. Operators should protect `~/.memery`, restrict filesystem permissions, encrypt backups where appropriate, and avoid storing secrets unless their threat model explicitly permits it.
