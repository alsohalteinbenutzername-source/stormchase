-- Store each outlook location independently so publishing does not require
-- one large JSONB write.

create table if not exists public.daily_outlook_forecast_points (
    forecast_date date not null references public.daily_outlook_forecasts(forecast_date)
        on delete cascade,
    point_index integer not null check (point_index between 0 and 743),
    grid_payload jsonb not null check (
        jsonb_typeof(grid_payload) = 'object'
        and grid_payload ? 'icon_d2'
    ),
    primary key (forecast_date, point_index)
);

alter table public.daily_outlook_forecast_points enable row level security;
drop policy if exists "Today's outlook points are readable by everyone"
    on public.daily_outlook_forecast_points;
create policy "Today's outlook points are readable by everyone"
    on public.daily_outlook_forecast_points for select to anon, authenticated
    using (
        forecast_date = (now() at time zone 'Europe/Berlin')::date
    );

revoke all on public.daily_outlook_forecast_points from public, anon, authenticated;
grant select on public.daily_outlook_forecast_points to anon, authenticated;
grant all on public.daily_outlook_forecast_points to service_role;
