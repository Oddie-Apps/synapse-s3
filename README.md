# synapse-s3

Minimal extension of the **official `ghcr.io/element-hq/synapse`** image that pre-installs the **stock** `synapse-s3-storage-provider`, so Synapse's `media_storage_providers` config can use S3 directly without a per-pod pip install at startup.

Used by the Kampong Social Matrix homeserver on the fondue k8s cluster.

## Image

```
forge.oddie.app/oddie-apps/synapse-s3:vX.Y.Z   # tracks upstream Synapse vX.Y.Z
forge.oddie.app/oddie-apps/synapse-s3:latest   # follows latest upstream stable
```

## Build cadence

CI rebuilds on:
- Push to `main` (Dockerfile / workflow changes)
- Manual workflow_dispatch with `synapse_version` input
- Weekly schedule (Mondays 06:00 UTC) — polls `element-hq/synapse` for new releases

## Why this exists

Official `ghcr.io/element-hq/synapse` doesn't bundle the s3 storage provider. The plugin lives in `matrix-org/synapse-s3-storage-provider` and is `pip install`-able. Baking it into the runtime image is the standard pattern (alternatives — pip install at startup via init container, or a PYTHONPATH overlay — are messier) and the cleanest for production.

## The one non-stock line

The Dockerfile carries a single `sed` that removes an overstrict assertion in `synapse/handlers/profile.py` (upstream bug **element-hq/synapse#19702**, still OPEN as of v1.157.0). Without it, avatar/profile updates for appservice (bridge) virtual users that lack an existing profile row crash. It is **not a fork** — one line on stock code, with a `grep` guard that fails the build once upstream removes the assert, forcing us to drop the workaround. No other patches; the s3 provider is used unmodified.

## Bumping

Renovate in the `platform` repo auto-detects new tags here and opens a PR to bump the deployment image.
