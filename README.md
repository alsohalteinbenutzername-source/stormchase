# StormChase

StormChase is a Windows desktop radar viewer built with Tkinter. It fetches
DWD reflectivity, radial velocity, rain echoes, and lightning locally while
running. Supabase is used for pseudonymous report identities, community reports,
and shared KONRAD3D cell-tracking and movement-prediction snapshots.

## Milestone

On 17 November, from approximately 5–6 PM, StormChase produced its first
(pretty much) verified tornado projection in a case near the Belgium-France
border, at Estinnes. The projection was issued with a 15–30% tornado risk and
was subsequently supported by a tornado debris signature observed across all
three radar parts: correlation coefficient (CC), velocity, and reflectivity.
Keraunos later reported a damaging wind phenomenon at Boiry-Becquerelle in
Pas-de-Calais, France, but noted that Doppler data showed no distinct rotation
and that the event's nature was still under investigation. That report does not
confirm a tornado at Estinnes or in Belgium
([Keraunos report](https://www.keraunos.org/actualites/fil-infos/2026/novembre/tornade-possible-boiry-becquerelle-pas-de-calais-arras-17-novembre-2026)).

## Run from source

Use Python 3.12 on Windows:

```powershell
python -m pip install -r requirements.txt
py -3.12 main.py
```

In VS Code, select **StormChase desktop app** in Run and Debug and press F5 to
open the app in its own window.

The map uses Esri World Imagery, shows Germany's country outline, and displays
the required Esri imagery attribution. An internet connection is required for
the background map. Zoom with the mouse wheel or the **Zoom +** / **Zoom −**
buttons; **Reset** returns to the full map view.

The live map's radar-product selector switches between reflectivity (dBZ) and
radial velocity (m/s). Radar frames and rotation detections are fetched during
the regular five-minute data updates. In velocity mode, click the map to
select the nearest active DWD Doppler station; its radar coverage is shown in
its correct map position. DWD's volume-scan mesocyclone detections are overlaid
as rotation symbols with five intensity levels: green (1), yellow (2), orange
(3), red (4), and pink/purple (5). Each symbol also identifies the reported
mesocyclone base and top in km above ground level. These levels and heights
come from DWD's native volume product, rather than being inferred from a
single 2-D velocity sweep. DWD's mesocyclone algorithm analyzes Doppler data
from the multi-elevation radar volumes and reports 3-D detections with
severity and base/top heights. The feed can arrive later than the velocity
image; if it is unavailable, the velocity image still loads and rotation
symbols are omitted until a later refresh. By default, only levels 3–5 are
drawn: DWD's published severity criteria define level 3 as meeting its
deep-mesocyclone thresholds, while levels 1–2 are weaker signatures. A symbol
is shown only when it is within 8 km of a KONRAD3D cell center or when the
reflectivity composite has at least 35 dBZ within 5 km. KONRAD3D supplies cell
centers, not polygon boundaries, so the distance is an approximate overlap
check. The reflectivity threshold is configurable and set to the midpoint of
the requested 30–40 dBZ range. These detections are algorithm guidance, not
confirmation of a tornado or an official warning. See the [DWD mesocyclone
algorithm paper](https://journals.ametsoc.org/doi/10.1175/JTECH-D-16-0230.1)
and [DWD radar product documentation](https://wetterdienst.readthedocs.io/en/latest/data/provider/dwd/radar/index.html).
The integrated publisher runs from this repository on its configured schedule; the app reads shared
KONRAD3D tracking snapshots and movement predictions from Supabase when configured.
KONRAD3D convective cells are shown with their observed movement history and a
dashed, one-hour movement prediction from Supabase when present, with an
ICON-D2 700 hPa wind projection as a local fallback. Wind direction is treated
as the direction the wind comes from. Connected 20–30 dBZ regions are
shown separately as rain echoes, with persistent IDs and fading, directional
movement tracks between scans. Echo patches are buffered by three radar pixels
before grouping, bridging gaps of up to about six pixels; isolated detections
smaller than 25 radar pixels are ignored. Echoes within one pixel of stronger
reflectivity are excluded to separate them from stronger storm cores. This is a
reflectivity-only proxy, not a meteorological precipitation-type classifier.
An internet connection and a valid Windows certificate store are required. If a
data refresh fails, the status bar shows the error and the last successfully
loaded map remains visible.

The **StormAnalyzer outlook** includes an **Outlook analysis** panel below the
map. When device location is enabled, the forecast calculation follows your
current location. Otherwise, enter latitude and longitude in the panel to show
one. Scroll within the panel to run a manual storm-risk calculation using the
same StormAnalyzer scoring.
The two-day risk map evaluates all 744 grid points across its displayed extent
using ICON-D2. Days 3–5 are not included, avoiding the additional ICON-EU grid
requests. Click **Load map** to fetch the outlook; opening the Outlook view does
not make a forecast request. Initial loading can take several minutes due to
upstream request pacing; loading and errors are shown on the map. Use **Refresh
map** to request updated data. Very-low overall-risk areas are hatched, while a
threat map with no qualifying areas reports that no elevated tier was forecast.
Best forecast hours are anchored to the daily grid's issue time, or local
midnight for an Open-Meteo fallback. Fallback ICON-D2 responses are cached
locally until the next Berlin midnight, so reopening does not change the day's
inputs or selected window. The overall risk score is out of 100; tornado,
hail, and wind scores are out of 80. The tornado sub-threat uses a
low-level interaction gate based on 0–500 m shear, 0–1 km storm-relative
helicity and efficiency, hodograph critical angle, LCL, and CIN. The manual
calculator requires all low-level profile inputs. The daily publisher stores
the raw hourly ICON-D2 grid; StormAnalyzer derives profile metrics from the
wind, temperature, humidity, and geopotential-height fields, and estimates
surface-parcel 0–3 km CAPE and LFC locally. A 6000 m LFC value denotes that no
LFC was found in the profile. Cached outlooks missing required raw profile
fields are refreshed before scoring.

Blitzortung / LightningMaps community-network lightning strokes appear as
yellow plus markers for up to 15 minutes on the live and DWD warning maps.
StormChase connects to the lightning feed locally while it is running. The
underlying feed is community-run and non-commercial, can be delayed or
incomplete, and is not an official safety service. Do not use it for life-safety
decisions or as a substitute for a certified lightning-detection feed.

## Supabase shared data and reports

The desktop fetches DWD reflectivity, velocity, KONRAD3D fallback data, and
lightning directly while running. Rain echoes and rotation detections are
derived locally from those radar frames. The StormAnalyzer outlook reads
today's shared ICON-D2 grid from Supabase when a publisher has populated it,
and otherwise fetches it from Open-Meteo. Point nowcasts are disabled to
conserve API limits. Warning overlays, point lookups, Esri imagery, and
cell-motion wind projections continue to use live third-party requests. When
configured, Supabase also supplies shared KONRAD3D tracking/prediction
snapshots and authenticated community-report features.

The separate general-weather analysis loads today's 744-point ICON-D2
environment from the daily outlook tables, fetches matching daily weather
extras from Open-Meteo, and combines them with StormAnalyzer. It stores two
days of WMO-coded conditions, precipitation and rain probabilities, amounts,
temperatures, wind, sunrise/sunset, UV index, and analysis in
`general_weather_forecasts` and `general_weather_forecast_points`. The analysis
uses WMO conditions, precipitation probability, and the ICON-D2 CAPE, wind,
temperature, humidity, and precipitation environment to estimate whether rain,
frozen precipitation, or a thunderstorm signal is likely.

1. In the Supabase SQL editor, run
   [`supabase/001_stormchase_schema.sql`](./supabase/001_stormchase_schema.sql).
   It creates the tables, indexes, read policies, authenticated report policies,
   and a `pg_cron` job that limits live-data observations to two hours. The job
   runs every five minutes. Storage cleanup must use the Storage API, not direct
   SQL deletion. Community reports are excluded from live-data retention. Then
   run [`supabase/003_daily_outlook_cache.sql`](./supabase/003_daily_outlook_cache.sql)
   to create the daily outlook cache. Then run
   [`supabase/005_daily_outlook_forecast_points.sql`](./supabase/005_daily_outlook_forecast_points.sql)
   to create the chunked outlook-point table. The desktop reads today's
   744-point ICON-D2 grid from that table in 100-row pages so PostgREST row caps
   do not truncate it. Only a trusted publisher using `service_role` can write
   it. The publisher stores each location's `grid_payload` separately, keyed
   by its index and the local `forecast_date` in `Europe/Berlin`, and records
   the API `issued_at` in the daily cache table. If no snapshot is published or
   its required raw profile fields are incomplete, the app fetches Open-Meteo;
   Supabase request errors are shown in the outlook UI instead of silently
   switching data sources. The publisher should refresh the grid once per day;
   a database job retains only today's and yesterday's rows. Then run
   [`supabase/006_general_weather_forecasts.sql`](./supabase/006_general_weather_forecasts.sql)
   to create the shared general-weather tables and their read policies. The
   general-weather workflow publishes its 744 locations in chunks and retains
   today's and yesterday's forecasts. Then run
   [`supabase/004_anonymous_reporters.sql`](./supabase/004_anonymous_reporters.sql)
   to configure pseudonymous report identities and posting limits.
2. In Supabase Auth, enable **Anonymous Sign-Ins**. The first time someone files
   a report, StormChase creates an anonymous Supabase session and asks for a
   unique 3–24 character username (letters, numbers, and underscores). The
   username is tied to that session, cannot be changed, and is attached to each
   report; passwords and email addresses are not collected. The session refresh
   token is stored in Windows Credential Manager. If the saved identity is
   lost, it cannot be recovered, and the old username remains reserved.
   Supabase RLS verifies the session and username for each report. The database
   limits each identity to five reports per ten minutes; because identities are
   anonymous, this does not prevent someone from creating another identity.
   Report text is limited to 2000 characters.
3. The publisher is included in `publisher/` and runs from this repository's
   GitHub Actions workflows. Add these repository **Actions secrets**:

   - `SUPABASE_URL`: the HTTPS URL of this Supabase project.
   - `SUPABASE_SERVICE_ROLE_KEY`: the server-side service-role key. Keep it
     only in GitHub Actions; never add it to `config.py`, the desktop app, or a
     distributed build.

   The live-data workflow runs every five minutes and publishes radar and
   lightning data without fetching the daily outlook. The separate
   daily-outlook workflow refreshes the shared ICON-D2 grid shortly after
   midnight in Germany (using `--daily-outlook-only`) and can also be started
   manually. The general-weather workflow uses `workflow_dispatch` and can be
   started manually or by cron-job.org. To configure cron-job.org, create a
   fine-grained GitHub token limited to this repository with **Actions: write**
   permission. Configure a POST job to
   `https://api.github.com/repos/OWNER/REPO/actions/workflows/general_weather.yml/dispatches`
   with these headers and body:

   ```text
   Accept: application/vnd.github+json
   Authorization: Bearer YOUR_FINE_GRAINED_TOKEN
   X-GitHub-Api-Version: 2022-11-28
   Content-Type: application/json
   ```

   ```json
   {"ref":"main"}
   ```

   Set the cron-job.org timezone to `Europe/Berlin` and choose the interval you
   want (for example, every three hours, starting at 00:30 so the daily outlook
   has time to publish first). Replace `OWNER/REPO` and `main` with
   your GitHub owner, repository, and default branch. Keep the token in
   cron-job.org's secure header settings; do not put it in the workflow file.
   GitHub Actions and scheduled workflows must be enabled in this
   repository's **Settings → Actions**.

   Supabase Cron can dispatch the live-data workflow every five minutes too.
   To enable that, create a fine-grained GitHub token restricted to this
   `stormchase` repository with **Actions: read and write** permission. Store
   it in Supabase Vault as `stormchase_github_dispatch_token`, then run
   [`supabase/002_github_publisher_scheduler.sql`](./supabase/002_github_publisher_scheduler.sql)
   in the Supabase SQL Editor. This Vault token is separate from the two
   GitHub Actions secrets above. The desktop uses only its public publishable
   key to read snapshots and anonymous Supabase sessions for community
   reports.
4. For a prototype release, set the project URL and publishable key in
   `config.py`:

   ```python
   SUPABASE_URL = "https://your-project.supabase.co"
   SUPABASE_PUBLISHABLE_KEY = "sb_publishable_..."
   ```

   The URL and publishable key are public settings and can be bundled with the
   desktop app. For local development, `STORMCHASE_SUPABASE_URL` and
   `STORMCHASE_SUPABASE_PUBLISHABLE_KEY` environment variables override the
   bundled values; the legacy `STORMCHASE_SUPABASE_ANON_KEY` variable is also
   supported. Never put a Supabase secret or `service_role` key in this app,
   its source, or a distributed build. Windows stores the anonymous session
   refresh token using the system credential store.

Without the URL and publishable key, local live data and outlook still work,
while Supabase tracking and report identities are unavailable. Automated
database-client tests use mocked responses. Verify the live project after
deploying the SQL and configuring the publisher; those services are not
available to the local test suite.

## Location privacy

Device location is off by default. Select **Enable device location**, accept the
in-app explanation, and then grant access in Windows. The blue location dot and
map center update as Windows reports position changes while tracking is enabled.
**Stop location tracking** stops updates and clears the displayed device
position. Location coordinates are kept in memory and are not sent to DWD. If
access is denied or unavailable, you can enter coordinates manually instead.

## Optional PyInstaller command

`main.py` is the desktop application entry point. When you are ready to build
the Windows app yourself, install the requirements and include the bundled
Germany outline asset:

```powershell
pyinstaller --onefile --windowed --add-data "assets\germany.geojson;assets" --add-data "assets\countries_110m.geojson;assets" main.py
```

PyInstaller writes the executable to `dist\main.exe`. Build on Windows for the
Windows target; PyInstaller does not cross-compile. No executable is included
with this source template.

## Data notes

Radar and storm-cell data come from the Deutscher Wetterdienst (DWD). Lightning
strokes come from the Blitzortung.org / LightningMaps.org community network.
Its feed is non-commercial and is not an official safety service. StormChase
is a situational-awareness display, not an official warning service. Continue
to follow official warnings and instructions. The map displays the composite
over an approximate Germany-wide coverage extent. The country outline in
`assets\germany.geojson` is from Natural Earth 1:10m Admin 0 Countries
(public domain). `assets\countries_110m.geojson` is from Natural Earth 1:110m
Admin 0 Countries (public domain) and supplies the outlook's simplified
country borders and coastlines.
