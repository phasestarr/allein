# allein

Artificial, Agents, Aligned.

allein is a browser-based, multi-user Codex service designed for Kubernetes.
Users submit prompts and files, browse their conversations and workspace files, and run independent conversations concurrently while the server owns execution and workspace recovery.

## v1 design and implementation status

[Architecture](docs/architecture.md) is the canonical v1 design contract.
It defines singleton application services, CNPG-managed PostgreSQL with PgBouncer, durable artifact storage, and temporary per-conversation Codex execution environments.
Service-wide high availability is outside the v1 contract; independent DB placement and worker checkpoint recovery are supported design requirements.

- [Deployment topology and ownership](docs/architecture.md#deployment-topology-and-ownership).
- [Databases and storage](docs/architecture.md#databases-and-storage).
- [Checkpoints and suspension](docs/architecture.md#checkpoints-and-suspension).
- [Failure and recovery](docs/architecture.md#failure-and-recovery).
- [Verification requirements](docs/architecture.md#verification-requirements).

The Codex baseline is upstream version `0.170.0`.
The application, container images, Helm charts, and executable CI are not implemented in this checkout.
The architecture describes required behavior, not an inventory of deployed or verified runtime behavior.

## Delivery ownership

This project owns its source, tests, container images, Helm chart definitions, and project validation.
Public source and release information belong on GitHub; public images and Helm OCI packages belong on GHCR.
The GitHub Actions entry point is `.github/workflows/ci.yaml`, and `validation/` owns project validation logic shared with local development.
Validation must run `lint`, `build`, and `test` in that order, with all three required.
The workflow and validation directory are currently empty, so there are no executable validation commands yet.
Release automation and Dependabot configuration are not specified.

The consuming `cluster-addons` repository owns selected chart versions, cluster-specific values, and Argo CD registration.
Its Azure Pipelines checks validate that deployment configuration independently of this project's CI.
Argo CD reconciles the reviewed static deployment configuration; the allein runtime owns temporary execution resources under the architecture's lifecycle contract.
Publishing an allein release does not itself select that release for a cluster.
