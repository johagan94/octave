# Security Policy

## Reporting a Vulnerability

Report security vulnerabilities by opening a private issue or contacting the maintainer directly. Do not open public issues for security concerns.

## Security Practices

- API keys and tokens are loaded from environment variables only
- No secrets are committed to the repository
- Dependencies are scanned for known vulnerabilities
- Containers run as non-root users where applicable

