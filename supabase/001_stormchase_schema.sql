-- StormChase shared-data contract.
-- Run in the Supabase SQL editor. The scheduled collector must use service_role;
-- desktop clients use only the public anon key and are constrained by RLS.
-- Live radar, snapshot, and lightning data is retained for two hours.

grant usage on schema public to anon, authenticated;

create table if not exists public.live_snapshots (
    product text not null check (
        product in ('konrad3d', 'rain_cells', 'rotation_detections')
    ),
    scope text not null default '',
    observed_at timestamptz not null,
    published_at timestamptz not null default now(),
    payload jsonb not null check (jsonb_typeof(payload) = 'object'),
    primary key (product, scope, observed_at)
);

create index if not exists live_snapshots_latest_idx
    on public.live_snapshots (product, scope, observed_at desc);

alter table public.live_snapshots enable row level security;
drop policy if exists "Live snapshots are readable by everyone"
    on public.live_snapshots;
create policy "Live snapshots are readable by everyone"
    on public.live_snapshots for select to anon, authenticated
    using (observed_at >= now() - interval '2 hours');
grant select on public.live_snapshots to anon, authenticated;

create table if not exists public.radar_frames (
    product text not null check (product in ('reflectivity', 'velocity')),
    site_code text not null default '',
    observed_at timestamptz not null,
    published_at timestamptz not null default now(),
    storage_path text not null check (
        storage_path <> ''
        and storage_path !~ '(^/|/$|//|(^|/)\.\.?(/|$))'
    ),
    primary key (product, site_code, observed_at),
    check (
        (product = 'reflectivity' and site_code = '')
        or (product = 'velocity' and site_code <> '')
    )
);

create index if not exists radar_frames_latest_idx
    on public.radar_frames (product, site_code, observed_at desc);

alter table public.radar_frames enable row level security;
drop policy if exists "Radar frame metadata is readable by everyone"
    on public.radar_frames;
create policy "Radar frame metadata is readable by everyone"
    on public.radar_frames for select to anon, authenticated
    using (observed_at >= now() - interval '2 hours');
grant select on public.radar_frames to anon, authenticated;

insert into storage.buckets (id, name, public)
values ('stormchase-radar', 'stormchase-radar', true)
on conflict (id) do update set public = excluded.public;

drop policy if exists "StormChase radar frames are readable by everyone"
    on storage.objects;
create policy "StormChase radar frames are readable by everyone"
    on storage.objects for select to anon, authenticated
    using (bucket_id = 'stormchase-radar');

create table if not exists public.lightning_strikes (
    identifier text primary key check (identifier <> ''),
    latitude double precision not null check (latitude between -90 and 90),
    longitude double precision not null check (longitude between -180 and 180),
    observed_at timestamptz not null,
    quality text,
    uncertainty_km double precision check (
        uncertainty_km is null or uncertainty_km >= 0
    ),
    published_at timestamptz not null default now()
);

create index if not exists lightning_strikes_recent_idx
    on public.lightning_strikes (observed_at desc);

alter table public.lightning_strikes enable row level security;
drop policy if exists "Lightning observations are readable by everyone"
    on public.lightning_strikes;
create policy "Lightning observations are readable by everyone"
    on public.lightning_strikes for select to anon, authenticated
    using (observed_at >= now() - interval '2 hours');
grant select on public.lightning_strikes to anon, authenticated;

create table if not exists public.storm_reports (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    cell_id text not null check (cell_id <> ''),
    latitude double precision not null check (latitude between -90 and 90),
    longitude double precision not null check (longitude between -180 and 180),
    report text not null check (length(btrim(report)) between 1 and 2000),
    created_at timestamptz not null default now()
);

create index if not exists storm_reports_recent_idx
    on public.storm_reports (created_at desc);
create index if not exists storm_reports_cell_idx
    on public.storm_reports (cell_id, created_at desc);

alter table public.storm_reports enable row level security;
drop policy if exists "Authenticated users can read community reports"
    on public.storm_reports;
create policy "Authenticated users can read community reports"
    on public.storm_reports for select to authenticated
    using (auth.uid() is not null);
drop policy if exists "Users can create reports as themselves"
    on public.storm_reports;
create policy "Users can create reports as themselves"
    on public.storm_reports for insert to authenticated
    with check (auth.uid() = user_id);
grant select, insert on public.storm_reports to authenticated;

-- Prune expired live data every five minutes. Storage objects are removed
-- separately by the publisher through the Storage API; deleting rows from
-- storage.objects directly would leave orphaned files in the bucket.
create or replace function public.cleanup_stormchase_expired_data()
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    retention_cutoff timestamptz := now() - interval '2 hours';
begin
    delete from public.live_snapshots
    where observed_at < retention_cutoff;

    delete from public.radar_frames
    where observed_at < retention_cutoff;

    delete from public.lightning_strikes
    where observed_at < retention_cutoff;
end;
$$;

revoke all on function public.cleanup_stormchase_expired_data()
    from public, anon, authenticated;

create extension if not exists pg_cron;

do $$
declare
    existing_job_id bigint;
begin
    for existing_job_id in
        select jobid
        from cron.job
        where jobname = 'stormchase-two-hour-retention'
    loop
        perform cron.unschedule(existing_job_id);
    end loop;
end;
$$;

select cron.schedule(
    'stormchase-two-hour-retention',
    '*/5 * * * *',
    'select public.cleanup_stormchase_expired_data();'
);

-- Apply the limit immediately as well as on the recurring schedule.
select public.cleanup_stormchase_expired_data();

-- Publisher contract (all timestamps are ISO-8601 UTC):
-- * radar_frames upserts on (product, site_code, observed_at); upload the raw
--   DWD HDF5 object to stormchase-radar first, then store its relative object
--   path here. Reflectivity uses site_code=''; velocity uses the DWD site code.
-- * live_snapshots upserts on (product, scope, observed_at). Use scope='' for
--   KONRAD3D and rain_cells; use the DWD site code for rotation_detections.
--   KONRAD3D payload:
--   {"cells":[{"identifier":"...", "latitude":0, "longitude":0,
--   "reflectivity_dbz":null, "severity":null, "track":[[0,0]],
--   "forecast":[]}]} where each track point is [latitude, longitude].
--   rain_cells payload: {"cells":[{"identifier":"...", "latitude":0,
--   "longitude":0, "max_reflectivity_dbz":0, "area_pixels":0,
--   "track":[{"latitude":0,"longitude":0,"timestamp":"...",
--   "reflectivity_dbz":0}]}]}.
--   rotation_detections payload:
--   {"detections":[{"latitude":0,"longitude":0,
--   "negative_pixels":0,"positive_pixels":0}]}.
--   Rotation markers represent adjacent inbound/outbound regions with at least
--   five pixels on each side at or beyond +/-15 m/s.
--   Publish empty arrays when a scan has no cells/detections so clients can
--   clear the previous scan rather than keeping old markers on the map.
-- * lightning_strikes upserts on identifier (for example "17/42"), with
--   latitude, longitude, observed_at, quality, and uncertainty_km.
-- * storm_reports are created by authenticated app clients; the publisher
--   should not write reports or use a client-supplied user_id as authority.
-- The two-hour retention job removes expired live-feed rows. The publisher
-- must also remove expired radar files using the Storage API. Community reports
-- are intentionally retained and are not part of live-feed cleanup.
