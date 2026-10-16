# OmniRoute gateway for the AutoOS docker AI stack (compose.yml, service omniroute).
#
# The image comes from our fork (Ch3fUlrich/OmniRoute, branch autoos/*): its
# Dockerfile.autoos already carries qodercli@1.1.63 for the Qoder PAT login,
# bcryptjs@3.0.3 for the reset-password CLI, and the Vertex turn-shape guard v3
# as source patches, built by the fork's own workflow. This file is only a
# digest-pinned pointer, so image_source_hash still tracks the base.
# TODO(digest): the zero digest is a placeholder until the fork image is
# published - L1 replaces it with the real value before merge.
FROM ghcr.io/ch3fulrich/omniroute:3.8.50-autoos.1@sha256:0000000000000000000000000000000000000000000000000000000000000000
