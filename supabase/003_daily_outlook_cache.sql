-- desktop clients read snapshots; only the trusted publisher writes them.

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

-- prune old forecast payloads daily.
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
