-- StormChase community warning polygons and report moderation.
-- Apply after 001_stormchase_schema.sql and 004_anonymous_reporters.sql.
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
        case
            when jsonb_typeof(polygon) = 'array'
            then jsonb_array_length(polygon) between 4 and 251
            else false
        end
    ),
    status text not null default 'active' check (
        status in ('active', 'cancelled', 'expired')
    ),
    is_official boolean not null default false check (is_official = false),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    expires_at timestamptz not null check (expires_at > created_at),
    check (
        (category = 'damaging_wind' and (
            (level = 1 and threshold = '≥58 mph / 93 km/h')
            or (level = 2 and threshold = '≥70 mph / 113 km/h')
            or (level = 3 and threshold = '≥80 mph / 129 km/h')
        ))
        or (category = 'hail' and (
            (level = 1 and threshold = '≥1.00 in / 25 mm')
            or (level = 2 and threshold = '≥1.75 in / 44 mm')
            or (level = 3 and threshold = '≥2.75 in / 70 mm')
        ))
        or (category = 'tornado' and threshold is null)
        or (category = 'heavy_rain' and (
            (level = 1 and threshold = '15–25 mm/1 h or 20–35 mm/6 h')
            or (level = 2 and threshold = '>25–40 mm/1 h or >35–60 mm/6 h')
            or (level = 3 and threshold = '>40 mm/1 h or >60 mm/6 h')
        ))
    ),
    check (
        (category = 'damaging_wind' and (
            (level = 1 and level_tag = 'BASE')
            or (level = 2 and level_tag = 'CONSIDERABLE')
            or (level = 3 and level_tag = 'DESTRUCTIVE')
        ))
        or (category = 'hail' and (
            (level = 1 and level_tag = 'BASE')
            or (level = 2 and level_tag = 'CONSIDERABLE')
            or (level = 3 and level_tag = 'DESTRUCTIVE')
        ))
        or (category = 'tornado' and (
            (level = 1 and level_tag = 'BASE')
            or (level = 2 and level_tag = 'CONSIDERABLE / PDS')
            or (level = 3 and level_tag = 'CATASTROPHIC / TORNADO EMERGENCY')
        ))
        or (category = 'heavy_rain' and (
            (level = 1 and level_tag = 'DWD LEVEL 2')
            or (level = 2 and level_tag = 'DWD LEVEL 3')
            or (level = 3 and level_tag = 'DWD LEVEL 4')
        ))
    )
);

create index if not exists storm_warnings_active_idx
    on public.storm_warnings(status, expires_at desc);

create or replace function public.validate_storm_warning_polygon()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
    vertex jsonb;
    first_vertex jsonb;
    last_vertex jsonb;
    previous_longitude double precision;
    previous_latitude double precision;
    current_longitude double precision;
    current_latitude double precision;
    vertex_count integer := 0;
    area_twice double precision := 0;
begin
    if jsonb_typeof(new.polygon) <> 'array'
       or jsonb_array_length(new.polygon) not between 4 and 251 then
        raise exception 'A warning polygon must contain 3 to 250 vertices plus closure.';
    end if;

    for vertex in select value from jsonb_array_elements(new.polygon)
    loop
        if jsonb_typeof(vertex) <> 'array'
           or jsonb_array_length(vertex) <> 2
           or jsonb_typeof(vertex -> 0) <> 'number'
           or jsonb_typeof(vertex -> 1) <> 'number' then
            raise exception 'Every warning polygon vertex must be [longitude, latitude].';
        end if;

        current_longitude := (vertex ->> 0)::double precision;
        current_latitude := (vertex ->> 1)::double precision;
        if current_longitude not between -180 and 180
           or current_latitude not between -90 and 90 then
            raise exception 'Warning polygon coordinates are outside geographic bounds.';
        end if;

        vertex_count := vertex_count + 1;
        if vertex_count = 1 then
            first_vertex := vertex;
        else
            area_twice := area_twice
                + previous_longitude * current_latitude
                - current_longitude * previous_latitude;
        end if;
        previous_longitude := current_longitude;
        previous_latitude := current_latitude;
        last_vertex := vertex;
    end loop;

    if vertex_count < 4 or first_vertex <> last_vertex then
        raise exception 'Warning polygon ring must contain at least 3 vertices and be closed.';
    end if;
    if abs(area_twice) < 1e-8 then
        raise exception 'Warning polygon area is zero or too small.';
    end if;

    return new;
end;
$$;

revoke all on function public.validate_storm_warning_polygon() from public, anon, authenticated;
drop trigger if exists storm_warning_polygon_validation_trigger on public.storm_warnings;
create trigger storm_warning_polygon_validation_trigger
    before insert or update of polygon on public.storm_warnings
    for each row execute function public.validate_storm_warning_polygon();

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
        case
            when old.status = 'active' and new.status = 'cancelled' then 'cancelled'
            when old.status = 'active' and new.status = 'expired' then 'expired'
            else 'updated'
        end,
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

create table if not exists public.storm_report_moderation_audit (
    id bigint generated always as identity primary key,
    report_id uuid not null references public.storm_reports(id) on delete cascade,
    actor_id uuid,
    previous_status text,
    new_status text not null,
    review_note text,
    created_at timestamptz not null default now()
);

create index if not exists storm_report_moderation_audit_recent_idx
    on public.storm_report_moderation_audit(created_at desc);

alter table public.storm_report_moderation_audit enable row level security;
drop policy if exists "Admins can read report moderation audit"
    on public.storm_report_moderation_audit;
create policy "Admins can read report moderation audit"
    on public.storm_report_moderation_audit for select to authenticated
    using (public.is_stormchase_admin());
revoke all on public.storm_report_moderation_audit from anon, authenticated;
grant select on public.storm_report_moderation_audit to authenticated;

create or replace function public.audit_storm_report_moderation()
returns trigger
language plpgsql
security definer
set search_path = ''
as $
begin
    if new.verification_status is distinct from old.verification_status
       or new.review_note is distinct from old.review_note then
        insert into public.storm_report_moderation_audit (
            report_id, actor_id, previous_status, new_status, review_note
        )
        values (
            new.id, auth.uid(), old.verification_status,
            new.verification_status, new.review_note
        );
    end if;
    return new;
end;
$;

revoke all on function public.audit_storm_report_moderation() from public, anon, authenticated;
drop trigger if exists storm_report_moderation_audit_trigger on public.storm_reports;
create trigger storm_report_moderation_audit_trigger
    after update on public.storm_reports
    for each row execute function public.audit_storm_report_moderation();

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

do $
declare
    existing_job_id bigint;
begin
    for existing_job_id in
        select jobid
        from cron.job
        where jobname = 'stormchase-community-warning-expiry'
    loop
        perform cron.unschedule(existing_job_id);
    end loop;
end;
$;

select cron.schedule(
    'stormchase-community-warning-expiry',
    '*/5 * * * *',
    'select public.expire_stormchase_warnings();'
);
