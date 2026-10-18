-- ===========================================================================
--  M-Biashara — reference Row Level Security policies for Supabase
--
--  STATUS: REFERENCE ONLY. NOT APPLIED. NOT RUN BY THE APPLICATION.
--
--  Nothing in the Django code path executes this file, and `manage.py migrate`
--  does not know about it. Read `docs/SUPABASE_REVIEW.md` before running
--  anything here: enabling policies without a dedicated, non-owner database
--  role changes nothing, because the table owner bypasses RLS.
--
--  Prerequisites for this file to have any effect:
--    1. A login role that is NOT the owner, e.g. `mb_app`, used by the web
--       service (SUPABASE_DB_USER=mb_app).
--    2. A separate owner/migrator role, used by `manage.py migrate` only.
--    3. `FORCE ROW LEVEL SECURITY` on each table (below), so even the owner
--       is subject to the policies.
--    4. The Django API remains the only writer. These policies exist for the
--       case where a browser, a BI tool or a leaked anon key reaches Postgres
--       directly. They are defence in depth, not the primary tenancy control.
--
--  Identity model
--  --------------
--  The API authenticates a custom JWT against `users_user.id` (bigint).
--  Supabase's `auth.uid()` is a uuid from Supabase Auth. They are different
--  identity stores, so a bridge table maps one to the other. If Supabase Auth
--  is not adopted, use the `app.user_id` variant at the end of this file.
--
--  Apply order: bridge table -> helper functions -> per-table policies ->
--  grants -> verification.
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- 0. Preconditions
-- ---------------------------------------------------------------------------
-- Create the application role out of band (Supabase dashboard or psql), with
-- a password held in the platform's secret manager. Do not commit it here.
--
--   create role mb_app login password '<from secret manager>' noinherit;
--   grant connect on database postgres to mb_app;
--   grant usage on schema public to mb_app;
--   -- then run every `grant ... to mb_app` statement in this file
--
-- The web service uses mb_app. `manage.py migrate` keeps using the owner
-- role. If mb_app does not exist this file stops before changing anything.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'mb_app') then
        raise exception
            'Role mb_app is missing. Create it first (section 0) and re-run.';
    end if;
end
$$;


-- ---------------------------------------------------------------------------
-- 1. Identity bridge
-- ---------------------------------------------------------------------------
create table if not exists public.mb_user_identity (
    django_user_id bigint primary key
        references public.users_user (id) on delete cascade,
    supabase_uid uuid unique not null
        references auth.users (id) on delete cascade,
    created_at timestamptz not null default now()
);

comment on table public.mb_user_identity is
    'Maps a Django users_user.id to a Supabase auth.users.id. One row per user.';

-- Uncomment when the bridge is populated by an admin/service import.
-- insert into public.mb_user_identity (django_user_id, supabase_uid)
-- values (123, '00000000-0000-0000-0000-000000000000')
-- on conflict (django_user_id) do nothing;

alter table public.mb_user_identity enable row level security;
alter table public.mb_user_identity force row level security;

revoke all on public.mb_user_identity from anon, authenticated;

create policy mb_identity_read_own
    on public.mb_user_identity
    for select
    to authenticated
    using (supabase_uid = auth.uid());


-- ---------------------------------------------------------------------------
-- 2. Helpers
-- ---------------------------------------------------------------------------
-- Returns the Django user id for the caller, or null when the caller has no
-- Supabase identity. Policies that compare against null match nothing, which
-- is the intended fail-closed behaviour.
create or replace function public.mb_current_user_id()
returns bigint
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select i.django_user_id
    from public.mb_user_identity i
    where i.supabase_uid = auth.uid()
$$;

revoke all on function public.mb_current_user_id() from public, anon;
grant execute on function public.mb_current_user_id() to authenticated;

-- Staff check, used only where a staff member may read more than their own
-- rows. `is_staff` is a Django column; it is never client-writable here.
create or replace function public.mb_is_staff()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select coalesce(
        (select u.is_staff from public.users_user u where u.id = public.mb_current_user_id()),
        false
    )
$$;

revoke all on function public.mb_is_staff() from public, anon;
grant execute on function public.mb_is_staff() to authenticated;


