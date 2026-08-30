# Security policy

## Report a vulnerability privately

Do not open a public issue for a security problem. Use GitHub's private vulnerability
reporting page:

https://github.com/gititya/hold-my-data/security/advisories/new

Do not include real personal information, private documents, passwords, API keys, or other
live credentials. Reproduce the problem with fake data whenever possible.

Please include:

- the affected version and operating system;
- the exact command or library call;
- the result you expected and the result you saw; and
- a small fake-data reproduction.

The maintainer will acknowledge a report within seven days and will coordinate a fix and
disclosure with the reporter. Please allow time for a fix before publishing details.

## Supported versions

Only the latest tagged release receives security fixes.

## Security boundaries

A report is especially useful when Hold My Data:

- sends data over a network during redaction;
- writes a partly redacted output after OCR or detector failure;
- overwrites an input or existing output without explicit permission;
- logs raw personal information or credentials; or
- allows one host product's runtime taxonomy to affect another product.

Accuracy misses are important but are not always software vulnerabilities. Report a
repeatable bypass involving a supported entity privately when publishing it would expose
users to a practical leak. Use a normal issue for general accuracy improvements, with fake
examples only.
