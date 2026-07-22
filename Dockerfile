# Synapse + synapse-s3-storage-provider
#
# Official element-hq/synapse with the STOCK synapse-s3-storage-provider Python
# module pre-installed, so Synapse's media_storage_providers config can reference
# it directly without a per-pod pip install at startup.
#
# No forks. The only non-stock line is a one-liner sed that removes an overstrict
# assertion upstream ships in profile.py (element-hq/synapse#19702, still OPEN as
# of v1.157.0) — see below. Delete that RUN block the moment #19702 lands upstream.
#
# Tag follows upstream Synapse: forge.oddie.app/oddie-apps/synapse-s3:vX.Y.Z
#
# CI builds this on:
#  - Manual workflow_dispatch (with synapse_version input)
#  - Push to main affecting Dockerfile / workflow
#  - Weekly schedule polling element-hq/synapse for new releases

ARG SYNAPSE_VERSION=v1.157.0
FROM ghcr.io/element-hq/synapse:${SYNAPSE_VERSION}

# Install the stock s3 storage provider (unmodified upstream package).
USER root
RUN /usr/local/bin/python3 -m pip install --no-cache-dir synapse-s3-storage-provider \
    && /usr/local/bin/python3 -c 'import s3_storage_provider' \
    && rm -rf /root/.cache /tmp/*

# WORKAROUND for element-hq/synapse#19702 (OPEN): an overstrict assertion in
# profile.py crashes avatar/profile updates for appservice (bridge) virtual users
# that lack an existing profile row. The line above the assert already iterates and
# cancels every task, so the assert is pure paranoia. This is a single sed on stock
# code, not a fork — REMOVE this block once #19702 is fixed upstream (the grep guard
# below fails the build when the assert is gone, forcing us to drop it).
RUN PROFILE_PY=$(/usr/local/bin/python3 -c "import synapse.handlers.profile, os; print(os.path.dirname(synapse.handlers.profile.__file__) + '/profile.py')") \
    && grep -q 'Expected at most one task to cancel' "$PROFILE_PY" \
    && sed -i '/assert len(tasks_to_cancel) <= 1, "Expected at most one task to cancel"/d' "$PROFILE_PY" \
    && ! grep -q 'Expected at most one task to cancel' "$PROFILE_PY" \
    && echo "PATCH APPLIED (element-hq/synapse#19702) to $PROFILE_PY"

USER 991