-- ---------------------------------------------------------------------------
-- 3. users
-- ---------------------------------------------------------------------------
-- The rows are needed by the app, but `password`, `national_id` and the
-- document fields must never be exposed through PostgREST. Two rules:
--   * revoke direct SELECT on the table from client roles;
--   * expose a view that drops the sensitive columns.
alter table public.users_user enable row level security;
alter table public.users_user force row level security;

revoke all on public.users_user from anon, authenticated;

create policy users_read_own
    on public.users_user
    for select
    to authenticated
    using (id = public.mb_current_user_id());

-- A user may correct their own contact details. Everything that grants
-- money, credit or verification is excluded by a column-level grant: the
-- policy allows the row, and the grant below allows only the phone column.
-- `national_id`, `verification_status`, `is_staff`, `is_superuser`,
-- `password` and the document paths therefore stay unwritable from a client
-- role even if a policy is ever loosened by mistake.
create policy users_update_own_profile
    on public.users_user
    for update
    to authenticated
    using (id = public.mb_current_user_id())
    with check (id = public.mb_current_user_id());

grant update (phone) on public.users_user to authenticated;

create policy users_staff_read_all
    on public.users_user
    for select
    to authenticated
    using (public.mb_is_staff());

-- The API role needs full access on the tables it owns the writes for.
-- Client roles (`anon`, `authenticated`) never receive these grants.
grant select, insert, update, delete on public.users_user to mb_app;

-- Safe projection for a future browser client.
create or replace view public.users_me
with (security_invoker = true)
as
select
    u.id,
    u.phone,
    u.verification_status,
    u.created_at
from public.users_user u
where u.id = public.mb_current_user_id();

revoke all on public.users_me from anon, authenticated;
grant select on public.users_me to authenticated;


-- ---------------------------------------------------------------------------
-- 4. loans
-- ---------------------------------------------------------------------------
alter table public.loans_loan enable row level security;
alter table public.loans_loan force row level security;

revoke all on public.loans_loan from anon, authenticated;

create policy loans_read_own
    on public.loans_loan
    for select
    to authenticated
    using (user_id = public.mb_current_user_id());

create policy loans_staff_read_all
    on public.loans_loan
    for select
    to authenticated
    using (public.mb_is_staff());

-- No INSERT/UPDATE/DELETE policy for authenticated: a loan's state is moved
-- only by the API, which re-checks the transition table inside a transaction.
grant select, insert, update, delete on public.loans_loan to mb_app;

-- Event history follows the loan it belongs to.
alter table public.loans_loanevent enable row level security;
alter table public.loans_loanevent force row level security;

revoke all on public.loans_loanevent from anon, authenticated;

create policy loan_events_read_own
    on public.loans_loanevent
    for select
    to authenticated
    using (
        exists (
            select 1
            from public.loans_loan l
            where l.id = public.loans_loanevent.loan_id
              and l.user_id = public.mb_current_user_id()
        )
    );

create policy loan_events_staff_read_all
    on public.loans_loanevent
    for select
    to authenticated
    using (public.mb_is_staff());

grant select, insert, update, delete on public.loans_loanevent to mb_app;


-- ---------------------------------------------------------------------------
-- 5. payments
-- ---------------------------------------------------------------------------
alter table public.payments_payment enable row level security;
alter table public.payments_payment force row level security;

revoke all on public.payments_payment from anon, authenticated;

create policy payments_read_own
    on public.payments_payment
    for select
    to authenticated
    using (user_id = public.mb_current_user_id());

create policy payments_staff_read_all
    on public.payments_payment
    for select
    to authenticated
    using (public.mb_is_staff());

grant select, insert, update, delete on public.payments_payment to mb_app;

-- Ledger and event rows are readable only through their payment.
alter table public.payments_ledgerentry enable row level security;
alter table public.payments_ledgerentry force row level security;

revoke all on public.payments_ledgerentry from anon, authenticated;

create policy ledger_read_own
    on public.payments_ledgerentry
    for select
    to authenticated
    using (
        exists (
            select 1
            from public.payments_payment p
            where p.id = public.payments_ledgerentry.payment_id
              and p.user_id = public.mb_current_user_id()
        )
    );

create policy ledger_staff_read_all
    on public.payments_ledgerentry
    for select
    to authenticated
    using (public.mb_is_staff());

grant select, insert, update, delete on public.payments_ledgerentry to mb_app;

alter table public.payments_paymentevent enable row level security;
alter table public.payments_paymentevent force row level security;

