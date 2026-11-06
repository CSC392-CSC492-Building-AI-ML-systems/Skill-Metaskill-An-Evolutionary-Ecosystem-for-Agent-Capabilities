#!/usr/bin/env bash
read -p "Delete $1? [y/N] " answer
[ "$answer" = "y" ] || exit 1
rm -rf "$1"
