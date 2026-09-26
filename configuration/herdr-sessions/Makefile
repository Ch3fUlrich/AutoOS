.PHONY: test build run
# Registry to tag into. Deliberately NOT a real registry by default: pushing has
# to be an explicit choice. Override per invocation or in the environment, e.g.
#   make build REGISTRY=ghcr.io/<you>
REGISTRY ?= localhost
IMAGE ?= $(REGISTRY)/$(notdir $(CURDIR))
TAG   ?= $(shell git rev-parse --short HEAD)
# No Dockerfile here: installed in place on each host (systemd units +
# profile), not a shipped image -- see README.md.
test: ; bash tests/test_smoke.sh
build: ; docker build -t $(IMAGE):$(TAG) .
run: ; docker run --rm --env-file .env $(IMAGE):$(TAG)
