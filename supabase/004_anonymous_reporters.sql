create table if not exists public.stormchase_profiles (
    user_id uuid primary key references auth.users (id) on delete cascade,
    username text not null check (username ~ '^[a-z0-9_]{3,24}$'),
    created_at timestamptz not null default now()
);

create unique index if not exists stormchase_profiles_username_idx
    on public.stormchase_profiles (lower(username));

alter table public.stormchase_profiles enable row level security;
drop policy if exists "Report usernames are readable by authenticated users"
    on public.stormchase_profiles;
create policy "Report usernames are readable by authenticated users"
    on public.stormchase_profiles for select to authenticated
    using (true);
drop policy if exists "Anonymous users can set their own report username"
    on public.stormchase_profiles;
create policy "Anonymous users can set their own report username"
    on public.stormchase_profiles for insert to authenticated
    with check (
        user_id = (select auth.uid())
        and coalesce((select auth.jwt() ->> 'is_anonymous')::boolean, false)
    );
revoke all on public.stormchase_profiles from anon, authenticated;
grant select, insert on public.stormchase_profiles to authenticated;

insert into public.stormchase_profiles (user_id, username)
select id, lower(split_part(email, '@', 1))
from auth.users
where email ~* '^[a-z0-9_]{3,24}@(example\.com|login\.stormchase\.invalid)$'
on conflict do nothing;

alter table public.storm_reports
    add column if not exists reporter_username text;

update public.storm_reports as report
set reporter_username = coalesce(
    profile.username,
    nullif(split_part(auth_user.email, '@', 1), ''),
    'former reporter'
)
from auth.users as auth_user
left join public.stormchase_profiles as profile
    on profile.user_id = auth_user.id
where report.user_id = auth_user.id
    and report.reporter_username is null;

update public.storm_reports
set reporter_username = 'former reporter'
where reporter_username is null;

alter table public.storm_reports
    alter column reporter_username set not null;

create index if not exists storm_reports_user_recent_idx
    on public.storm_reports (user_id, created_at desc);

drop policy if exists "Users can create reports as themselves"
    on public.storm_reports;
create policy "Users can create reports as themselves"
    on public.storm_reports for insert to authenticated
    with check (
        (select auth.uid()) = user_id
        and exists (
            select 1
            from public.stormchase_profiles as profile
            where profile.user_id = (select auth.uid())
                and profile.username = reporter_username
        )
    );

create or replace function public.enforce_storm_report_rate_limit()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    recent_report_count integer;
begin
    if new.user_id is distinct from (select auth.uid()) then
        raise exception using
            errcode = '42501',
            message = 'Reports must be submitted as the current user.';
    end if;

    new.created_at := pg_catalog.clock_timestamp();

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(new.user_id::text, 0)
    );

    select count(*)
    into recent_report_count
    from public.storm_reports
    where user_id = new.user_id
        and created_at >= pg_catalog.clock_timestamp() - interval '10 minutes';

    if recent_report_count >= 5 then
        raise exception using
            errcode = 'P0001',
            message = 'Report limit reached. Wait before submitting more reports.';
    end if;

    return new;
end;
$$;

revoke all on function public.enforce_storm_report_rate_limit()
    from public, anon, authenticated;

drop trigger if exists storm_reports_rate_limit_trigger
    on public.storm_reports;
create trigger storm_reports_rate_limit_trigger
    before insert on public.storm_reports
    for each row
    execute function public.enforce_storm_report_rate_limit();
