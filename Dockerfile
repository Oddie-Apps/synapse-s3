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

# Patch element-hq/synapse#19702 — overstrict assertion in profile.py crashes
# avatar updates from appservices when stale tasks linger in the scheduler.
# The code on the next line already iterates all tasks and cancels them, so
# the assertion is paranoia. Removing it.
RUN PROFILE_PY=$(/usr/local/bin/python3 -c "import synapse.handlers.profile, os; print(os.path.dirname(synapse.handlers.profile.__file__) + '/profile.py')") \
    && grep -q 'Expected at most one task to cancel' "$PROFILE_PY" \
    && sed -i '/assert len(tasks_to_cancel) <= 1, "Expected at most one task to cancel"/d' "$PROFILE_PY" \
    && ! grep -q 'Expected at most one task to cancel' "$PROFILE_PY" \
    && echo "PATCH APPLIED to $PROFILE_PY"

# Overlay our local fork of s3_storage_provider.py with cache-on-read.
# Without this, every R2 fetch costs a full round-trip even with a PVC
# at /data, because upstream's fetch() never writes back to local cache.
# Marked changes inside the file with "OA-CACHE".
COPY s3_storage_provider.py /tmp/s3_storage_provider.py
RUN TARGET=$(/usr/local/bin/python3 -c "import s3_storage_provider, os; print(s3_storage_provider.__file__)") \
    && cp /tmp/s3_storage_provider.py "$TARGET" \
    && rm /tmp/s3_storage_provider.py \
    && grep -q 'OA-CACHE' "$TARGET" \
    && echo "S3 PROVIDER PATCH APPLIED to $TARGET"

USER 991
