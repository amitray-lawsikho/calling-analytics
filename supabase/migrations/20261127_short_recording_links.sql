-- Shorter recording links (Amit, 27 Nov 2026): keep 60 days of call_rec well under the 500 MB free plan.
-- The common start of every link (and Acefone's fixed token ending) is kept once in rec_fmt, filled from the stored
-- links themselves, so nothing about the links is written in this repo. Each row keeps only the part that changes;
-- _rec_url() rebuilds the full link for the 50 rows a page shows. A link is shortened only when rebuilding it gives
-- back exactly the same text; anything else is kept whole.

create table if not exists public.rec_fmt (src char(1) primary key, base text not null, pname text, tail text);
alter table public.rec_fmt enable row level security;
revoke all on public.rec_fmt from anon, authenticated;

insert into public.rec_fmt (src, base, pname, tail)
select 'A', split_part(rec, '?', 1), split_part(split_part(rec, '?', 2), '=', 1),
       split_part(convert_from(decode(replace(split_part(rec, '&token=', 2), '%3D', '='), 'base64'), 'UTF8'), '::', 2)
from public.call_rec where src = 'A' and rec ~ '^https://[^?]+\?[A-Za-z_]+=[^&]+&type=rec&token=' limit 1
on conflict (src) do nothing;
insert into public.rec_fmt (src, base)
select 'M', substring(rec from '^(https://[^/]+/[^/]+/[^/]+/)') from public.call_rec where src = 'M' and rec ~ '^https://' limit 1
on conflict (src) do nothing;

-- short form -> full link: 'M<rest>' or 'A<call id>|<token body>'; a full link (http...) comes back unchanged
create or replace function public._rec_url(p text) returns text language sql stable security definer set search_path = public as $$
  select case
    when p is null or p = '' then null
    when p ~ '^https?://' then p
    when left(p, 1) = 'M' then (select f.base || substr(p, 2) from rec_fmt f where f.src = 'M')
    when left(p, 1) = 'A' then (select f.base || '?' || f.pname || '=' || split_part(substr(p, 2), '|', 1) || '&type=rec&token=' ||
        replace(replace(replace(replace(encode(convert_to(split_part(substr(p, 2), '|', 2) || '::' || f.tail, 'UTF8'), 'base64'),
          E'\n', ''), '=', '%3D'), '+', '%2B'), '/', '%2F')
      from rec_fmt f where f.src = 'A')
    else p end
$$;

-- full link -> short form, only when _rec_url gives the very same link back; never raises
create or replace function public._rec_pack(u text) returns text language plpgsql stable security definer set search_path = public as $$
declare f public.rec_fmt; p text; q text; tok text; dec text;
begin
  if u is null or u !~ '^https?://' then return u; end if;
  begin
    select * into f from rec_fmt where src = 'M';
    if found and left(u, length(f.base)) = f.base then
      p := 'M' || substr(u, length(f.base) + 1);
    else
      select * into f from rec_fmt where src = 'A';
      if found and left(u, length(f.base) + length(f.pname) + 2) = f.base || '?' || f.pname || '=' then
        q := split_part(substr(u, length(f.base) + length(f.pname) + 3), '&type=rec&token=', 1);
        tok := split_part(u, '&type=rec&token=', 2);
        dec := convert_from(decode(replace(replace(replace(tok, '%3D', '='), '%2B', '+'), '%2F', '/'), 'base64'), 'UTF8');
        if right(dec, length(f.tail) + 2) = '::' || f.tail and position('|' in q) = 0 then
          p := 'A' || q || '|' || left(dec, length(dec) - length(f.tail) - 2);
        end if;
      end if;
    end if;
    if p is not null and _rec_url(p) = u then return p; end if;
  exception when others then return u;
  end;
  return u;
end $$;

create or replace function public._rec_pack_trg() returns trigger language plpgsql set search_path = public as $$
begin
  if tg_table_name = 'call_rec' then new.rec := _rec_pack(new.rec); else new.recording := _rec_pack(new.recording); end if;
  return new;
end $$;

drop trigger if exists call_rec_pack on public.call_rec;
create trigger call_rec_pack before insert or update of rec on public.call_rec for each row execute function public._rec_pack_trg();
drop trigger if exists sus_calls_pack on public.sus_calls;
create trigger sus_calls_pack before insert or update of recording on public.sus_calls for each row execute function public._rec_pack_trg();

revoke all on function public._rec_url(text) from public, anon, authenticated;
revoke all on function public._rec_pack(text) from public, anon, authenticated;
revoke all on function public._rec_pack_trg() from public, anon, authenticated;

-- the two lists rebuild the link only for the rows they return
create or replace function public.all_list(p_from date, p_to date, p_teams text[] default null, p_verticals text[] default null,
  p_callers text[] default null, p_srcs text[] default null, p_number text default null, p_min int default 0, p_max int default null,
  p_ended text default null, p_offset int default 0, p_limit int default 50) returns json language sql stable security definer set search_path = public as $$
  with g as (
    select r.id, r.src, c.call_datetime, coalesce(t.caller_name, c.call_owner) caller, t.team_name team, c.client_number, r.talk_sec,
           r.agent_end, r.rec
    from call_rec r join calls c on c.id = r.id left join team_map t on t.merge_key = lower(trim(c.call_owner))
    where r.call_date between p_from and p_to and c.call_date between p_from and p_to
      and r.talk_sec >= coalesce(p_min, 0) and (p_max is null or r.talk_sec < p_max)
      and (p_ended is null or r.agent_end = (p_ended = 'agent'))
      and (p_number is null or c.client_number like '%' || p_number || '%')
      and _sus_pass(coalesce(t.caller_name, c.call_owner), t.team_name, t.vertical, r.src, p_teams, p_verticals, p_callers, p_srcs))
  select json_build_object('total', (select count(*) from (select 1 from g limit 10001) x),
    'rows', (select coalesce(json_agg(x), '[]') from (
      select id::text id, src, call_datetime, caller, team, client_number, talk_sec, agent_end, _rec_url(rec) recording
      from (select * from g order by call_datetime desc offset greatest(p_offset, 0) limit least(greatest(p_limit, 1), 200)) pg
      order by call_datetime desc) x))
  where (select sus_allowed());
$$;

create or replace function public.sus_list(p_from date, p_to date, p_max int default 5, p_lo int default 0, p_teams text[] default null, p_verticals text[] default null,
  p_callers text[] default null, p_srcs text[] default null, p_number text default null, p_repeat boolean default false,
  p_sort text default 'new', p_offset int default 0, p_limit int default 50) returns json language sql stable security definer set search_path = public as $$
  with f as (
    select s.*, coalesce(t.caller_name, s.caller) caller_name, t.team_name team,
      count(*) over (partition by s.caller, s.client_number) cuts_on_number
    from sus_calls s left join team_map t on t.merge_key = lower(trim(s.caller))
    where s.call_date between p_from and p_to and s.talk_sec <= p_max and (coalesce(p_lo, 0) = 0 or s.talk_sec > p_lo)
      and (p_number is null or s.client_number like '%' || p_number || '%')
      and _sus_pass(coalesce(t.caller_name, s.caller), t.team_name, t.vertical, s.src, p_teams, p_verticals, p_callers, p_srcs)),
  g as (select * from f where not p_repeat or cuts_on_number >= 2),
  pg as (select * from g order by case when p_sort = 'repeat' then cuts_on_number end desc nulls last,
                      case when p_sort = 'repeat' then client_number end, call_datetime desc
         offset greatest(p_offset, 0) limit least(greatest(p_limit, 1), 200))
  select json_build_object('total', (select count(*) from g),
    'rows', (select coalesce(json_agg(x), '[]') from (
      select id::text id, src, call_datetime, caller_name caller, team, client_number, talk_sec, cring_sec, _rec_url(recording) recording, cuts_on_number
      from pg order by case when p_sort = 'repeat' then cuts_on_number end desc nulls last,
                      case when p_sort = 'repeat' then client_number end, call_datetime desc) x))
  where (select sus_allowed());
$$;
