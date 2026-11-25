-- Shared daily general-weather and precipitation-probability grid.

create table if not exists public.general_weather_forecasts (
    forecast_date date primary key,
    issued_at timestamptz not null,
    published_at timestamptz not null default now()
);

create table if not exists public.general_weather_forecast_points (
    forecast_date date not null
        references public.general_weather_forecasts(forecast_date)
        on delete cascade,
    point_index integer not null check (point_index between 0 and 743),
    forecast_payload jsonb not null check (
        jsonb_typeof(forecast_payload) = 'object'
        and jsonb_typeof(forecast_payload->'days') = 'array'
    ),
    primary key (forecast_date, point_index)
);

alter table public.general_weather_forecasts enable row level security;
drop policy if exists "Today's general weather is readable by everyone"
    on public.general_weather_forecasts;
create policy "Today's general weather is readable by everyone"
    on public.general_weather_forecasts for select to anon, authenticated
    using (
        forecast_date = (now() at time zone 'Europe/Berlin')::date
    );

alter table public.general_weather_forecast_points enable row level security;
drop policy if exists "Today's general weather points are readable by everyone"
    on public.general_weather_forecast_points;
create policy "Today's general weather points are readable by everyone"
    on public.general_weather_forecast_points for select to anon, authenticated
    using (
        forecast_date = (now() at time zone 'Europe/Berlin')::date
    );

revoke all on public.general_weather_forecasts from public, anon, authenticated;
revoke all on public.general_weather_forecast_points from public, anon, authenticated;
grant select on public.general_weather_forecasts to anon, authenticated;
grant select on public.general_weather_forecast_points to anon, authenticated;
grant all on public.general_weather_forecasts to service_role;
grant all on public.general_weather_forecast_points to service_role;

do $$
declare
    existing_job_id bigint;
begin
    for existing_job_id in
        select jobid
        from cron.job
        where jobname = 'stormchase-general-weather-retention'
    loop
        perform cron.unschedule(existing_job_id);
    end loop;
end;
$$;

select cron.schedule(
    'stormchase-general-weather-retention',
    '30 0 * * *',
    $job$
        delete from public.general_weather_forecasts
        where forecast_date < (now() at time zone 'Europe/Berlin')::date - 1;
    $job$
);
