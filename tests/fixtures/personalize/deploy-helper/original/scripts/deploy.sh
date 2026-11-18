#!/bin/sh
set -eu

WORKDIR="/Users/janedoe/work/payments"
BUILD_HOST="build.acme.internal"

cd "$WORKDIR"
ssh "$BUILD_HOST" "cd ${HOME}/payments && make deploy"
echo "Deployed; notify jane.doe@acme-example.dev"
