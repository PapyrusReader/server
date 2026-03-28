#!/usr/bin/env bash

set -euo pipefail

target_dir=".local/powersync"
private_key_path="${target_dir}/private.pem"
public_key_path="${target_dir}/public.pem"

if [[ "${1:-}" == "--force" ]]; then
  rm -f "${private_key_path}" "${public_key_path}"
fi

if [[ -f "${private_key_path}" || -f "${public_key_path}" ]]; then
  echo "PowerSync key files already exist in ${target_dir}. Use --force to replace them."
  exit 1
fi

mkdir -p "${target_dir}"

openssl genrsa -out "${private_key_path}" 2048 >/dev/null 2>&1
openssl rsa -in "${private_key_path}" -pubout -out "${public_key_path}" >/dev/null 2>&1

echo "Generated PowerSync dev keys:"
echo "  ${private_key_path}"
echo "  ${public_key_path}"
echo
echo "Recommended .env values:"
echo "  POWERSYNC_JWT_PRIVATE_KEY_FILE=${private_key_path}"
echo "  POWERSYNC_JWT_PUBLIC_KEY_FILE=${public_key_path}"
echo "  POWERSYNC_JWT_KEY_ID=papyrus-powersync-dev"
echo "  POWERSYNC_JWT_AUDIENCE=powersync-dev"
