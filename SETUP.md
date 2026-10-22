# Setting up Cricle

This puts Cricle on its own website, with Google sign-in, leaderboards and a weekly data refresh.
Step 0 gets it online today in practice mode. The rest takes about an hour, spread over a few sittings.
Everything except the domain is free.

You'll create four accounts (Netlify, GitHub, Supabase, a Google Cloud project) and buy one domain. Claude
can't create accounts or type passwords and keys for you, so those parts are yours. Everything else, Claude can do.

| Piece | What it does | Cost |
| --- | --- | --- |
| Netlify | Hosts the website | Free (see the limits at the end) |
| GitHub | Holds the code, runs the Monday refresh and a daily check | Free |
| Supabase | The game server: players, daily answers, results, sign-in | Free up to 50,000 players a month |
| Google Cloud | Lets people "Sign in with Google" | Free |
| playcricle.com | The address | About £8 to £10 a year |

Keep a note of these as you go (a password manager is ideal):
- Supabase project URL, publishable key, secret key, database password
- Google client ID and client secret

In the steps below, `cricle.netlify.app` stands for whatever Netlify address you end up with in step 0.

---

## 0. Netlify (online today, practice mode)

1. Sign up at https://app.netlify.com/signup (free). Sign up with GitHub if you already have it.
2. Go to https://app.netlify.com/drop and drag the **docs** folder from `Documents\cricle` onto the page.
   Drag the folder itself, not the files inside it.
