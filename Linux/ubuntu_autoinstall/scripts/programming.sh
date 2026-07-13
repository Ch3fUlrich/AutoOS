#!/bin/bash

# programming.sh - Script to install programming software

# Install XPipe
bash <(curl -sL https://github.com/xpipe-io/xpipe/raw/master/get-xpipe.sh)

# Create apps directory
mkdir -p /home/ubuntu/apps
chown ubuntu:ubuntu /home/ubuntu/apps
