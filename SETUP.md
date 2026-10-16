# Setting up Cricle

This puts Cricle on its own website, with Google sign-in, leaderboards and a weekly data refresh.
It takes about an hour, spread over a few sittings. Everything except the domain is free.

You'll create three accounts (GitHub, Supabase, a Google Cloud project) and buy one domain. Claude can't
create accounts or type passwords and keys for you, so those parts are yours. Everything else, Claude can do.

| Piece | What it does | Cost |
| --- | --- | --- |
| GitHub | Holds the code, hosts the website (GitHub Pages), runs the Monday refresh and a daily check | Free |
| Supabase | The game server: players, daily answers, results, sign-in | Free up to 50,000 players a month |
| Google Cloud | Lets people "Sign in with Google" | Free |
| playcricle.com | The address | About £8 to £10 a year |

Keep a note of these as you go (a password manager is ideal):
- Supabase project URL, publishable key, secret key, database password
- Google client ID and client secret

---

## 1. GitHub

1. Sign up at https://github.com/signup (or sign in).
2. Top right, **+** > **New repository**.
   - Repository name: `cricle`
   - **Public** (GitHub Pages is free for public repositories; no secrets are ever stored in the code)
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
   - Site URL: `https://playcricle.com`
   - Redirect URLs, add each of these:
     - `https://playcricle.com/**`
     - `https://YOURNAME.github.io/cricle/**` (until the domain is working)
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
3. **Branding**: app home page `https://playcricle.com`, privacy policy `https://playcricle.com/privacy.html`.
   Under authorised domains add `playcricle.com` and `xxxx.supabase.co` (your Supabase project's address,
   without `https://`). **Save**.
4. **Clients** > **Create client**:
   - Application type: **Web application**; name: `Cricle website`
   - Authorised JavaScript origins: `https://playcricle.com`
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
   These stay locked inside GitHub; only the Monday refresh can use them.
2. In the repository, open `docs/config.js`, press the pencil (edit), and fill in:
   ```js
   supabaseUrl: "https://xxxx.supabase.co",
   supabaseKey: "sb_publishable_...",
   ```
   **Commit changes**. (Or paste these two values to Claude; they're public.)

## 5. Day 1 and the first refresh

1. Choose the date of Daily #1 and tell Claude, who sets `LAUNCH_DATE` in `pipeline/build_players.py`.
2. **Settings** > **Pages** > Build and deployment > Source: **GitHub Actions**.
3. **Actions** tab. If asked, enable workflows. Open **Weekly data refresh** > **Run workflow**.
   It takes about 10 minutes: it fetches every player from Wikipedia, Wikidata and Cricsheet, adds photos,
   fills Supabase, draws the daily answers (with a secret random seed, so nobody can work them out from
   the code) and publishes the site.
4. When it's green, the game is live at `https://YOURNAME.github.io/cricle/`. Try it on your phone.

## 6. The domain

1. Buy `playcricle.com`. Porkbun (https://porkbun.com) is simple and includes free privacy protection;
   any registrar works. Check with Claude first that it's still available.
2. In GitHub: **Settings** > **Pages** > Custom domain: `playcricle.com` > **Save**.
3. At the registrar, open the domain's **DNS records**, delete any default "parking" records, and add:

   | Type | Host | Answer |
   | --- | --- | --- |
   | A | (blank or @) | 185.199.108.153 |
   | A | (blank or @) | 185.199.109.153 |
   | A | (blank or @) | 185.199.110.153 |
   | A | (blank or @) | 185.199.111.153 |
   | AAAA | (blank or @) | 2606:50c0:8000::153 |
   | AAAA | (blank or @) | 2606:50c0:8001::153 |
   | AAAA | (blank or @) | 2606:50c0:8002::153 |
   | AAAA | (blank or @) | 2606:50c0:8003::153 |
   | CNAME | www | YOURNAME.github.io |

4. Wait (usually under an hour) until GitHub's Pages settings say the DNS check succeeded, then tick
   **Enforce HTTPS**.
5. Recommended: verify the domain so nobody else can point a GitHub site at it. Your GitHub profile
   picture > **Settings** > **Pages** > **Add a domain**, and add the TXT record it shows at the registrar.

## 7. Before you tell anyone

1. Fill in your name and a contact email in `docs/privacy.html` (the two places marked `[OWNER NAME]` and
   `[CONTACT EMAIL]`). A contact address is needed for the privacy page and for Google.
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
- **Tile rules, the answer pool and the bands** live in the CONFIG block at the top of
  `pipeline/build_players.py`. Change them there; the next refresh updates the website and the server together.
- **Moderating names**: in the Supabase **SQL Editor**:
  `update game.profiles set nickname = 'Player 1', auto_name = true where lower(nickname) = 'rude name';`
  Add words to the blocklist with `insert into game.banned_words values ('word', false);`

## Limits worth knowing

- Supabase free: 50,000 monthly active players, 500 MB of data (a few years of results at a few thousand
  players a day), and no automatic backups. If Cricle takes off, Supabase Pro ($25 a month) adds daily
  backups and more room.
- GitHub Pages: about 100 GB of downloads a month. A first visit loads roughly 300 KB and repeat visits
  far less, so that's comfortably over 300,000 visits a month.
- Someone determined could make throwaway guest accounts to peek at the answer before playing properly.
  Every word game has this; the server makes sure nobody can see the answer, or the next day's, without
  playing.
