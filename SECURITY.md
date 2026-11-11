# Security policy

## Report a vulnerability

Use GitHub private vulnerability reporting. Open the "Security" tab of this repo, then select "Report a vulnerability". Only the maintainers can read the report.

Do not open a public issue for a vulnerability.

## Scope

This tool runs with cloud credentials. A fault in it can expose a credential, or can start a cloud action that costs money. Report these faults:

- Code that prints, stores, or sends a credential.
- Code that allocates cloud capacity without the consent key of the job.
- A fault in the OpenTofu stacks that opens access more than the specs say.

## Credentials

Logs can hold account or subscription IDs, because the stack stores provider messages without a filter. Read a log before you share it. Never paste a credential into an issue, a pull request, or a log you share. This includes access keys, tokens, SAS strings, and account or subscription IDs. If you pasted one by mistake, revoke it at once. Then tell the maintainers through a private report.

## Response

The maintainers read each report and reply in the report. They publish a fix before they publish the details.
