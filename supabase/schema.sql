-- =====================================================================================================
-- CRICLE DATABASE
-- Paste this whole file into Supabase > SQL Editor > New query, and press Run.
-- Running it again later is safe: tables and data are kept, functions are rebuilt.
--
-- How it fits together
--   game.*      private tables (players, daily answers, results, nicknames). The website can't read
--               these directly; Supabase only exposes the "public" schema, and nothing here is granted.
--   public.*    the functions the website and the weekly refresh call. Each one checks who is asking
--               and only ever returns what that person is allowed to see. Today's answer is never sent
--               to the browser until that player has finished.
--   The tile colours are worked out here, using exactly the rules in pipeline/build_players.py
--   (the same rules the page uses for practice rounds).
-- =====================================================================================================

begin;

create schema if not exists game;
revoke all on schema game from public;

-- ----------------------------------------------------------------------------- tables

create table if not exists game.settings (
  id          boolean primary key default true check (id),
  launch      date,                                -- day 1 (UTC). Set by the first refresh, never changed after
  rules       jsonb not null default '{}'::jsonb,  -- bands, region, role_order, bowl_family, pool_rule (from the build)
  max_guesses int not null default 10,
  hint_at     int[] not null default '{5,7}',      -- the two hints unlock after this many guesses
  miss_score  int not null default 12,             -- leaderboard points for all out, or a day not played
  updated_at  timestamptz not null default now()
);
insert into game.settings (id) values (true) on conflict do nothing;

create table if not exists game.players (
  id         int primary key,                      -- English Wikipedia page id (stable across refreshes)
  name       text not null,
  countries  text[] not null default '{}',
  role       text,
  bats       text,
  bowls      text,
  debut      int,
  tests      int,
  odis       int,
  t20is      int,
  runs       int,
  wickets    int,
  pool       boolean not null default false,       -- can be a daily answer
  guessable  boolean not null default true,        -- false = dropped out of the latest build (row kept for history)
  test_bat   numeric,
  test_bowl  numeric,
  teams      text[] not null default '{}',
  wiki       text,
  born       text,
  last       int,
  updated_at timestamptz not null default now()
);

