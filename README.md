# synapse-s3

Minimal extension of `matrixdotorg/synapse` that pre-installs `synapse-s3-storage-provider`, so Synapse's `media_storage_providers` config can use S3 directly without per-pod pip install at startup.

Used by the Kampong Social Matrix homeserver on the gruyere k8s cluster.

## Image

```
ghcr.io/oddie-apps/synapse-s3:vX.Y.Z   # tracks upstream Synapse vX.Y.Z
ghcr.io/oddie-apps/synapse-s3:latest   # follows latest upstream stable
```

## Build cadence

CI rebuilds on:
- Push to `main` (Dockerfile changes)
- Manual workflow_dispatch with `synapse_version` input
- Weekly schedule (Mondays 06:00 UTC) — polls upstream for new releases

## Why this exists

Upstream `matrixdotorg/synapse` doesn't bundle the s3 storage provider. The plugin lives in `matrix-org/synapse-s3-storage-provider` and is `pip install`-able. Building it into the runtime image is the standard pattern (alternatives: pip install at startup via init container, mount via PYTHONPATH overlay) and the cleanest for production.

## Bumping

Renovate in the `argocd` repo will auto-detect new tags here and open a PR to bump the deployment image.
