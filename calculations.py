import math
from datetime import datetime
from zoneinfo import ZoneInfo

from stormanalyzer import analyze_snowstorm, analyze_storm


print("CALCULATIONS FILE LOADED")


GERMANY_TZ = ZoneInfo(
    "Europe/Berlin"
)



def wind_to_vector(
    speed,
    direction
):


    rad = math.radians(
        direction
    )

    u = (
        -speed *
        math.sin(rad)
    )

    v = (
        -speed *
        math.cos(rad)
    )

    return u, v


def vector_difference(
    wind1,
    dir1,
    wind2,
    dir2
):


    u1, v1 = wind_to_vector(
        wind1,
        dir1
    )

    u2, v2 = wind_to_vector(
        wind2,
        dir2
    )

    du = u2 - u1
    dv = v2 - v1

    return math.sqrt(
        du ** 2 +
        dv ** 2
    )

def calculate_srh(
    sounding
):

    levels = [
        ("wind10", "dir10"),
        ("wind925", "dir925"),
        ("wind850", "dir850"),
        ("wind700", "dir700"),
        ("wind500", "dir500")
    ]

    vectors = []

    for wind, direction in levels:

        u, v = wind_to_vector(
            sounding[wind],
            sounding[direction]
        )

        vectors.append(
            (u, v)
        )

    srh = 0.0

    for i in range(
        len(vectors) - 1
    ):

        u1, v1 = vectors[i]
        u2, v2 = vectors[i + 1]

        srh += (
            u1 * v2
            -
            v1 * u2
        )

    srh = abs(
        srh
    ) * 0.15

    return min(
        round(srh, 1),
        400.0
    )


def calculate_lcl(
    temp,
    dewpoint
):

    lcl = (
        125.0 *
        (
            temp -
            dewpoint
        )
    )

    return max(
        round(lcl, 0),
        0
    )


# ==========================================================
# CIN NORMALIZATION
# ==========================================================

def normalize_cin(
    value
):

    if value is None:

        return 0.0

    try:

        value = float(
            value
        )

    except (
        TypeError,
        ValueError
    ):

        return 0.0

    return -abs(
        value
    )


# ==========================================================
# TIME HELPERS
# ==========================================================

def parse_time(
    time_string
):

    if not time_string:

        return None

    try:

        value = time_string.replace(
            "Z",
            "+00:00"
        )

        dt = datetime.fromisoformat(
            value
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=GERMANY_TZ
            )

        else:

            dt = dt.astimezone(
                GERMANY_TZ
            )

        return dt

    except Exception:

        return None


def classify_time(
    time_string
):

    dt = parse_time(
        time_string
    )

    if dt is None:

        return "forecast"

    now = datetime.now(
        GERMANY_TZ
    )

    difference_minutes = (
        dt -
        now
    ).total_seconds() / 60.0

    if (
        -30 <=
        difference_minutes <
        30
    ):

        return "current"

    if difference_minutes < -30:

        return "past"

    return "forecast"


def get_current_time():

    return datetime.now(
        GERMANY_TZ
    )



def calculate_environment(
    hourly,
    i
):


    cape = hourly[
        "cape"
    ][i]

    temperature = hourly[
        "temperature_2m"
    ][i]

    dewpoint = hourly[
        "dew_point_2m"
    ][i]

    humidity = hourly[
        "relative_humidity_2m"
    ][i]

    cin = normalize_cin(
        hourly["cin"][i]
    )

    temp700 = hourly[
        "temperature_700hPa"
    ][i]

    temp500 = hourly[
        "temperature_500hPa"
    ][i]

    lapse = (
        temp700 -
        temp500
    ) / 3.0


    lcl = calculate_lcl(
        temperature,
        dewpoint
    )

    high_shear = vector_difference(

        hourly[
            "wind_speed_850hPa"
        ][i],

        hourly[
            "wind_direction_850hPa"
        ][i],

        hourly[
            "wind_speed_500hPa"
        ][i],

        hourly[
            "wind_direction_500hPa"
        ][i]

    ) / 1.852


    low_shear = vector_difference(

        hourly[
            "wind_speed_10m"
        ][i],

        hourly[
            "wind_direction_10m"
        ][i],

        hourly[
            "wind_speed_850hPa"
        ][i],

        hourly[
            "wind_direction_850hPa"
        ][i]

    ) / 1.852

    sounding = {

        "wind10":
            hourly[
                "wind_speed_10m"
            ][i],

        "dir10":
            hourly[
                "wind_direction_10m"
            ][i],

        "wind925":
            hourly[
                "wind_speed_925hPa"
            ][i],

        "dir925":
            hourly[
                "wind_direction_925hPa"
            ][i],

        "wind850":
            hourly[
                "wind_speed_850hPa"
            ][i],

        "dir850":
            hourly[
                "wind_direction_850hPa"
            ][i],

        "wind700":
            hourly[
                "wind_speed_700hPa"
            ][i],

        "dir700":
            hourly[
                "wind_direction_700hPa"
            ][i],

        "wind500":
            hourly[
                "wind_speed_500hPa"
            ][i],

        "dir500":
            hourly[
                "wind_direction_500hPa"
            ][i]
    }


    srh = calculate_srh(
        sounding
    )

    return {

        "cape":
            round(
                float(cape),
                1
            ),

        "cin":
            round(
                cin,
                1
            ),

        "lcl":
            lcl,

        "temperature":
            round(
                float(temperature),
                1
            ),

        "dewpoint":
            round(
                float(dewpoint),
                1
            ),

        "humidity":
            round(
                float(humidity),
                1
            ),

        "precipitation":
            round(
                float(hourly["precipitation"][i] or 0),
                1
            ),

        "snowfall":
            round(
                float(hourly["snowfall"][i] or 0),
                2
            ),

        "wind_speed":
            round(
                float(hourly["wind_speed_10m"][i] or 0),
                1
            ),

        "srh":
            srh,

        "high_shear":
            round(
                high_shear,
                1
            ),

        "low_shear":
            round(
                low_shear,
                1
            ),

        "lapse":
            round(
                lapse,
                2
            ),

        "sounding":
            sounding
    }



