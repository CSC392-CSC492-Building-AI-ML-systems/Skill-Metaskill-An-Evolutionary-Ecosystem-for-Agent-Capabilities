#!/bin/sh
set -eu

WORKDIR="{{config.workdir}}"
BUILD_HOST="{{config.build_host}}"

cd "$WORKDIR"
ssh "$BUILD_HOST" "cd ${HOME}/payments && make deploy"
echo "Deployed; notify {{config.contact_email}}"