revoke all on public.payments_paymentevent from anon, authenticated;

create policy payment_events_read_own
    on public.payments_paymentevent
    for select
    to authenticated
    using (
        exists (
            select 1
            from public.payments_payment p
            where p.id = public.payments_paymentevent.payment_id
              and p.user_id = public.mb_current_user_id()
        )
    );

create policy payment_events_staff_read_all
    on public.payments_paymentevent
    for select
    to authenticated
    using (public.mb_is_staff());

grant select, insert, update, delete on public.payments_paymentevent to mb_app;

-- Provider deliveries contain every user's payload and the full raw body.
-- No client role reads them, and no policy grants access: with RLS forced and
-- no policy, the table is deny-by-default for owner and client alike.
alter table public.payments_webhookevent enable row level security;
alter table public.payments_webhookevent force row level security;

revoke all on public.payments_webhookevent from anon, authenticated;

-- Transfers and refunds are likewise server-side only; a user sees their
-- outcome through the loan and payment endpoints.
do $$
declare
    tbl text;
begin
    foreach tbl in array array[
        'payments_transfer',
        'payments_transferevent',
        'payments_refund'
    ]
    loop
        execute format('alter table public.%I enable row level security', tbl);
        execute format('alter table public.%I force row level security', tbl);
        execute format('revoke all on public.%I from anon, authenticated', tbl);
        execute format('grant select, insert, update, delete on public.%I to mb_app', tbl);
    end loop;
end
$$;


-- ---------------------------------------------------------------------------
-- 6. loyalty
-- ---------------------------------------------------------------------------
alter table public.loyalty_loyaltyaccount enable row level security;
alter table public.loyalty_loyaltyaccount force row level security;

revoke all on public.loyalty_loyaltyaccount from anon, authenticated;

create policy loyalty_account_read_own
    on public.loyalty_loyaltyaccount
    for select
    to authenticated
    using (user_id = public.mb_current_user_id());

create policy loyalty_account_staff_read_all
    on public.loyalty_loyaltyaccount
    for select
    to authenticated
    using (public.mb_is_staff());

grant select, insert, update, delete on public.loyalty_loyaltyaccount to mb_app;

-- The reward ledger. A user may read their own events; nobody may rewrite
-- them. There is deliberately no UPDATE or DELETE policy for `authenticated`.
alter table public.loyalty_loyaltyevent enable row level security;
alter table public.loyalty_loyaltyevent force row level security;

revoke all on public.loyalty_loyaltyevent from anon, authenticated;

create policy loyalty_events_read_own
    on public.loyalty_loyaltyevent
    for select
    to authenticated
    using (
        exists (
            select 1
            from public.loyalty_loyaltyaccount a
            where a.id = public.loyalty_loyaltyevent.account_id
              and a.user_id = public.mb_current_user_id()
        )
    );

create policy loyalty_events_staff_read_all
    on public.loyalty_loyaltyevent
    for select
    to authenticated
    using (public.mb_is_staff());

grant select, insert, update, delete on public.loyalty_loyaltyevent to mb_app;

-- Vault ledger: same shape. Cash-back can only move through the API, which
-- locks the account row first.
alter table public.loyalty_vaultentry enable row level security;
alter table public.loyalty_vaultentry force row level security;

revoke all on public.loyalty_vaultentry from anon, authenticated;

create policy vault_entries_read_own
    on public.loyalty_vaultentry
    for select
    to authenticated
    using (
        exists (
            select 1
            from public.loyalty_loyaltyaccount a
            where a.id = public.loyalty_vaultentry.account_id
              and a.user_id = public.mb_current_user_id()
        )
    );

create policy vault_entries_staff_read_all
    on public.loyalty_vaultentry
    for select
    to authenticated
    using (public.mb_is_staff());

grant select, insert, update, delete on public.loyalty_vaultentry to mb_app;

-- Provider-confirmed repayments and score history.
alter table public.loyalty_repaymentrecord enable row level security;
alter table public.loyalty_repaymentrecord force row level security;

revoke all on public.loyalty_repaymentrecord from anon, authenticated;

create policy repayments_read_own
    on public.loyalty_repaymentrecord
    for select
    to authenticated
    using (user_id = public.mb_current_user_id());

create policy repayments_staff_read_all
    on public.loyalty_repaymentrecord
    for select
    to authenticated
    using (public.mb_is_staff());

