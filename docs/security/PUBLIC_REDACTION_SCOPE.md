# Public MX repository: sanitized tip policy

Status: **working-tree redaction only**. This commit is not a historical purge.
P0 tag \`p0-accepted\` and commit \`15dd282\` are intentionally unchanged.

## Excluded from the public tip

- Original P0 GUI evidence, run logs, login automation and local account backups: **255 files**
- Private development notes and desktop tooling under \`.workbuddy/\`: **31 files**
- Archived unreviewed protocol/client reference bundles: **5 tracked paths**

The original snapshots remain reachable from previously published commits/tags until a
separate history-cleaning/public-repository migration is authorized. Git cannot
unpublish data already cloned, cached or downloaded. Before any history rewrite,
preserve an **offline restricted evidence archive**, assess the P0 hash/CI
implications, notify collaborators, and rotate every potentially reused credential.

## Maintained integrity and limits

- Does **not** change gameplay logic, protocol fields, the P0 tag, or P0 evidence hashes.
- Does **not** claim GUI original-client, P8 authenticity, or full 65-case tests passed.
- Does **not** rewrite or force-push shared history.
- Static GitHub-hosted checks only certify the sanitized **current checkout**.
- Credential strings visible in prior login automation could have been reused:
  revoke/rotate any relevant accounts independently of this Git commit.
- Protocol traces and analysis reports outside the excluded folders remain
  candidates for separate provenance/privacy/licensing review.
