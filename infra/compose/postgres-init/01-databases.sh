#!/bin/sh
# Separate application and identity databases, each with its own least-privilege user.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE motionmirror LOGIN PASSWORD '${MM_DB_PASSWORD}';
CREATE DATABASE motionmirror OWNER motionmirror;
CREATE ROLE keycloak LOGIN PASSWORD '${MM_KEYCLOAK_DB_PASSWORD}';
CREATE DATABASE keycloak OWNER keycloak;
SQL
