# Security policy

CheckedFlow 0.1.x is experimental. Security corrections target the latest published 0.1.x
release; no long-term support or production security certification is promised.

Report a vulnerability privately through
[GitHub private vulnerability reporting](https://github.com/kadubon/checkedflow/security/advisories/new).
Include the affected version, minimal reproduction, trust boundary crossed and expected impact.
Remove keys, access tokens, private tasks and personal filesystem paths from reports. Do not
post working credentials or exploitable private deployment details in a public issue.

The [security audit and threat model](docs/security.md) describes the reviewed boundaries,
automated checks, known limits and responsible deployment assumptions. Keep node/admin keys,
gateway tokens, callback credentials and local journals outside version control.
