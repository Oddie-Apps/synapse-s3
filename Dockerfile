# Synapse + synapse-s3-storage-provider
#
# Vanilla matrixdotorg/synapse with the s3 storage provider Python module
# pre-installed, so Synapse's media_storage_providers config can directly
# reference it without per-pod pip install at startup.
#
# Tag follows upstream Synapse: ghcr.io/oddie-apps/synapse-s3:vX.Y.Z
#
# CI builds this on:
#  - Manual workflow_dispatch (with version input)
#  - Push to main affecting Dockerfile / workflow
#  - Weekly schedule polling matrixdotorg/synapse for new releases

ARG SYNAPSE_VERSION=v1.151.0
FROM matrixdotorg/synapse:${SYNAPSE_VERSION}

# Install s3 storage provider
# - --no-cache-dir keeps the layer small
# - Pinning the version is a follow-up; for now track the latest published
USER root
RUN /usr/local/bin/python3 -m pip install --no-cache-dir synapse-s3-storage-provider \
    && rm -rf /root/.cache /tmp/*
USER 991
