# StormChase

StormChase is a Windows desktop radar viewer built with Tkinter. It fetches
DWD reflectivity, radial velocity, rain echoes, and lightning locally while
running. Supabase is used for pseudonymous report identities, community reports,
and shared KONRAD3D cell-tracking and movement-prediction snapshots.

## Milestone

On 17 November, from approximately 5–6 PM, StormChase produced its first
(pretty much) verified tornado projection in a case near the Belgium-France
border, at Estinnes. The projection was issued with a 15–30% tornado risk and
was subsequently confirmed by a tornado debris signature observed across all
three radar parts: correlation coefficient (CC), velocity, and reflectivity.

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
radial velocity (m/s). Radar frames and rotation detections are fetched and
calculated locally from DWD during the regular five-minute data updates.
In velocity mode, click the map to select the nearest active DWD Doppler
station; its radar coverage is shown in its correct map position. The
local adjacent inbound/outbound velocity couplet detections (±15 m/s,
five pixels per side) are marked with a large rotation symbol. This is a
simple radar-velocity heuristic, not a tornado detector or official warning.
The separate publisher runs on its configured schedule; the app reads shared
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
helicity and efficiency, hodograph critical angle, LCL, and CIN. Low-level
lapse rate and optional 0–3 km CAPE/LFC values are also considered. The
additional 180 m wind fields are optional when reading older cached outlooks.

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

1. In the Supabase SQL editor, run
   [`supabase/001_stormchase_schema.sql`](./supabase/001_stormchase_schema.sql).
   It creates the tables, indexes, read policies, authenticated report policies,
   and a `pg_cron` job that limits live-data observations to two hours. The job
   runs every five minutes. Storage cleanup must use the Storage API, not direct
   SQL deletion. Community reports are excluded from live-data retention. Then
   run [`supabase/003_daily_outlook_cache.sql`](./supabase/003_daily_outlook_cache.sql)
   to create the daily outlook cache. The desktop can read today's snapshot
   from this table; only a trusted publisher using `service_role` can write it.
   Publish the 744-point ICON-D2 grid in `grid_payload`, in the same shape
   returned by `weather_api.get_germany_grid_weather()`, with its local
   `forecast_date` in `Europe/Berlin` and API `issued_at`. The app requests
   Open-Meteo if today's row is not available. The publisher should refresh the
   row once per day; a database job retains only today's and yesterday's rows.
   Then run
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
3. Configure the separate publisher to publish KONRAD3D tracking and movement
   prediction snapshots, and to populate the daily outlook cache using its
   trusted `service_role` key. The desktop app never writes shared snapshots;
   it writes community reports only through the user's RLS-protected session.
   Supabase Cron dispatches the publisher's GitHub Actions workflow every five
   minutes; it can also be started manually. Create a
   fine-grained GitHub token restricted to the publisher repository with
   **Actions: read and write**, then add it to Supabase Vault as
   `stormchase_github_dispatch_token`. Run
   [`supabase/002_github_publisher_scheduler.sql`](./supabase/002_github_publisher_scheduler.sql)
   after adding that Vault secret. Keep the publisher's server-side secret or
   legacy `service_role` key in GitHub Actions only. The desktop uses its
   public publishable key to read tracking snapshots and anonymous Supabase
   sessions for community reports.
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
deploying the SQL and configuring the external publisher; those services are
not available to the local test suite.

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
