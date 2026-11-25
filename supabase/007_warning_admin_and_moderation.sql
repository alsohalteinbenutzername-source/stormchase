-- StormChase community warning polygons and report moderation.
-- Apply after 006_general_weather_forecasts.sql.
-- Admin membership is managed out-of-band by a trusted project owner using service_role.
-- Never grant users a client-side way to add themselves to stormchase_admins.

create table if not exists public.stormchase_admins (
    user_id uuid primary key references auth.users(id) on delete cascade,
    granted_at timestamptz not null default now()
);

alter table public.stormchase_admins enable row level security;
revoke all on public.stormchase_admins from anon, authenticated;

create or replace function public.is_stormchase_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
        from public.stormchase_admins admin
        where admin.user_id = (select auth.uid())
    );
$$;

revoke all on function public.is_stormchase_admin() from public, anon;
grant execute on function public.is_stormchase_admin() to authenticated;

create table if not exists public.storm_warnings (
    id uuid primary key default gen_random_uuid(),
    created_by uuid not null default auth.uid() references auth.users(id),
    category text not null check (
        category in ('damaging_wind', 'tornado', 'hail', 'heavy_rain')
    ),
    level smallint not null check (level between 1 and 3),
    level_tag text not null,
    threshold text,
    headline text not null check (length(btrim(headline)) between 1 and 120),
    description text not null check (length(btrim(description)) between 1 and 2000),
    polygon jsonb not null check (
        jsonb_typeof(polygon) = 'array'
        and jsonb_array_length(polygon) between 4 and 251
    ),
    status text not null default 'active' check (
        status in ('active', 'cancelled', 'expired')
    ),
    is_official boolean not null default false check (is_official = false),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    expires_at timestamptz not null check (expires_at > created_at),
    check (
        category <> 'tornado'
        or level <> 3
        or level_tag = 'CATASTROPHIC / TORNADO EMERGENCY'
    )
);

create index if not exists storm_warnings_active_idx
    on public.storm_warnings(status, expires_at desc);

alter table public.storm_warnings enable row level security;
drop policy if exists "Active community warnings are publicly readable"
    on public.storm_warnings;
create policy "Active community warnings are publicly readable"
    on public.storm_warnings for select to anon, authenticated
    using (status = 'active' and expires_at > now());

drop policy if exists "StormChase admins can issue community warnings"
    on public.storm_warnings;
create policy "StormChase admins can issue community warnings"
    on public.storm_warnings for insert to authenticated
    with check (
        public.is_stormchase_admin()
        and created_by = (select auth.uid())
        and is_official = false
    );

drop policy if exists "StormChase admins can update community warnings"
    on public.storm_warnings;
create policy "StormChase admins can update community warnings"
    on public.storm_warnings for update to authenticated
    using (public.is_stormchase_admin())
    with check (
        public.is_stormchase_admin()
        and is_official = false
    );

grant select on public.storm_warnings to anon, authenticated;
grant insert, update on public.storm_warnings to authenticated;

create table if not exists public.storm_warning_audit (
    id bigint generated always as identity primary key,
    warning_id uuid not null references public.storm_warnings(id) on delete cascade,
    actor_id uuid,
    action text not null,
    before_data jsonb,
    after_data jsonb,
    created_at timestamptz not null default now()
);

create index if not exists storm_warning_audit_recent_idx
    on public.storm_warning_audit(created_at desc);

alter table public.storm_warning_audit enable row level security;
drop policy if exists "Admins can read warning audit"
    on public.storm_warning_audit;
create policy "Admins can read warning audit"
    on public.storm_warning_audit for select to authenticated
    using (public.is_stormchase_admin());
revoke all on public.storm_warning_audit from anon, authenticated;
grant select on public.storm_warning_audit to authenticated;

create or replace function public.audit_storm_warning_changes()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if tg_op = 'INSERT' then
        insert into public.storm_warning_audit
            (warning_id, actor_id, action, after_data)
        values (new.id, auth.uid(), 'issued', to_jsonb(new));
        return new;
    end if;

    new.updated_at := now();
    insert into public.storm_warning_audit
        (warning_id, actor_id, action, before_data, after_data)
    values (
        new.id,
        auth.uid(),
        case when old.status = 'active' and new.status = 'cancelled'
             then 'cancelled' else 'updated' end,
        to_jsonb(old),
        to_jsonb(new)
    );
    return new;
end;
$$;

revoke all on function public.audit_storm_warning_changes() from public, anon, authenticated;
drop trigger if exists storm_warning_audit_trigger on public.storm_warnings;
create trigger storm_warning_audit_trigger
    before insert or update on public.storm_warnings
    for each row execute function public.audit_storm_warning_changes();

-- Community reports start unverified. Moderators can move them through
-- under_review -> corroborated/rejected; the original reporter cannot alter status.
alter table public.storm_reports
    add column if not exists verification_status text not null default 'unverified'
        check (verification_status in ('unverified', 'under_review', 'corroborated', 'rejected'));
alter table public.storm_reports
    add column if not exists reviewed_by uuid references auth.users(id);
alter table public.storm_reports
    add column if not exists reviewed_at timestamptz;
alter table public.storm_reports
    add column if not exists review_note text;

drop policy if exists "StormChase admins can moderate community reports"
    on public.storm_reports;
create policy "StormChase admins can moderate community reports"
    on public.storm_reports for update to authenticated
    using (public.is_stormchase_admin())
    with check (public.is_stormchase_admin());
grant update (verification_status, reviewed_by, reviewed_at, review_note)
    on public.storm_reports to authenticated;

create or replace function public.enforce_storm_report_moderation()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.verification_status is distinct from old.verification_status
       or new.reviewed_by is distinct from old.reviewed_by
       or new.reviewed_at is distinct from old.reviewed_at
       or new.review_note is distinct from old.review_note then
        if not public.is_stormchase_admin() then
            raise exception using errcode = '42501',
                message = 'Only StormChase administrators can moderate reports.';
        end if;
        new.reviewed_by := auth.uid();
        new.reviewed_at := now();
        if new.review_note is not null then
            new.review_note := left(btrim(new.review_note), 500);
        end if;
    end if;
    return new;
end;
$$;

revoke all on function public.enforce_storm_report_moderation() from public, anon, authenticated;
drop trigger if exists storm_report_moderation_trigger on public.storm_reports;
create trigger storm_report_moderation_trigger
    before update on public.storm_reports
    for each row execute function public.enforce_storm_report_moderation();

-- Efficient shared read: the desktop polls only this small metadata table.
create or replace function public.expire_stormchase_warnings()
returns void
language sql
security definer
set search_path = ''
as $$
    update public.storm_warnings
    set status = 'expired', updated_at = now()
    where status = 'active' and expires_at <= now();
$$;

revoke all on function public.expire_stormchase_warnings() from public, anon, authenticated;
