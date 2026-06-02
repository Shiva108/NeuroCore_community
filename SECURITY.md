# Security Policy

## Reporting

Do not open public issues for suspected vulnerabilities.

Do not include real API keys, bearer tokens, database URLs, local `.env`
content, or unsanitized provider logs in bug reports, screenshots, or pull
requests.

Preferred path:

- use GitHub private vulnerability reporting once it is enabled on the public
  repository

Fallback:

- contact the maintainer through a private channel and include reproduction
  steps, impact, and affected versions

## Supported Versions

Security fixes are targeted at:

- the current `main` branch
- the latest tagged release line

## Scope

Please report issues involving:

- unauthorized access to stored content
- isolation or sensitivity boundary failures
- authentication or adapter exposure issues
- secret handling, credential leakage, or unsafe defaults
