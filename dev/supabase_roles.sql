-- Local stand-in for the roles and grants a hosted Supabase project starts with.
-- Run as the bootstrap superuser (supabase_admin) on a fresh cluster, BEFORE the auth migrations.
-- Mirrors supabase/postgres init scripts closely enough to test schema.sql under the same
-- permissive defaults (anon/authenticated get EXECUTE and table rights on anything new in public).

create role postgres login createdb createrole replication bypassrls password 'postgres';
alter database postgres owner to postgres;

create role anon nologin noinherit;
create role authenticated nologin noinherit;
create role service_role nologin noinherit bypassrls;
create role authenticator login noinherit password 'authenticator';
grant anon, authenticated, service_role to authenticator;
grant anon, authenticated, service_role to postgres;

create role supabase_auth_admin login noinherit createrole password 'auth';
create schema auth authorization supabase_auth_admin;
grant usage on schema auth to anon, authenticated, service_role, postgres;
alter role supabase_auth_admin set search_path = auth;

create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;
create extension if not exists "uuid-ossp" with schema extensions;
grant usage on schema extensions to postgres, anon, authenticated, service_role;

grant usage, create on schema public to postgres;
grant usage on schema public to anon, authenticated, service_role;
alter default privileges for role postgres in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges for role postgres in schema public grant all on functions to anon, authenticated, service_role;
alter default privileges for role postgres in schema public grant all on sequences to anon, authenticated, service_role;

alter role anon set statement_timeout = '3s';
alter role authenticated set statement_timeout = '8s';
alter role postgres set search_path = public, extensions;
