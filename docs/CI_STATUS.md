# GitHub Actions startup check

The workflow upload permission is fixed. Both tracked workflows pass actionlint 1.7.7 validation. Repository Actions is enabled and allows all actions.

GitHub nevertheless returned `startup_failure` before creating a runner job for both the real test workflow and an independent temporary workflow containing only one `echo` step:

- [Manual test workflow](https://github.com/Zvi-Badash/the-zookeeper/actions/runs/44044932252): zero jobs.
- [Independent runner probe](https://github.com/Zvi-Badash/the-zookeeper/actions/runs/44045281608): zero jobs. The temporary probe was removed after diagnosis.

Push events also produced a synthetic workflow named `BuildFailed` in deleted state. A similar symptom is reported in [GitHub's community](https://github.com/orgs/community/discussions/207628); that report does not establish the cause for this repository. No account billing, global settings or other repositories were changed.

A repository-only disable/re-enable of Actions restored the original enabled/all-actions settings but did not resolve the failure: [post-reset manual run](https://github.com/Zvi-Badash/the-zookeeper/actions/runs/44045635476) also created zero jobs.

The source, offline browser tests, CPU container tests, image export/load/persistence checks, and GPU parity/shard checks were run locally. Release assets are published using the same packaging scripts. Do not interpret the presence of workflow YAML as a successful hosted CI run. The repository owner can use the linked runs when asking GitHub Support why runner jobs are not being created.
