# StormChase

StormChase is a Windows desktop radar viewer built with Tkinter. It fetches
DWD reflectivity, radial velocity, rain echoes, and lightning locally while
running. Supabase is used for sign-in, community reports, and shared KONRAD3D
cell-tracking and movement-prediction snapshots.

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

Blitzortung / LightningMaps community-network lightning strokes appear as
yellow plus markers for up to 15 minutes on the live and DWD warning maps.
StormChase connects to the lightning feed locally while it is running. The
underlying feed is community-run and non-commercial, can be delayed or
incomplete, and is not an official safety service. Do not use it for life-safety
decisions or as a substitute for a certified lightning-detection feed.

## Supabase shared data and reports

The desktop fetches DWD reflectivity, velocity, KONRAD3D fallback data, and
lightning directly while running. Rain echoes and rotation detections are
derived locally from those radar frames. The StormAnalyzer outlook, warning
overlays, point lookups, Esri imagery, and cell-motion wind projections also
use direct third-party requests. When configured, Supabase supplies shared
KONRAD3D tracking/prediction snapshots and authenticated community-report
features; Supabase being unavailable does not prevent local radar or outlook
from loading.

1. In the Supabase SQL editor, run
   [`supabase/001_stormchase_schema.sql`](./supabase/001_stormchase_schema.sql).
   It creates the tables, indexes, read policies, authenticated report policies,
   and a `pg_cron` job that limits live-data observations to two hours. The job
   runs every five minutes. Storage cleanup must use the Storage API, not direct
   SQL deletion. Community reports are excluded from live-data retention.
2. Enable email/password authentication in Supabase Auth. Configure email
   confirmation as desired. Signed-in users can read community reports; RLS
   only allows each signed-in user to create reports as themselves. Report
   text is limited to 2000 characters.
3. Configure the separate publisher to publish KONRAD3D tracking and movement
   prediction snapshots only. Keep its server-side `service_role` secret in
   GitHub Actions only. The desktop uses its public anon key to read tracking
   snapshots and email/password Auth for reports.
4. Set the project URL and public anon key in the desktop launch environment:

   ```powershell
   $env:STORMCHASE_SUPABASE_URL = "https://your-project.supabase.co"
   $env:STORMCHASE_SUPABASE_ANON_KEY = "your-public-anon-key"
   py -3.12 main.py
   ```

   The anon key is intended for desktop clients and is constrained by RLS. Never
   put the `service_role` key in this app, its source, or a distributed build.
   Windows stores the signed-in session refresh token using the system
   credential store; users can sign out from the app.

Without the URL and anon key, local live data and outlook still work, while
Supabase tracking and report sign-in are unavailable. Automated database-client
tests use mocked responses. Verify the live project after deploying the SQL and
configuring the external publisher; those services are not available to the
local test suite.

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