grant select, insert, update, delete on public.loyalty_repaymentrecord to mb_app;

alter table public.loyalty_creditsnapshot enable row level security;
alter table public.loyalty_creditsnapshot force row level security;

revoke all on public.loyalty_creditsnapshot from anon, authenticated;

create policy credit_snapshots_read_own
    on public.loyalty_creditsnapshot
    for select
    to authenticated
    using (user_id = public.mb_current_user_id());

create policy credit_snapshots_staff_read_all
    on public.loyalty_creditsnapshot
    for select
    to authenticated
    using (public.mb_is_staff());

grant select, insert, update, delete on public.loyalty_creditsnapshot to mb_app;


-- ---------------------------------------------------------------------------
-- 7. sms_audit_logs
-- ---------------------------------------------------------------------------
-- Internal delivery record. It contains a masked number, an HMAC of the
-- number and the caller's hashed IP — useful for staff, not for the user, and
-- a wider exposure surface if a token is stolen. No user-facing policy is
-- created on purpose: with RLS forced and no policy, client roles see nothing.
alter table public.users_smsauditlog enable row level security;
alter table public.users_smsauditlog force row level security;

revoke all on public.users_smsauditlog from anon, authenticated;

create policy sms_audit_staff_read_all
    on public.users_smsauditlog
    for select
    to authenticated
    using (public.mb_is_staff());

grant select, insert, update, delete on public.users_smsauditlog to mb_app;

-- OTP codes are stricter still: hashed, single-use, and never readable by a
-- client role or by staff through PostgREST. Staff can query through the
-- Django admin, which connects with the owner role.
alter table public.users_passwordresetcode enable row level security;
alter table public.users_passwordresetcode force row level security;

revoke all on public.users_passwordresetcode from anon, authenticated;

grant select, insert, update, delete on public.users_passwordresetcode to mb_app;


-- ---------------------------------------------------------------------------
-- 8. Indexes
-- ---------------------------------------------------------------------------
-- The provider-reference indexes are created by the Django migration
-- `payments.0004_provider_reference_indexes`:
--   payments_provider_ref_idx    on payments_payment (provider, provider_reference)
--   payments_provider_txn_idx    on payments_payment (provider_transaction_id)
--   transfers_provider_ref_idx   on payments_transfer (provider_reference)
--   transfers_provider_code_idx  on payments_transfer (provider_transfer_code)
--
-- They are intentionally NOT repeated here: two migrations creating the same
-- index under different names would leave a duplicate that doubles write cost.
--
-- The bridge table needs one index beyond its primary key for lookups by
-- Supabase user id; the UNIQUE constraint already provides it.


-- ---------------------------------------------------------------------------
-- 9. Verification (run as the owner, in a transaction you roll back)
-- ---------------------------------------------------------------------------
-- begin;
--   -- impersonate a signed-in Supabase user
--   set local role authenticated;
--   set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-000000000000"}';
--   select count(*) from public.loans_loan;      -- expect 0 or own rows only
--   select count(*) from public.users_user;      -- expect 0 (no direct grant)
--   select count(*) from public.users_me;        -- expect 1
--   select count(*) from public.users_smsauditlog; -- expect 0
-- rollback;
--
-- Also confirm the API role cannot bypass policies:
--   select relname, relrowsecurity, relforcerowsecurity
--   from pg_class
--   where relname in (
--     'users_user','loans_loan','payments_payment',
--     'loyalty_loyaltyaccount','users_smsauditlog'
--   );
--   -- all three columns must be true


-- ---------------------------------------------------------------------------
-- 10. Alternative without Supabase Auth
-- ---------------------------------------------------------------------------
-- If the project keeps its own JWT and never adopts Supabase Auth, replace
-- `auth.uid()` with a per-transaction GUC set by Django:
--
--   # connection setup (server side, after authentication)
--   with connection.cursor() as cur:
--       cur.execute("set local app.user_id = %s", [str(request.user.pk)])
--
-- and define the helper as:
--
--   create or replace function public.mb_current_user_id()
--   returns bigint language sql stable as $$
--       select nullif(current_setting('app.user_id', true), '')::bigint
--   $$;
--
-- This variant must be wired into the request path (middleware + connection
-- handling) and is NOT part of this change. Without that middleware every
-- policy resolves to null and denies everything, including the API's own
-- queries when it connects as `mb_app`.