3. When it's uploaded, open **Site configuration** > **Change site name** and type `cricle`
   (or `cricle-game` if that's taken). The site is now live at `https://cricle.netlify.app`.

Until Supabase is connected (steps 2 and 4), the site opens straight into practice rounds with a note that
the daily game is switching on soon. To update it by hand later, open the site in Netlify > **Deploys** and drag
the docs folder onto the box there. Once step 5 is done you never need to do this again.

## 1. GitHub

1. Sign up at https://github.com/signup (or sign in).
2. Top right, **+** > **New repository**.
   - Repository name: `cricle`
   - **Private**
   - Leave "Add a README" unticked.
   - **Create repository**.
3. Tell Claude the address (for example `https://github.com/yourname/cricle`). Claude uploads the code.

## 2. Supabase (the game server)

1. Go to https://supabase.com/dashboard and **Sign in with GitHub**.
2. **New project**.
   - Name: `cricle`
   - Database password: press **Generate a password** and save it.
   - Region: **West EU (London)**. The privacy page says data is held in London, so keep these matching.
   - Plan: Free. **Create new project** and wait a minute or two.
3. **SQL Editor** (left menu) > **New query**. Open `supabase/schema.sql`, copy all of it, paste it in, press
   **Run**. If it warns about a destructive operation, confirm. You should see "Success".
4. **Authentication** > **Sign In / Providers**: turn on **Allow anonymous sign-ins**, then **Save**.
   (Everyone plays as a guest first; Google sign-in is optional.)
5. **Authentication** > **Rate Limits**: raise the limit for anonymous sign-ins from 30 to **1000** an hour,
   then **Save**. The limit is per internet connection, and phone networks put thousands of people behind
   one connection.
6. **Authentication** > **URL Configuration**:
   - Site URL: `https://cricle.netlify.app` (change it to `https://playcricle.com` once the domain works)
   - Redirect URLs, add each of these:
     - `https://cricle.netlify.app/**`
     - `https://playcricle.com/**`
7. **Project Settings** > **API Keys**. Note the **Project URL** (`https://xxxx.supabase.co`), the
   **publishable key** (`sb_publishable_...`) and the **secret key** (`sb_secret_...`; press reveal, or
   create one if there isn't one).
   The publishable key is meant to be public. The secret key must never go in the website or in a chat.

## 3. Google sign-in

1. Go to https://console.cloud.google.com, sign in, and create a project called `Cricle`
   (project picker at the top > **New project**).
2. Search for **Google Auth Platform** and open it. Press **Get started**:
   - App name: `Cricle`; user support email: your email
   - Audience: **External**
   - Contact information: your email. Agree and **Create**.
3. **Branding**: app home page `https://cricle.netlify.app`, privacy policy `https://cricle.netlify.app/privacy.html`
   (switch both to playcricle.com once the domain works). Under authorised domains add `cricle.netlify.app`,
   `playcricle.com` and `xxxx.supabase.co` (your Supabase project's address, without `https://`). **Save**.
4. **Clients** > **Create client**:
   - Application type: **Web application**; name: `Cricle website`
   - Authorised JavaScript origins: `https://cricle.netlify.app` and `https://playcricle.com`
   - Authorised redirect URIs: `https://xxxx.supabase.co/auth/v1/callback`
     (Supabase shows this exact address on its Google provider page, with a copy button.)
   - **Create**. Copy the **client ID** and **client secret**.
5. **Audience** > **Publish app** > Confirm. Until you do, only test users can sign in.
6. Back in Supabase: **Authentication** > **Sign In / Providers** > **Google**: turn it on, paste the client ID
   and client secret, **Save**.

On the free Supabase plan, Google's sign-in screen says "continue to xxxx.supabase.co" rather than
playcricle.com. That's normal; showing your own domain there is a paid Supabase add-on.

## 4. Connect GitHub to Supabase

1. In your GitHub repository: **Settings** > **Secrets and variables** > **Actions** > **New repository secret**.
   Add two secrets:
   - `SUPABASE_URL` = the Project URL
   - `SUPABASE_SECRET_KEY` = the secret key
   These stay locked inside GitHub; only the Monday refresh can use them. (Netlify never needs them.)
2. In the repository, open `docs/config.js`, press the pencil (edit), and fill in:
   ```js
   supabaseUrl: "https://xxxx.supabase.co",
   supabaseKey: "sb_publishable_...",
   ```
   **Commit changes**. (Or paste these two values to Claude; they're public.)

## 5. Link Netlify to GitHub, then Day 1

1. In Netlify, open the site > **Site configuration** > **Build & deploy** > **Link repository** (it may say
   "Link site to Git") > **GitHub** > allow access > pick `cricle`. Netlify reads `netlify.toml` from the code,
   so leave the build settings as they come (publish directory `docs`, no build command) and save.
   From now on, any change to the website in GitHub, including the Monday data refresh, redeploys it by itself.
2. Choose the date of Daily #1 and tell Claude, who sets `LAUNCH_DATE` in `pipeline/build_players.py`.
3. GitHub **Actions** tab. If asked, enable workflows. Open **Weekly data refresh** > **Run workflow**.
   It takes about 10 minutes: it fetches every player from Wikipedia, Wikidata and Cricsheet, adds photos,
   fills Supabase, draws the daily answers (with a secret random seed, so nobody can work them out from
   the code) and commits the new data. Netlify then publishes it.
4. Open `https://cricle.netlify.app` on your phone: the daily game, leaderboards and sign-in are now on.

## 6. The domain

1. Buy `playcricle.com`. Porkbun (https://porkbun.com) is simple and includes free privacy protection;
   any registrar works. Check with Claude first that it's still available.
2. In Netlify: the site > **Domain management** > **Add a domain** > `playcricle.com` > follow the prompts.
   Choose "external DNS" (keep DNS at your registrar) unless you'd rather move it to Netlify.
3. Netlify then shows the exact records to add at your registrar: an **A** record for `playcricle.com`
   pointing at Netlify, and a **CNAME** for `www` pointing at `cricle.netlify.app`. At the registrar, open the
   domain's **DNS records**, delete any default "parking" records, and add those.
4. Within an hour or so Netlify confirms the domain and switches on HTTPS by itself.
5. Then update the playcricle.com references: Supabase Site URL (step 2.6) and the Google branding links
   (step 3.3). Tell Claude, who also points the link-preview image at the new address.

## 7. Before you tell anyone

1. The privacy page names the site as "Cricle" with the contact address playcricle@gmail.com (done).
   Use the same address as the support email for Google sign-in.
2. Play a game, sign in with Google, check the leaderboard, open it on a second device.
3. If you played test games before Daily #1, clear them: Supabase **SQL Editor** > new query >
   `select admin_reset_game();` > Run, then run **Weekly data refresh** again. This deletes every result and
   draws a new schedule. Never do this after launch.

---

## Running it

- **Every Monday** at 04:17 UTC the refresh runs by itself. Past answers never change. If the new data looks
  broken (lots of players vanished), it stops and publishes nothing; GitHub emails you that the run failed.
  Each run's data reports (`qa_report.csv` and friends) are on the run's page under Artifacts.
- **Every day** a health check asks the server how it's doing. It fails, and GitHub emails you, if fewer
  than 30 days of answers are scheduled, if the Monday refresh has stopped running, or if the server
  doesn't answer. It also stops a free Supabase project pausing for inactivity.
- **Changing who can be the answer** (POOL_MIN_MATCHES, POOL_MIN_MATCHES_OLDER, RECENT_FROM in the CONFIG block):
  the next refresh would stop at its safety check if the pool shrinks a lot. Run **Weekly data refresh** by hand
  and tick **Publish even if many players left the answer pool**. Past answers and today's stay as they are.
- **Tile rules, the answer pool and the bands** live in the CONFIG block at the top of
  `pipeline/build_players.py`. Change them there; the next refresh updates the website and the server together.
- **Moderating names**: in the Supabase **SQL Editor**:
  `update game.profiles set nickname = 'Player 1', auto_name = true where lower(nickname) = 'rude name';`
  Add words to the blocklist with `insert into game.banned_words values ('word', false);`

## Backups

- **Every Sunday** at 03:41 UTC, **Weekly backup** saves a copy of everything that can't be rebuilt: results,
  leaderboard names, accounts (with their Google sign-in links), the answer schedule, settings, sponsor counts
  and the players. Find it on the run's page in the Actions tab, under Artifacts. Each copy is kept 90 days.
- The repository is public, so each copy is encrypted before it's stored. GitHub only has the public key
  (`.github/backup_cert.pem`). The private key, `cricle-backup-private-key.pem`, is kept by the owner, never in
  GitHub. Without it the backups can't be opened, so keep it somewhere safe that isn't this repository.
- To open one (unzip the artifact first):
  `openssl cms -decrypt -binary -inform DER -in cricle-backup-DATE.json.gz.cms -inkey cricle-backup-private-key.pem -out backup.json.gz`
  It's a gzipped JSON file with one list per table. To put it back into Supabase, ask Claude.
- If a run fails, GitHub emails you. It also refuses to save a backup with no settings or schedule in it.

## Limits worth knowing

- Supabase free: 50,000 monthly active players, 500 MB of data (a few years of results at a few thousand
  players a day), and no automatic backups (the Weekly backup above covers that). If Cricle takes off, Supabase Pro ($25 a month) adds daily
  backups and more room.
- Netlify free (accounts opened since September 2025): 300 credits a month. Each deploy uses 15 and each GB
  of downloads uses 20. The Monday refresh uses about 75 a month, which leaves room for roughly 10 GB, about
  35,000 first visits a month (a first visit is roughly 300 KB; repeat visits mostly come from the visitor's
  own cache). If the credits run out, Netlify takes the site offline until the next month, so check
  **Usage** in Netlify now and then. If Cricle outgrows it, Netlify Pro or GitHub Pages (free, 100 GB a month)
  both work with the same code.
- GitHub free: 2,000 minutes a month of Actions for private repositories; Cricle uses about 70.
- Someone determined could make throwaway guest accounts to peek at the answer before playing properly.
  Every word game has this; the server makes sure nobody can see the answer, or the next day's, without
  playing.
