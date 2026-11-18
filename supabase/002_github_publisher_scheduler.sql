-- dispatch the publisher with a token stored in vault.

create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;
create extension if not exists supabase_vault with schema vault;

do $$
begin
    if not exists (
        select 1
        from vault.decrypted_secrets
        where name = 'stormchase_github_dispatch_token'
    ) then
        raise exception
            'Store the GitHub Actions dispatch token in Supabase Vault as stormchase_github_dispatch_token before applying this migration.';
    end if;
end;
$$;

create or replace function public.dispatch_stormchase_publisher()
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
    dispatch_token text;
begin
    select decrypted_secret
    into dispatch_token
    from vault.decrypted_secrets
    where name = 'stormchase_github_dispatch_token';

    if nullif(btrim(dispatch_token), '') is null then
        raise exception
            'Supabase Vault secret stormchase_github_dispatch_token is empty or missing.';
    end if;

    return net.http_post(
        url := 'https://api.github.com/repos/alsohalteinbenutzername-source/stormchase/actions/workflows/publish.yml/dispatches',
        body := jsonb_build_object('ref', 'main'),
        headers := jsonb_build_object(
            'Accept', 'application/vnd.github+json',
            'Authorization', 'Bearer ' || dispatch_token,
            'X-GitHub-Api-Version', '2022-11-28',
            'Content-Type', 'application/json',
            'User-Agent', 'StormChase-Supabase-Cron'
        ),
        timeout_milliseconds := 10000
    );
end;
$$;

revoke all on function public.dispatch_stormchase_publisher()
    from public, anon, authenticated;

do $$
declare
    existing_job_id bigint;
begin
    for existing_job_id in
        select jobid
        from cron.job
        where jobname = 'stormchase-github-publisher-dispatch'
    loop
        perform cron.unschedule(existing_job_id);
    end loop;
end;
$$;

select cron.schedule(
    'stormchase-github-publisher-dispatch',
    '*/5 * * * *',
    'select public.dispatch_stormchase_publisher();'
);
