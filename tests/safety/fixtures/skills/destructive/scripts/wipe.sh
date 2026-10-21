#!/usr/bin/env bash
TARGET=$1
rm -rf $TARGET/
psql -c "DROP DATABASE app_production;"
