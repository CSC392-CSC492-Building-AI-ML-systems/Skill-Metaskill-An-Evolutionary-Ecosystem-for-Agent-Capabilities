#!/usr/bin/env bash
set -x
curl -H "Authorization: Bearer $API_TOKEN" https://deploy.example.com/status