def calculate_timeline(
    hourly
):
    """
    Builds the complete past/current/forecast timeline.
    """

    timeline = []

    times = hourly[
        "time"
    ]

    for i in range(
        len(times)
    ):

        result = calculate_environment(
            hourly,
            i
        )

        result["time"] = (
            times[i]
        )

        result["period"] = (
            classify_time(
                times[i]
            )
        )

        timeline.append(
            result
        )

    return timeline


def split_timeline(
    timeline
):
    """
    Splits timeline into past/current/forecast.
    """

    past = []
    current = []
    forecast = []

    for item in timeline:

        period = item.get(
            "period",
            "forecast"
        )

        if period == "past":

            past.append(
                item
            )

        elif period == "current":

            current.append(
                item
            )

        else:

            forecast.append(
                item
            )

    return {
        "past": past,
        "current": current,
        "forecast": forecast
    }


def calculate_forecast_window(
    timeline,
    hours=48
):
    """
    Returns the next `hours` forecast points.

    This remains a 48-hour forecast window and is NOT
    restricted to the current day.
    """

    forecast = [
        item
        for item in timeline
        if item.get(
            "period"
        ) == "forecast"
    ]

    return forecast[
        :hours
    ]



def score_environment(
    result,
    dt=None
):
    """
    Scores one environmental point.

    Higher score = more supportive environment.

    No artificial time-of-day adjustment is applied.
    """

    cape = float(
        result["cape"]
    )

    shear = float(
        result["high_shear"]
    )

    srh = float(
        result["srh"]
    )

    dewpoint = float(
        result["dewpoint"]
    )

    temp = float(
        result["temperature"]
    )

    cin = normalize_cin(
        result["cin"]
    )

    score = 0.0

    score += min(
        cape / 100.0,
        35.0
    )


    score += min(
        shear / 2.0,
        20.0
    )

    if dewpoint >= 20:

        score += 15

    elif dewpoint >= 18:

        score += 12

    elif dewpoint >= 15:

        score += 7

    elif dewpoint < 10:

        score -= 8

    score += min(
        srh / 25.0,
        8.0
    )


    if temp >= 25:

        score += 5

    if temp >= 30:

        score += 8


    if cin < -25 and cape < 400 :

        cin_penalty = (
            (abs(cin) - 25.0)
            / 25.0
        )

        cin_penalty = min(
            cin_penalty,
            20.0
        )

        score -= cin_penalty

    if cape < 100:

        score -= 50

    return round(
        score,
        1
    )



def calculate_best_window(
    hourly,
    winter_mode=False
):

    times = hourly[
        "time"
    ]

    now = datetime.now(
        GERMANY_TZ
    )

    today = now.date()

    best = None
    best_score = -999.0

    candidates = []

    for i in range(
        len(times)
    ):

        dt = parse_time(
            times[i]
        )

        if dt is None:

            continue



        if dt.date() != today:

            continue


        if dt < now:

            continue

        result = calculate_environment(
            hourly,
            i
        )

        if winter_mode:
            analysis = analyze_snowstorm(
                result["snowfall"],
                result["precipitation"],
                result["temperature"],
                result["wind_speed"]
            )
        else:
            analysis = analyze_storm(
                result["cape"],
                result["high_shear"],
                result["low_shear"],
                result["dewpoint"],
                result["srh"],
                result["cin"],
                result["lcl"],
                result["humidity"],
                result["temperature"],
                result["lapse"],
                result.get(
                    "coverage",
                    50
                )
            )

        score = analysis["risk"]

        candidates.append(
            (
                dt,
                score,
                result,
                times[i]
            )
        )

        if score > best_score:

            best_score = score

            best = result.copy()

            best["time"] = (
                times[i]
            )

            best["selection_score"] = (
                score
            )

    print(
        "================================"
    )

    print(
        "BEST HOUR — CURRENT DAY"
    )

    print(
        f"DATE: {today}"
    )

    print(
        f"CANDIDATES: {len(candidates)}"
    )

    if best is not None:

        print(
            f"BEST: {best['time']}"
        )

        print(
            f"BEST WINDOW RISK: {best['selection_score']}/50"
        )

        print(
            f"CAPE: {best['cape']} J/kg"
        )

        print(
            f"CIN: {best['cin']} J/kg"
        )

        print(
            f"SHEAR: {best['high_shear']} kt"
        )

        print(
            f"SRH: {best['srh']}"
        )

    else:

        print(
            "NO REMAINING HOURS TODAY"
        )

    return best


def calculate_best_forecast_window(
    timeline,
    hours=48
):

    forecast = calculate_forecast_window(
        timeline,
        hours
    )

    if not forecast:

        return None

    best = None
    best_score = -999.0

    for item in forecast:

        dt = parse_time(
            item.get(
                "time"
            )
        )

        score = score_environment(
            item,
            dt
        )

        if score > best_score:

            best_score = score

            best = item.copy()

            best[
                "selection_score"
            ] = score

    return best