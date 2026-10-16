# OmniRoute gateway for the AutoOS docker AI stack (compose.yml, service omniroute).
#
# The image comes from our fork (Ch3fUlrich/OmniRoute, branch autoos/*): its
# Dockerfile.autoos already carries qodercli@1.1.63 for the Qoder PAT login,
# bcryptjs@3.0.3 for the reset-password CLI, and the Vertex turn-shape guard v3
# as source patches, built by the fork's own workflow. This file is only a
# digest-pinned pointer, so image_source_hash still tracks the base.
# Bump the tag and digest together with the fork image (its workflow summary
# prints both).
FROM ghcr.io/ch3fulrich/omniroute:3.8.50-autoos.10@sha256:61662976dfaf46b3322b537c4c1541ffc4a0f724b4b49b87ff498d5d8efd2033
