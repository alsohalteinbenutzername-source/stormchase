-- Shared daily ICON-D2 grid cache consumed by the desktop app.
-- A trusted publisher must write grid_payload using its service_role key.
-- The desktop uses only its public key and has read-only access.
--
-- grid_payload is the 744-element JSON array returned by
-- weather_api.get_germany_grid_weather(), with each item shaped as:
-- {"icon_d2":{"time":[...],"cape":[...],...,"cin":[...]}}

create table if not exists public.daily_outlook_forecasts (
    forecast_date date primary key,
    issued_at timestamptz not null,
    grid_payload jsonb not null check (jsonb_typeof(grid_payload) = 'array'),
    published_at timestamptz not null default now()
);

alter table public.daily_outlook_forecasts enable row level security;
drop policy if exists "Today's outlook is readable by everyone"
    on public.daily_outlook_forecasts;
create policy "Today's outlook is readable by everyone"
    on public.daily_outlook_forecasts for select to anon, authenticated
    using (
        forecast_date = (now() at time zone 'Europe/Berlin')::date
    );

revoke all on public.daily_outlook_forecasts from public, anon, authenticated;
grant select on public.daily_outlook_forecasts to anon, authenticated;
grant all on public.daily_outlook_forecasts to service_role;

-- Keep only today's and yesterday's snapshots.
do $$
declare
    existing_job_id bigint;
begin
    for existing_job_id in
        select jobid
        from cron.job
        where jobname = 'stormchase-daily-outlook-retention'
    loop
        perform cron.unschedule(existing_job_id);
    end loop;
end;
$$;

select cron.schedule(
    'stormchase-daily-outlook-retention',
    '15 0 * * *',
    $job$
        delete from public.daily_outlook_forecasts
        where forecast_date < (now() at time zone 'Europe/Berlin')::date - 1;
    $job$
);