create table if not exists game.puzzles (
  day       int primary key check (day >= 0),      -- 0 = launch day (shown as #1)
  player_id int not null references game.players (id)
);

create table if not exists game.results (
  user_id     uuid not null references auth.users (id) on delete cascade,
  day         int not null,
  guesses     int[] not null default '{}',         -- player ids, in order
  hints       int not null default 0,              -- bit 1 = first hint opened, bit 2 = second
  done        boolean not null default false,
  won         boolean not null default false,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  primary key (user_id, day)
);
create index if not exists results_day_done_idx on game.results (day) where done;

create table if not exists game.profiles (
  user_id    uuid primary key references auth.users (id) on delete cascade,
  nickname   text not null,
  auto_name  boolean not null default true,        -- true until the player picks their own
  created_at timestamptz not null default now()
);
create unique index if not exists profiles_nickname_key on game.profiles (lower(nickname));

-- Moving a guest's results onto their Google account after they sign in on a new device
create table if not exists game.claims (
  token      uuid primary key default gen_random_uuid(),
  from_user  uuid not null references auth.users (id) on delete cascade,
  created_at timestamptz not null default now()
);

-- Words not allowed in nicknames. anywhere = true also blocks the word inside a longer one.
create table if not exists game.banned_words (
  word     text primary key,
  anywhere boolean not null default false
);
insert into game.banned_words (word, anywhere) values
  ('fuck', true), ('motherfucker', true), ('nigger', true), ('nigga', true), ('faggot', true), ('wanker', true),
  ('asshole', true), ('arsehole', true), ('bollocks', true), ('rapist', true), ('hitler', true), ('retard', true),
  ('shit', false), ('cunt', false), ('bitch', false), ('bastard', false), ('twat', false), ('dick', false),
  ('cock', false), ('pussy', false), ('slut', false), ('whore', false), ('fag', false), ('rape', false),
  ('nazi', false), ('porn', false), ('penis', false), ('vagina', false), ('prick', false), ('tosser', false),
  ('paki', false), ('chink', false), ('spastic', false), ('kike', false), ('coon', false), ('admin', false),
  ('cricle', false), ('moderator', false)
on conflict do nothing;

-- Caches so leaderboards stay quick with lots of players (rebuilt automatically, safe to empty)
create table if not exists game.day_summary (
  day         int primary key,
  counts      int[] not null,                     -- same layout as day_stats
  players     int not null,
  solved      int not null,
  avg_guesses numeric
);
create table if not exists game.board_rows (
  period  text not null,
  user_id uuid not null,
  points  int not null,
  played  int not null,
  won     int not null,
  last_at timestamptz,
  rnk     int not null,
  pos     int not null,
  primary key (period, user_id)
);
create index if not exists board_rows_pos_idx on game.board_rows (period, pos);
create table if not exists game.cache (
  key       text primary key,
  valid_for text not null,
  built_at  timestamptz not null,
  size      int,
  data      jsonb
);

alter table game.settings enable row level security;
alter table game.players enable row level security;
alter table game.puzzles enable row level security;
alter table game.results enable row level security;
alter table game.profiles enable row level security;
alter table game.claims enable row level security;
alter table game.banned_words enable row level security;
alter table game.day_summary enable row level security;
alter table game.board_rows enable row level security;
alter table game.cache enable row level security;

-- ----------------------------------------------------------------------------- rebuild functions
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
           where n.nspname = 'game' or (n.nspname = 'public' and p.proname in (
             'game_state', 'make_guess', 'open_hint', 'day_stats', 'my_stats', 'leaderboard', 'hall_of_fame',
             'past_answers', 'set_nickname', 'create_claim', 'redeem_claim', 'delete_my_account', 'health',
             'admin_export', 'admin_sync_players', 'admin_mark_guessable', 'admin_put_settings',
             'admin_put_schedule', 'admin_cleanup', 'admin_reset_game'))
  loop execute 'drop function ' || f.sig || ' cascade'; end loop;
end $$;

-- ----------------------------------------------------------------------------- helpers (private)

create function game.settings_row() returns game.settings
language sql stable set search_path = '' as $$ select * from game.settings where id $$;

-- Today's day number: 0 on launch day, changing at midnight UTC. Null before the first refresh.
create function game.today() returns int
language sql stable set search_path = '' as $$
  select ((now() at time zone 'utc')::date - launch) from game.settings where id
$$;

create function game.me() returns uuid
language plpgsql stable set search_path = '' as $$
declare u uuid := auth.uid();
begin
  if u is null then raise exception 'not_signed_in' using errcode = '28000'; end if;
  return u;
end $$;

create function game.is_guest(u uuid) returns boolean
language sql stable set search_path = '' as $$
  select coalesce((select is_anonymous from auth.users where id = u), true)
$$;

create function game.matches(p game.players) returns int
language sql immutable set search_path = '' as $$
  select case when p.tests is null or p.odis is null or p.t20is is null then null else p.tests + p.odis + p.t20is end
$$;

create function game.numeric_colour(tile text, g numeric, a numeric, bands jsonb) returns text
language plpgsql immutable set search_path = '' as $$
declare b jsonb := bands -> tile; d numeric;
begin
  if g is null or a is null then return 'dashed'; end if;
  d := abs(g - a);
  if tile = 'debut' then
    return case when d <= (b ->> 'green_years')::numeric then 'green'
                when d <= (b ->> 'gold_years')::numeric then 'gold' else 'grey' end;
  end if;
  if d <= greatest(a * (b ->> 'green_pct')::numeric / 100, (b ->> 'green_min')::numeric) then return 'green'; end if;
  if d <= greatest(a * (b ->> 'gold_pct')::numeric / 100, (b ->> 'gold_min')::numeric) then return 'gold'; end if;
  return 'grey';
end $$;

create function game.arrow(g numeric, a numeric) returns text
language sql immutable set search_path = '' as $$
  select case when g is null or a is null or g = a then '' when a > g then 'up' else 'down' end
$$;

-- The eight tiles for guess g against answer a. Mirrors compare() in the page and build_players.py.
create function game.tiles(g game.players, a game.players, rules jsonb) returns jsonb
language plpgsql immutable set search_path = '' as $$
declare
  region jsonb := rules -> 'region';
  fam    jsonb := rules -> 'bowl_family';
  ro     jsonb := rules -> 'role_order';
  bands  jsonb := rules -> 'bands';
  res    jsonb := '[]'::jsonb;
  shared text[]; reg text; colour text; gi int; ai int;
begin
  -- Country: green = played for a same country, gold = same region
  if cardinality(g.countries) = 0 or cardinality(a.countries) = 0 then
    res := res || jsonb_build_array(jsonb_build_object('tile', 'country', 'colour', 'dashed', 'arrow', ''));
  else
    shared := array(select c from unnest(g.countries) with ordinality u(c, i) where c = any (a.countries) order by i);
    if cardinality(shared) > 0 then
      res := res || jsonb_build_array(jsonb_build_object('tile', 'country', 'colour', 'green', 'arrow', '', 'detail', to_jsonb(shared)));
    else
      select region ->> c into reg from unnest(g.countries) with ordinality u(c, i)
       where region ->> c is not null
         and exists (select 1 from unnest(a.countries) x where region ->> x = region ->> c)
       order by i limit 1;
      res := res || jsonb_build_array(case when reg is null
        then jsonb_build_object('tile', 'country', 'colour', 'grey', 'arrow', '')
        else jsonb_build_object('tile', 'country', 'colour', 'gold', 'arrow', '', 'detail', reg) end);
    end if;
  end if;

  -- Role: gold = one step along the role order
  if g.role is null or a.role is null then colour := 'dashed';
  elsif g.role = a.role then colour := 'green';
  else
    select i - 1 into gi from jsonb_array_elements_text(ro) with ordinality u(v, i) where v = g.role;
    select i - 1 into ai from jsonb_array_elements_text(ro) with ordinality u(v, i) where v = a.role;
    colour := case when abs(coalesce(gi, -1) - coalesce(ai, -1)) = 1 then 'gold' else 'grey' end;
  end if;
  res := res || jsonb_build_array(jsonb_build_object('tile', 'role', 'colour', colour, 'arrow', ''));

  -- Bats: same hand or not
  colour := case when g.bats is null or a.bats is null then 'dashed' when g.bats = a.bats then 'green' else 'grey' end;
  res := res || jsonb_build_array(jsonb_build_object('tile', 'bats', 'colour', colour, 'arrow', ''));

  -- Bowls: gold = same family (pace or spin)
  colour := case when g.bowls is null or a.bowls is null then 'dashed'
                 when g.bowls = a.bowls then 'green'
                 when (fam ->> g.bowls) is not distinct from (fam ->> a.bowls) and (fam ->> g.bowls) is distinct from 'None' then 'gold'
                 else 'grey' end;
  res := res || jsonb_build_array(jsonb_build_object('tile', 'bowls', 'colour', colour, 'arrow', ''));

  -- Numbers, with arrows pointing to the answer
  res := res || jsonb_build_array(
    jsonb_build_object('tile', 'debut', 'colour', game.numeric_colour('debut', g.debut, a.debut, bands), 'arrow', game.arrow(g.debut, a.debut)),
    jsonb_build_object('tile', 'matches', 'colour', game.numeric_colour('matches', game.matches(g), game.matches(a), bands),
                       'arrow', game.arrow(game.matches(g), game.matches(a))),
    jsonb_build_object('tile', 'runs', 'colour', game.numeric_colour('runs', g.runs, a.runs, bands), 'arrow', game.arrow(g.runs, a.runs)),
    jsonb_build_object('tile', 'wickets', 'colour', game.numeric_colour('wickets', g.wickets, a.wickets, bands), 'arrow', game.arrow(g.wickets, a.wickets)));
  return res;
end $$;

create function game.public_facts(p game.players) returns jsonb
language sql immutable set search_path = '' as $$
  select jsonb_build_object('id', p.id, 'name', p.name, 'countries', to_jsonb(p.countries), 'role', p.role, 'bats', p.bats,
    'bowls', p.bowls, 'debut', p.debut, 'tests', p.tests, 'odis', p.odis, 't20is', p.t20is, 'runs', p.runs, 'wickets', p.wickets)
$$;

create function game.answer_facts(p game.players) returns jsonb
language sql immutable set search_path = '' as $$
  select game.public_facts(p) || jsonb_build_object('test_bat', p.test_bat, 'test_bowl', p.test_bowl,
    'teams', to_jsonb(p.teams), 'wiki', p.wiki, 'born', p.born, 'last', p.last)
$$;

create function game.hint_count(h int) returns int
language sql immutable set search_path = '' as $$ select (h & 1) + ((h >> 1) & 1) $$;

-- Histogram bucket: 1-10 = solved in that many guesses, 11 = all out
create function game.bucket(r game.results) returns int
language sql immutable set search_path = '' as $$ select case when r.won then cardinality(r.guesses) else 11 end $$;

-- Leaderboard points: guesses + hints for a solve, miss_score for all out. Lower is better.
create function game.points(r game.results, miss int) returns int
language sql immutable set search_path = '' as $$
  select case when r.won then cardinality(r.guesses) + game.hint_count(r.hints) else miss end
$$;

create function game.day_date(d int) returns date
language sql stable set search_path = '' as $$ select launch + d from game.settings where id $$;

-- Everything the page needs to draw one player's daily game.
create function game.game_json(u uuid, d int) returns jsonb
language plpgsql stable set search_path = '' as $$
declare
  s game.settings := game.settings_row();
  r game.results;
  a game.players;
  guess_list jsonb := '[]'::jsonb;
  g game.players;
  gid int;
  hint_list jsonb := '[]'::jsonb;
  k int;
  is_open boolean;
begin
  select p.* into a from game.puzzles z join game.players p on p.id = z.player_id where z.day = d;
  if not found then return null; end if;
  select * into r from game.results where user_id = u and day = d;
  if found then
    foreach gid in array r.guesses loop
      select * into g from game.players where id = gid;
      guess_list := guess_list || jsonb_build_array(jsonb_build_object('id', gid, 'v', game.public_facts(g), 'tiles', game.tiles(g, a, s.rules)));
    end loop;
  end if;
  for k in 0 .. cardinality(s.hint_at) - 1 loop
    is_open := coalesce(r.hints, 0) & (1 << k) <> 0;
    hint_list := hint_list || jsonb_build_array(jsonb_build_object('k', k, 'at', s.hint_at[k + 1], 'open', is_open,
      'data', case when is_open or coalesce(r.done, false) then
                case k when 0 then jsonb_build_object('tests', a.tests, 'test_bat', a.test_bat, 'test_bowl', a.test_bowl)
                       else jsonb_build_object('teams', to_jsonb(a.teams)) end end));
  end loop;
  return jsonb_build_object(
    'day', d,
    'guesses', guess_list,
    'hints', hint_list,
    'done', coalesce(r.done, false),
    'won', coalesce(r.won, false),
    'points', case when r.done then game.points(r, s.miss_score) end,
    'answer', case when r.done then game.answer_facts(a) end);
end $$;

-- Every player gets a cricket-y name until they choose one, e.g. "Gully 37".
create function game.ensure_profile(u uuid) returns game.profiles
language plpgsql volatile set search_path = '' as $$
declare
  prof game.profiles;
  spots text[] := array['Slip', 'Gully', 'Point', 'Cover', 'Mid-off', 'Mid-on', 'Midwicket', 'Square Leg', 'Fine Leg',
                        'Third Man', 'Long On', 'Long Off', 'Deep Cover', 'Silly Point', 'Short Leg', 'Leg Slip',
                        'Extra Cover', 'Cow Corner', 'Deep Point', 'Long Leg', 'Short Cover', 'Deep Midwicket'];
  nm text;
  tries int := 0;
begin
  select * into prof from game.profiles where user_id = u;
  if found then return prof; end if;
  loop
    tries := tries + 1;
    nm := spots[1 + floor(random() * cardinality(spots))::int] || ' ' ||
          (1 + floor(random() * case when tries <= 10 then 999 else 99999 end))::int;
    begin
      insert into game.profiles (user_id, nickname) values (u, nm) returning * into prof;
      return prof;
    exception when unique_violation then
      select * into prof from game.profiles where user_id = u;   -- a parallel call may have made it
      if found then return prof; end if;
      if tries > 40 then raise; end if;
    end;
  end loop;
end $$;

-- Longest run of consecutive daily wins for each player (best ever), and the day it ended.
create function game.best_streaks(only_user uuid) returns table (user_id uuid, streak int, ended int)
language sql stable set search_path = '' as $$
  with w as (select r.user_id, r.day, r.day - row_number() over (partition by r.user_id order by r.day) as grp
               from game.results r where r.done and r.won and (only_user is null or r.user_id = only_user)),
       runs as (select w.user_id, count(*)::int as len, max(w.day) as ended from w group by w.user_id, w.grp)
  select distinct on (runs.user_id) runs.user_id, runs.len, runs.ended from runs order by runs.user_id, runs.len desc, runs.ended
$$;

-- ----------------------------------------------------------------------------- what the website calls

-- On page load: today's number, my nickname and my game so far.
create function public.game_state() returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  u uuid := game.me();
  s game.settings := game.settings_row();
  t int := game.today();
  prof game.profiles := game.ensure_profile(u);
begin
  return jsonb_build_object(
    'today', t,
    'launch', s.launch,
    'now', now(),
    'max_guesses', s.max_guesses,
    'hint_at', to_jsonb(s.hint_at),
    'miss_score', s.miss_score,
    'me', jsonb_build_object('nickname', prof.nickname, 'auto_name', prof.auto_name, 'guest', game.is_guest(u)),
    'game', case when t is not null and t >= 0 then game.game_json(u, t) end);
end $$;

create function public.make_guess(p_day int, p_player int) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  u uuid := game.me();
  s game.settings := game.settings_row();
  t int := game.today();
  ans int;
  r game.results;
begin
  if t is null or p_day is distinct from t then raise exception 'day_over' using hint = 'The day has changed. Reload for the new cricketer.'; end if;
  select player_id into ans from game.puzzles where day = t;
  if not found then raise exception 'no_puzzle'; end if;
  if not exists (select 1 from game.players where id = p_player) then raise exception 'unknown_player'; end if;
  perform game.ensure_profile(u);
  insert into game.results (user_id, day) values (u, t) on conflict do nothing;
  select * into r from game.results where user_id = u and day = t for update;
  if not r.done and not (p_player = any (r.guesses)) then
    r.guesses := r.guesses || p_player;
    r.won := p_player = ans;
    r.done := r.won or cardinality(r.guesses) >= s.max_guesses;
    update game.results set guesses = r.guesses, won = r.won, done = r.done,
           finished_at = case when r.done then now() end
     where user_id = u and day = t;
  end if;
  return game.game_json(u, t);
end $$;

create function public.open_hint(p_day int, p_hint int) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  u uuid := game.me();
  s game.settings := game.settings_row();
  t int := game.today();
  r game.results;
begin
  if t is null or p_day is distinct from t then raise exception 'day_over'; end if;
  if p_hint is null or p_hint < 0 or p_hint >= cardinality(s.hint_at) then raise exception 'bad_hint'; end if;
  select * into r from game.results where user_id = u and day = t for update;
  if not found or cardinality(r.guesses) < s.hint_at[p_hint + 1] then raise exception 'hint_locked'; end if;
  if not r.done then
    update game.results set hints = hints | (1 << p_hint) where user_id = u and day = t;
  end if;
  return game.game_json(u, t);
end $$;

-- How everyone did on a day: counts[0..9] = solved in 1..10 guesses, counts[10] = all out.
-- Finished days are summarised once (they can't change after midnight); today is counted live.
create function game.summarise_past_days() returns void
language sql volatile set search_path = '' as $$
  insert into game.day_summary (day, counts, players, solved, avg_guesses)
  select z.day, c.counts, st.players, st.solved, st.avg_guesses
    from game.puzzles z
    cross join lateral (
      select array_agg(coalesce(k.n, 0) order by b.b)::int[] as counts
        from generate_series(1, 11) b(b)
        left join (select game.bucket(r) as bk, count(*)::int as n from game.results r
                    where r.day = z.day and r.done group by 1) k on k.bk = b.b) c
    cross join lateral (
      select count(*)::int as players, (count(*) filter (where r.won))::int as solved,
             round(avg(cardinality(r.guesses)) filter (where r.won), 1) as avg_guesses
        from game.results r where r.day = z.day and r.done) st
   where z.day < game.today() and not exists (select 1 from game.day_summary s where s.day = z.day)
  on conflict (day) do nothing
$$;

create function public.day_stats(p_day int default null) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  t int := game.today();
  d int := coalesce(p_day, t);
  c int[] := array_fill(0, array[11]);
  rec record;
  mine int;
begin
  if d is null or d < 0 or d > t then raise exception 'bad_day'; end if;
  if d < t then
    perform game.summarise_past_days();
    select counts into c from game.day_summary where day = d;
  else
    for rec in select game.bucket(r) as b, count(*)::int as n from game.results r where r.day = d and r.done group by 1 loop
      c[rec.b] := rec.n;
    end loop;
  end if;
  if auth.uid() is not null then
    select game.bucket(r) into mine from game.results r where r.user_id = auth.uid() and r.day = d and r.done;
  end if;
  return jsonb_build_object('day', d, 'counts', to_jsonb(c), 'mine', mine);
end $$;

-- My record across every device I've signed in on.
create function public.my_stats() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  u uuid := game.me();
  t int := game.today();
  s game.settings := game.settings_row();
  dist int[] := array_fill(0, array[11]);
  rec record;
  played int := 0; wins int := 0; cur int := 0; best int := 0; pts numeric;
  d int;
begin
  for rec in select game.bucket(r) as b, count(*)::int as n from game.results r where r.user_id = u and r.done group by 1 loop
    dist[rec.b] := rec.n; played := played + rec.n; if rec.b <= 10 then wins := wins + rec.n; end if;
  end loop;
  select coalesce(max(streak), 0) into best from game.best_streaks(u) b;
  -- current streak counts back from today if today's game is finished, otherwise from yesterday
  d := case when exists (select 1 from game.results where user_id = u and day = t and done) then t else t - 1 end;
  while exists (select 1 from game.results where user_id = u and day = d and done and won) loop
    cur := cur + 1; d := d - 1;
  end loop;
  select avg(game.points(r, s.miss_score)) into pts from game.results r where r.user_id = u and r.done;
  return jsonb_build_object('played', played, 'wins', wins, 'dist', to_jsonb(dist), 'current_streak', cur,
                            'best_streak', best, 'avg_points', round(pts, 2));
end $$;

-- Cached results are rebuilt on demand. With few players they're rebuilt on every request; once the
-- game is busy, at most every 15 to 60 seconds (the hall of fame every 10 minutes), by one request at a time.
create function game.cache_ttl(rows_last_time int, big boolean default false) returns interval
language sql immutable set search_path = '' as $$
  select case when coalesce(rows_last_time, 0) < 300 then interval '0'
              when big then interval '10 minutes'
              when rows_last_time < 3000 then interval '15 seconds'
              else interval '60 seconds' end
$$;

-- Returns true when the caller should rebuild the cache entry k for window w; waits for a rebuild already
-- running when there's nothing usable to show.
create function game.cache_needs_build(k text, w text, big boolean default false) returns boolean
language plpgsql volatile set search_path = '' as $$
declare c game.cache;
begin
  for attempt in 1 .. 2 loop
    select * into c from game.cache where key = k;
    if found and c.valid_for = w and c.built_at >= clock_timestamp() - game.cache_ttl(c.size, big) then return false; end if;
    if pg_try_advisory_xact_lock(hashtext('cricle:' || k)) then return true; end if;
    if found and c.valid_for = w then return false; end if;      -- someone is rebuilding; show the last one
    perform pg_advisory_xact_lock(hashtext('cricle:' || k));       -- nothing to show yet: wait for them
  end loop;
  return false;
end $$;

-- Leaderboards. 'today': points for today's game. 'week' / 'month': points added up over every day of
-- the current week (Monday to Sunday, UTC) or month so far, where a day not finished counts as miss_score.
create function public.leaderboard(p_period text default 'today', p_limit int default 100) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  s game.settings := game.settings_row();
  t int := game.today();
  u uuid := auth.uid();
  d0 int; d1 int; n int; w text;
  board jsonb; me jsonb; total int;
  my_points int; my_played int; my_won int; my_rank int;
begin
  if t is null or t < 0 then return jsonb_build_object('period', p_period, 'rows', '[]'::jsonb, 'players', 0); end if;
  d1 := t;
  d0 := case p_period
          when 'today' then t
          when 'week'  then greatest(0, date_trunc('week', (s.launch + t)::timestamp)::date - s.launch)
          when 'month' then greatest(0, date_trunc('month', (s.launch + t)::timestamp)::date - s.launch)
        end;
  if d0 is null then raise exception 'bad_period'; end if;
  n := d1 - d0 + 1;
  w := s.launch || ':' || d0 || '-' || d1;
  if game.cache_needs_build('board:' || p_period, w) then
    delete from game.board_rows where period = p_period;
    insert into game.board_rows (period, user_id, points, played, won, last_at, rnk, pos)
    select p_period, x.user_id, x.points, x.played, x.won, x.last_at,
           rank() over (order by x.points, x.won desc),
           row_number() over (order by x.points, x.won desc, x.played desc, x.last_at)
      from (select r.user_id,
                   sum(game.points(r, s.miss_score))::int + (n - count(*)::int) * s.miss_score as points,
                   count(*)::int as played, count(*) filter (where r.won)::int as won, max(r.finished_at) as last_at
              from game.results r where r.done and r.day between d0 and d1 group by r.user_id) x;
    get diagnostics total = row_count;
    insert into game.cache (key, valid_for, built_at, size) values ('board:' || p_period, w, clock_timestamp(), total)
    on conflict (key) do update set valid_for = excluded.valid_for, built_at = excluded.built_at, size = excluded.size;
  end if;
  select coalesce(jsonb_agg(jsonb_build_object('rank', b.rnk, 'name', coalesce(p.nickname, 'Player'), 'points', b.points,
                  'played', b.played, 'won', b.won, 'me', b.user_id = u) order by b.pos), '[]'::jsonb)
    into board
    from game.board_rows b left join game.profiles p on p.user_id = b.user_id
   where b.period = p_period and b.pos <= p_limit;
  select size into total from game.cache where key = 'board:' || p_period;
  -- my own row is always worked out fresh, ranked against the board
  if u is not null then
    select sum(game.points(r, s.miss_score))::int + (n - count(*)::int) * s.miss_score, count(*)::int,
           count(*) filter (where r.won)::int
      into my_points, my_played, my_won
      from game.results r where r.user_id = u and r.done and r.day between d0 and d1;
    if my_played > 0 then
      select 1 + count(*) into my_rank from game.board_rows b
       where b.period = p_period and b.user_id <> u and (b.points < my_points or (b.points = my_points and b.won > my_won));
      me := jsonb_build_object('rank', my_rank, 'name', (select nickname from game.profiles where user_id = u),
                               'points', my_points, 'played', my_played, 'won', my_won, 'me', true);
      if not exists (select 1 from game.board_rows where period = p_period and user_id = u) then total := total + 1; end if;
    end if;
  end if;
  return jsonb_build_object('period', p_period, 'from_day', d0, 'to_day', d1, 'days', n, 'miss_score', s.miss_score,
                            'rows', board, 'me', me, 'players', coalesce(total, 0));
end $$;

-- All-time honours: longest winning streak, most wins, best average (20+ games), most weeks won.
create function game.build_hall_of_fame(p_limit int) returns jsonb
language plpgsql volatile set search_path = '' as $$
declare
  s game.settings := game.settings_row();
  t int := game.today();
  streaks jsonb; wins jsonb; avgs jsonb; weeks jsonb;
begin
  select coalesce(jsonb_agg(jsonb_build_object('user_id', b.user_id, 'value', b.streak, 'day', b.ended)
                            order by b.streak desc, b.ended), '[]'::jsonb) into streaks
    from (select * from game.best_streaks(null) order by streak desc, ended limit p_limit) b;
  select coalesce(jsonb_agg(jsonb_build_object('user_id', w.user_id, 'value', w.n) order by w.n desc, w.first_at), '[]'::jsonb) into wins
    from (select r.user_id, count(*)::int as n, min(r.finished_at) as first_at from game.results r
           where r.done and r.won group by r.user_id order by n desc, first_at limit p_limit) w;
  select coalesce(jsonb_agg(jsonb_build_object('user_id', a.user_id, 'value', a.v, 'games', a.n) order by a.v, a.n desc), '[]'::jsonb) into avgs
    from (select r.user_id, round(avg(game.points(r, s.miss_score)), 2) as v, count(*)::int as n from game.results r
           where r.done group by r.user_id having count(*) >= 20 order by v, n desc limit p_limit) a;
  -- weeks that have finished (Monday to Sunday, UTC); the launch week counts from day 1
  with wk as (
    select greatest(0, ws - s.launch) as d0, (ws + 6) - s.launch as d1
      from generate_series(date_trunc('week', s.launch::timestamp), (s.launch + t - 7)::timestamp, interval '7 days') g(ws0),
           lateral (select g.ws0::date as ws) x
     where (ws + 6) - s.launch < t),
  tot as (
    select wk.d0, r.user_id, sum(game.points(r, s.miss_score))::int + ((wk.d1 - wk.d0 + 1) - count(*)::int) * s.miss_score as points
      from wk join game.results r on r.done and r.day between wk.d0 and wk.d1 group by wk.d0, wk.d1, r.user_id),
  top as (select tot.*, rank() over (partition by tot.d0 order by tot.points) as rnk from tot),
  champs as (select top.user_id, count(*)::int as n, max(top.d0) as latest from top where rnk = 1 group by top.user_id)
  select coalesce(jsonb_agg(jsonb_build_object('user_id', c.user_id, 'value', c.n) order by c.n desc, c.latest desc), '[]'::jsonb)
    into weeks
    from (select * from champs order by n desc, latest desc limit p_limit) c;
  return jsonb_build_object('streaks', streaks, 'wins', wins, 'average', avgs, 'weeks', weeks);
end $$;

create function public.hall_of_fame() returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  t int := game.today();
  u uuid := auth.uid();
  data jsonb;
  out_lists jsonb := '{}'::jsonb;
  k text;
  w text := (select launch from game.settings where id) || ':' || t;
begin
  if t is null or t < 0 then
    return jsonb_build_object('streaks', '[]'::jsonb, 'wins', '[]'::jsonb, 'average', '[]'::jsonb, 'weeks', '[]'::jsonb, 'min_games', 20);
  end if;
  if game.cache_needs_build('hall_of_fame', w, true) then
    data := game.build_hall_of_fame(10);
    insert into game.cache (key, valid_for, built_at, size, data)
    values ('hall_of_fame', w, clock_timestamp(), (select count(*) from game.results where done), data)
    on conflict (key) do update set valid_for = excluded.valid_for, built_at = excluded.built_at, size = excluded.size, data = excluded.data;
  else
    select c.data into data from game.cache c where key = 'hall_of_fame';
  end if;
  -- names are looked up fresh (so a new nickname shows straight away) and ids never leave the database
  foreach k in array array['streaks', 'wins', 'average', 'weeks'] loop
    out_lists := out_lists || jsonb_build_object(k, coalesce((
      select jsonb_agg((e - 'user_id') || jsonb_build_object('name', coalesce(p.nickname, 'Player'), 'me', (e ->> 'user_id')::uuid = u) order by i)
        from jsonb_array_elements(data -> k) with ordinality x(e, i)
        left join game.profiles p on p.user_id = (e ->> 'user_id')::uuid), '[]'::jsonb));
  end loop;
  return out_lists || jsonb_build_object('min_games', 20);
end $$;

-- Every past day's answer (today's appears from tomorrow), with how everyone did and my result.
create function public.past_answers(p_limit int default 400) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  t int := game.today();
  u uuid := auth.uid();
  res jsonb;
begin
  if t is null or t <= 0 then return '[]'::jsonb; end if;
  perform game.summarise_past_days();
  select coalesce(jsonb_agg(jsonb_build_object(
           'day', z.day, 'id', p.id, 'name', p.name, 'countries', to_jsonb(p.countries), 'role', p.role,
           'matches', game.matches(p), 'players', s.players, 'solved', s.solved, 'avg', s.avg_guesses,
           'mine', (select jsonb_build_object('won', r.won, 'n', cardinality(r.guesses), 'hints', game.hint_count(r.hints))
                      from game.results r where r.user_id = u and r.day = z.day and r.done)) order by z.day desc), '[]'::jsonb)
    into res
    from game.puzzles z join game.players p on p.id = z.player_id left join game.day_summary s on s.day = z.day
   where z.day < t and z.day >= t - greatest(p_limit, 1);
  return res;
end $$;

create function public.set_nickname(p_name text) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  u uuid := game.me();
  nm text := regexp_replace(btrim(coalesce(p_name, '')), '\s+', ' ', 'g');
  squashed text;
begin
  if char_length(nm) < 3 or char_length(nm) > 18 then raise exception 'name_length' using hint = 'Use 3 to 18 characters.'; end if;
  if nm !~ '^[[:alnum:]][[:alnum:] ._''-]*$' then raise exception 'name_chars' using hint = 'Letters, numbers, spaces and . _ '' - only.'; end if;
  squashed := regexp_replace(lower(nm), '[^[:alnum:]]', '', 'g');
  if exists (select 1 from game.banned_words b
              where b.word = any (regexp_split_to_array(lower(nm), '[^[:alnum:]]+'))
                 or (b.anywhere and position(b.word in squashed) > 0)) then
    raise exception 'name_rude' using hint = 'Please pick a different name.';
  end if;
  perform game.ensure_profile(u);
  begin
    update game.profiles set nickname = nm, auto_name = false where user_id = u;
  exception when unique_violation then
    raise exception 'name_taken' using hint = 'Someone already has that name.';
  end;
  return jsonb_build_object('nickname', nm);
end $$;

-- Signing in with Google on a device where you've played as a guest: before leaving for Google the page
-- asks for a claim token, and after signing in it redeems the token to move the guest results across.
create function public.create_claim() returns uuid
language plpgsql volatile security definer set search_path = '' as $$
declare u uuid := game.me(); tok uuid;
begin
  if not game.is_guest(u) then raise exception 'not_guest'; end if;
  delete from game.claims where from_user = u or created_at < now() - interval '1 day';
  insert into game.claims (from_user) values (u) returning token into tok;
  return tok;
end $$;

create function public.redeem_claim(p_token uuid) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  u uuid := game.me();
  src uuid;
  moved int := 0;
  mine game.profiles; theirs game.profiles;
begin
  if game.is_guest(u) then raise exception 'still_guest'; end if;
  delete from game.claims where token = p_token and created_at > now() - interval '1 hour' returning from_user into src;
  if src is null or src = u or not game.is_guest(src) then
    return jsonb_build_object('moved', 0, 'nickname', (select nickname from game.profiles where user_id = u));
  end if;
  -- results: keep the account's own game for any day both have, unless only the guest one is finished
  insert into game.results as cur (user_id, day, guesses, hints, done, won, started_at, finished_at)
    select u, r.day, r.guesses, r.hints, r.done, r.won, r.started_at, r.finished_at from game.results r where r.user_id = src
  on conflict (user_id, day) do update
    set guesses = excluded.guesses, hints = excluded.hints, done = excluded.done, won = excluded.won,
        started_at = excluded.started_at, finished_at = excluded.finished_at
    where not cur.done and excluded.done;
  get diagnostics moved = row_count;
  delete from game.results where user_id = src;
  -- nickname: a chosen name beats an automatic one; the account's own chosen name wins a tie
  select * into mine from game.profiles where user_id = u;
  select * into theirs from game.profiles where user_id = src;
  if theirs.user_id is not null and (mine.user_id is null or (mine.auto_name and not theirs.auto_name)) then
    delete from game.profiles where user_id = u;
    update game.profiles set user_id = u where user_id = src;
  else
    delete from game.profiles where user_id = src;
  end if;
  begin
    delete from auth.users where id = src and is_anonymous;   -- tidy up the guest account
  exception when insufficient_privilege then null;
  end;
  return jsonb_build_object('moved', moved, 'nickname', (select nickname from game.profiles where user_id = u));
end $$;

-- Deletes the signed-in player's results, nickname and account.
create function public.delete_my_account() returns boolean
language plpgsql volatile security definer set search_path = '' as $$
declare u uuid := game.me();
begin
  delete from game.results where user_id = u;
  delete from game.profiles where user_id = u;
  delete from game.claims where from_user = u;
  begin
    delete from auth.users where id = u;
  exception when insufficient_privilege then null;
  end;
  return true;
end $$;

-- For the daily health check: is there a puzzle scheduled well ahead?
create function public.health() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('ok', true, 'today', game.today(),
    'scheduled_ahead', (select count(*) from game.puzzles where day >= coalesce(game.today(), 0)),
    'players', (select count(*) from game.players where guessable),
    'updated_at', (select updated_at from game.settings where id))
$$;

-- ----------------------------------------------------------------------------- weekly refresh (secret key only)

create function public.admin_export() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object(
    'launch', (select launch from game.settings where id),
    'today', game.today(),
    'answers', coalesce((select jsonb_agg(player_id order by day) from game.puzzles), '[]'::jsonb),
    'days', (select count(*) from game.puzzles),
    'players', (select count(*) from game.players where guessable),
    'results', (select count(*) from game.results))
$$;

-- Upsert players. p_rows: [{id, name, countries, role, ...}, ...]
create function public.admin_sync_players(p_rows jsonb) returns int
language plpgsql volatile security definer set search_path = '' as $$
declare n int;
begin
  insert into game.players (id, name, countries, role, bats, bowls, debut, tests, odis, t20is, runs, wickets, pool,
                            guessable, test_bat, test_bowl, teams, wiki, born, last, updated_at)
  select x.id, x.name, coalesce(x.countries, '{}'), x.role, x.bats, x.bowls, x.debut, x.tests, x.odis, x.t20is, x.runs,
         x.wickets, coalesce(x.pool, false), true, x.test_bat, x.test_bowl, coalesce(x.teams, '{}'), x.wiki, x.born, x.last, now()
    from jsonb_to_recordset(p_rows) as x(id int, name text, countries text[], role text, bats text, bowls text, debut int,
         tests int, odis int, t20is int, runs int, wickets int, pool boolean, test_bat numeric, test_bowl numeric,
         teams text[], wiki text, born text, last int)
  on conflict (id) do update set
    name = excluded.name, countries = excluded.countries, role = excluded.role, bats = excluded.bats, bowls = excluded.bowls,
    debut = excluded.debut, tests = excluded.tests, odis = excluded.odis, t20is = excluded.t20is, runs = excluded.runs,
    wickets = excluded.wickets, pool = excluded.pool, guessable = true, test_bat = excluded.test_bat,
    test_bowl = excluded.test_bowl, teams = excluded.teams, wiki = excluded.wiki, born = excluded.born,
    last = excluded.last, updated_at = now();
  get diagnostics n = row_count;
  return n;
end $$;

-- After syncing every chunk: players not in this list are marked not guessable (rows are kept).
create function public.admin_mark_guessable(p_ids int[]) returns int
language plpgsql volatile security definer set search_path = '' as $$
declare n int;
begin
  update game.players set guessable = false, pool = false where guessable and not (id = any (p_ids));
  get diagnostics n = row_count;
  return n;
end $$;

create function public.admin_put_settings(p_rules jsonb) returns void
language plpgsql volatile security definer set search_path = '' as $$
begin
  if p_rules -> 'bands' is null or p_rules -> 'region' is null or p_rules -> 'role_order' is null or p_rules -> 'bowl_family' is null then
    raise exception 'rules incomplete';
  end if;
  update game.settings set rules = p_rules, updated_at = now() where id;
end $$;

-- Save the schedule from the build. Days up to and including today never change; future days are
-- replaced. The first call sets the launch date; after that it must match.
create function public.admin_put_schedule(p_launch date, p_answers int[]) returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare
  cur date := (select launch from game.settings where id);
  t int;
  added int := 0; replaced int := 0; d int; old int;
begin
  if cur is null then
    update game.settings set launch = p_launch, updated_at = now() where id;
    cur := p_launch;
  elsif cur <> p_launch then
    raise exception 'launch_mismatch' using detail = format('The live game started on %s but the build says %s.', cur, p_launch),
      hint = 'Set LAUNCH_DATE in pipeline/build_players.py back to the live date, or reset the game first (admin_reset_game).';
  end if;
  t := (now() at time zone 'utc')::date - cur;
  for d in 0 .. coalesce(cardinality(p_answers), 0) - 1 loop
    select player_id into old from game.puzzles where day = d;
    if not found then
      insert into game.puzzles (day, player_id) values (d, p_answers[d + 1]); added := added + 1;
    elsif d > t and old <> p_answers[d + 1] then
      update game.puzzles set player_id = p_answers[d + 1] where day = d; replaced := replaced + 1;
    end if;
  end loop;
  update game.settings set updated_at = now() where id;
  return jsonb_build_object('launch', cur, 'today', t, 'added', added, 'replaced', replaced,
                            'days', (select count(*) from game.puzzles));
end $$;

-- Weekly tidy-up (see docs/privacy.html): guest accounts that never finished a game are deleted after
-- 30 days, and guests who haven't played for 12 months are deleted too. Google accounts are never touched.
create function public.admin_cleanup() returns jsonb
language plpgsql volatile security definer set search_path = '' as $$
declare idle int := 0; old int := 0;
begin
  delete from auth.users u
   where u.is_anonymous and u.created_at < now() - interval '30 days'
     and not exists (select 1 from game.results r where r.user_id = u.id and r.done);
  get diagnostics idle = row_count;
  delete from auth.users u
   where u.is_anonymous
     and (select max(r.started_at) from game.results r where r.user_id = u.id) < now() - interval '12 months';
  get diagnostics old = row_count;
  delete from game.claims where created_at < now() - interval '1 day';
  return jsonb_build_object('guests_never_finished', idle, 'guests_inactive_a_year', old);
end $$;

-- Start again from scratch: deletes every result and the whole schedule, and clears the launch date.
-- Only for before going public. It can only be run from the SQL editor:  select admin_reset_game();
create function public.admin_reset_game() returns text
language plpgsql volatile security definer set search_path = '' as $$
begin
  delete from game.results;
  delete from game.claims;
  delete from game.puzzles;
  delete from game.day_summary;
  delete from game.board_rows;
  delete from game.cache;
  update game.settings set launch = null, updated_at = now() where id;
  return 'Reset. Run the weekly refresh to draw a new schedule from LAUNCH_DATE.';
end $$;

-- ----------------------------------------------------------------------------- who can call what

do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig, p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
           where n.nspname = 'public' and p.proname in (
             'game_state', 'make_guess', 'open_hint', 'day_stats', 'my_stats', 'leaderboard', 'hall_of_fame',
             'past_answers', 'set_nickname', 'create_claim', 'redeem_claim', 'delete_my_account', 'health',
             'admin_export', 'admin_sync_players', 'admin_mark_guessable', 'admin_put_settings',
             'admin_put_schedule', 'admin_cleanup', 'admin_reset_game')
  loop
    execute format('revoke all on function %s from public, anon, authenticated, service_role', f.sig);
    if f.proname in ('day_stats', 'leaderboard', 'hall_of_fame', 'past_answers', 'health') then
      execute format('grant execute on function %s to anon, authenticated', f.sig);
    elsif f.proname like 'admin\_%' then
      if f.proname <> 'admin_reset_game' then execute format('grant execute on function %s to service_role', f.sig); end if;
    else
      execute format('grant execute on function %s to authenticated', f.sig);
    end if;
  end loop;
end $$;

-- The private schema is never reachable from the website, even if someone exposes it by mistake.
revoke all on all functions in schema game from public, anon, authenticated, service_role;
revoke all on all tables in schema game from public, anon, authenticated, service_role;

-- Tell the API about the new functions straight away.
notify pgrst, 'reload schema';

commit;
