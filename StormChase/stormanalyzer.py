def get_risk_category(risk):

    if risk >= 45:
        return "Extreme Severe Weather Potential"
    elif risk >= 40:
        return "High Severe Weather Potential"
    elif risk >= 35:
        return "Moderate Severe Weather Potential"
    elif risk >= 25:
        return "Slight Severe Weather Potential"
    elif risk >= 10:
        return "Marginal Storm Potential"
    else:
        return "Fair Weather / Very Low Storm Potential"



def get_risk_threat(risk):
    if risk >= 40:
        return "High"
    elif risk >= 35:
        return "Moderate"
    elif risk >= 25:
        return "Slight"
    elif risk >= 15:
        return "Marginal"





def get_threats(tornado_score, hail_score, wind_score):

    threats = []
    if hail_score >= 30:
        threats.append("a significant large hail risk")
    elif hail_score >= 25:
        threats.append("a moderate large hail risk")
    elif hail_score >= 15:
        threats.append("an isolated large hail risk")

    if wind_score >= 30:
        threats.append("a significant risk of damaging wind gusts")
    elif wind_score >= 25:
        threats.append("a moderate risk of damaging wind gusts")
    elif wind_score >= 15:
        threats.append("an isolated risk of damaging wind gusts")

    if tornado_score >= 30:
        threats.append("a significant risk of tornadoes")
    elif tornado_score >= 25:
        threats.append("a considerable risk of tornadoes")
    elif tornado_score >= 15:
        threats.append("an isolated risk of tornadoes")

    return threats


def analyze_snowstorm(snowfall, precipitation, temperature, wind_speed):

    snowfall = max(float(snowfall or 0), 0)
    precipitation = max(float(precipitation or 0), 0)
    wind_speed = max(float(wind_speed or 0), 0)
    temperature = float(temperature)

    if snowfall < 0.1:
        snowfall = 0

    snow_score = min(snowfall * 15, 30)
    temperature_score = (
        (
            10 if temperature <= -2
            else 6 if temperature <= 0
            else 3 if temperature <= 2
            else 0
        )
        if snowfall
        else 0
    )
    wind_score = (
        min(max((wind_speed - 15) / 4, 0), 10)
        if snowfall
        else 0
    )
    precipitation_score = (
        min(precipitation, 5)
        if snowfall and temperature <= 2
        else 0
    )
    risk = round(
        min(
            snow_score
            + temperature_score
            + wind_score
            + precipitation_score,
            50
        ),
        2
    )

    if not snowfall:
        category = "Low Snowstorm Risk"
        storm_type = "No snowstorm signal"
    elif risk >= 30:
        category = "High Snowstorm Risk"
        storm_type = "Heavy Snowstorm Potential"
    elif risk >= 20:
        category = "Moderate Snowstorm Risk"
        storm_type = "Snow and Strong Wind Potential" if wind_speed >= 35 else "Steady Snow Potential"
    elif risk >= 10:
        category = "Snowstorm Potential"
        storm_type = "Periods of Snow Possible"
    else:
        category = "Light Snow Potential"
        storm_type = "Light Snow Possible"

    threats = []
    if snowfall:
        threats.append("falling snow")
    if snowfall and wind_speed >= 30:
        threats.append("blowing snow")

    return {
        "risk": risk,
        "category": category,
        "risk_threat": category,
        "tornado": 0,
        "hail": 0,
        "wind": round(wind_score, 2),
        "supercell": 0,
        "mcs": 0,
        "threats": threats,
        "issued": (
            f"{category}: {snowfall:.2f} cm snowfall, "
            f"{precipitation:.1f} mm precipitation, "
            f"{wind_speed:.1f} km/h wind."
        ),
        "storm_type": storm_type,
        "snowfall": snowfall,
        "precipitation": precipitation,
        "wind_speed": wind_speed,
        "criticism": [
            f"Forecast hourly snowfall: {snowfall:.2f} cm.",
            f"Near-surface temperature: {temperature:.1f} °C.",
            f"Wind may increase blowing-snow impacts at higher speeds."
        ]
    }


def generate_storm_forecast(outlook_areas):

    forecast_lines = []

    for outlook in outlook_areas:

        risk = outlook["risk"]
        areas = outlook["areas"]
        threats = outlook["threats"]

        if not threats:
            continue

        # One threat
        if len(threats) == 1:
            threat_text = threats[0]
        elif len(threats) == 2:
            threat_text = (
                f"{threats[0]} and {threats[1]}"
            )
        else:
            threat_text = (
                ", ".join(threats[:-1])
                + ", and "
                + threats[-1]
            )

        forecast_lines.append(
            f"A {risk} risk has been issued for "
            f"Schweinfurt mainly for {threat_text}."
        )

    if not forecast_lines:
        return "No risk for severe storms today."

    return "\n".join(forecast_lines)


def generate_criticism(
    cape,
    high_shear,
    low_shear,
    dewpoint,
    srh,
    cin,
    lcl,
    rh,
    temp,
    lapse
):
    reasons = []

    if cape < 500:
        reasons.append(
            "⚠ CAPE is extremely low, limiting thunderstorm development."
        )
    elif cape < 1000:
        reasons.append(
            "⚠ CAPE is below 1000 J/kg, limiting severe storm intensity."
        )
    elif cape >= 2500:
        reasons.append(
            "✓ Strong instability supports powerful updrafts."
        )


    if high_shear < 20:
        reasons.append(
            "⚠ Deep layer shear is weak, limiting storm organization."
        )
    elif high_shear >= 35:
        reasons.append(
            "✓ Strong shear supports organized severe storms."
        )


    if low_shear < 10:
        reasons.append(
            "⚠ Low level shear is weak, reducing tornado potential."
        )
    elif low_shear >= 20:
        reasons.append(
            "✓ Strong low level shear improves rotation potential."
        )


    if dewpoint < 12:
        reasons.append(
            "⚠ Low dewpoint reduces available moisture."
        )
    elif dewpoint >= 18:
        reasons.append(
            "✓ Rich moisture supports stronger convection."
        )

    if srh < 100:
        reasons.append(
            "⚠ SRH is below 100 m²/s², limiting tornado potential."
        )
    elif srh >= 200:
        reasons.append(
            "✓ Strong SRH supports rotating storms."
        )


    if lcl > 2000:
        reasons.append(
            "⚠ High LCL reduces tornado favorability."
        )
    elif lcl < 1000:
        reasons.append(
            "✓ Low LCL improves tornado environment."
        )


    if cin <= -100:
        reasons.append(
            "⚠ Strong CIN may prevent storm initiation."
        )


    if rh < 40:
        reasons.append(
            "⚠ Dry boundary layer may weaken storms."
        )


    if lapse < 6:
        reasons.append(
            "⚠ Weak lapse rates limit instability."
        )
    elif lapse >= 7:
        reasons.append(
            "✓ Steep lapse rates support strong hail growth."
        )

    return reasons


def get_storm_types(
    tornado,
    hail,
    wind,
    supercell,
    mcs,
    coverage
):
    pass


def analyze_storm(
    cape,
    high_shear,
    low_shear,
    dewpoint,
    srh,
    cin,
    lcl,
    rh,
    temp,
    lapse,
    coverage=50
):

    mcs_score = 0
    supercell_score = 0

    tornado_score = 0
    hail_score = 0
    damaging_wind_score = 0

    risk = 0


    def interpolate_cape_value(points, value):
        if value <= points[0][0]:
            return points[0][1]

        for (lower_cape, lower_value), (upper_cape, upper_value) in zip(
            points,
            points[1:]
        ):
            if value <= upper_cape:
                fraction = (
                    value - lower_cape
                ) / (
                    upper_cape - lower_cape
                )
                return lower_value + fraction * (
                    upper_value - lower_value
                )

        return points[-1][1]

    risk += min(interpolate_cape_value([
        (0, 0),
        (300, -5),
        (500, 3),
        (1000, 4),
        (1500, 6),
        (2000, 8),
        (3000, 10),
        (3700, 13)
    ], cape), 10)

    hail_score += min(interpolate_cape_value([
        (0, 0),
        (1000, 3),
        (1500, 6),
        (2000, 9),
        (3000, 10),
        (3700, 11)
    ], cape), 10)

    damaging_wind_score += min(interpolate_cape_value([
        (0, 0),
        (2000, 8),
        (3000, 10),
        (3700, 12)
    ], cape), 10)

    mcs_score += min(interpolate_cape_value([
        (0, 0),
        (3700, 7)
    ], cape), 6)

    supercell_score += min(interpolate_cape_value([
        (0, 0),
        (3700, 7)
    ], cape), 6)



    risk += min(interpolate_cape_value([
        (0, -7),
        (20, -3),
        (30, 7),
        (35, 14)
    ], high_shear), 8)

    tornado_score += min(interpolate_cape_value([
        (0, 0),
        (20, 0),
        (30, 5),
        (35, 6)
    ], high_shear), 4)

    damaging_wind_score += min(interpolate_cape_value([
        (0, 0),
        (20, 0),
        (30, 10),
        (35, 12)
    ], high_shear), 8)

    supercell_score += min(interpolate_cape_value([
        (0, 0),
        (20, 0),
        (30, 6),
        (35, 10)
    ], high_shear), 7)

    hail_score += min(interpolate_cape_value([
        (0, 0),
        (20, 0),
        (30, 7),
        (35, 10)
    ], high_shear), 8)



    risk += min(interpolate_cape_value([
        (0, -5),
        (15, -5),
        (20, 4),
        (29, 8)
    ], low_shear), 6)

    tornado_score += min(interpolate_cape_value([
        (0, -3),
        (15, -3),
        (20, 3),
        (29, 7)
    ], low_shear), 5)

    damaging_wind_score += min(interpolate_cape_value([
        (0, -2),
        (15, -2),
        (20, 4),
        (29, 6)
    ], low_shear), 4)



    risk += min(interpolate_cape_value([
        (0, 0),
        (15, 3),
        (18, 4),
        (20, 5)
    ], dewpoint), 4)

    hail_score += min(interpolate_cape_value([
        (0, 0),
        (18, 0),
        (20, 5)
    ], dewpoint), 4)


    risk += min(interpolate_cape_value([
        (0, 0),
        (150, 3),
        (250, 8),
        (300, 14)
    ], srh), 10)

    tornado_score += min(interpolate_cape_value([
        (0, 0),
        (150, 4),
        (250, 14),
        (300, 31)
    ], srh), 20)

    supercell_score += min(interpolate_cape_value([
        (0, 0),
        (150, 5),
        (250, 12),
        (300, 14)
    ], srh), 10)

    damaging_wind_score += min(interpolate_cape_value([
        (0, 0),
        (250, 4),
        (300, 6)
    ], srh), 4)



    risk += min(interpolate_cape_value([
        (850, 4),
        (1200, 3),
        (1500, 2),
        (2500, 0)
    ], lcl), 3.5)

    tornado_score += min(interpolate_cape_value([
        (850, 6),
        (1200, 5),
        (1500, 0),
        (2500, 0)
    ], lcl), 4)

    hail_score += min(interpolate_cape_value([
        (850, 3),
        (1200, 0),
        (2500, 0)
    ], lcl), 2)


    risk += min(interpolate_cape_value([
        (0, 0),
        (70, 2),
        (85, 7)
    ], rh), 5)

    mcs_score += min(interpolate_cape_value([
        (0, 0),
        (85, 4)
    ], rh), 3)


    risk += min(interpolate_cape_value([
        (0, 0),
        (30, 2)
    ], temp), 1.5)

    hail_score += min(interpolate_cape_value([
        (0, 0),
        (30, 3)
    ], temp), 2)



    risk += min(interpolate_cape_value([
        (0, -2),
        (6, 2),
        (6.5, 2),
        (7.5, 5)
    ], lapse), 4)

    hail_score += min(interpolate_cape_value([
        (0, -3),
        (6, 0),
        (6.5, 3),
        (7.5, 10)
    ], lapse), 7)

    if cin <= -100 and cape < 1000:
        risk -= 5
        tornado_score *= 0.4
        hail_score *= 0.4
        damaging_wind_score *= 0.4
        supercell_score *= 0.4
        mcs_score *= 0.4


    if coverage >= 80:
        mcs_score = mcs_score * 0.45
        supercell_score += 5
        mcs_score -= 5

    elif coverage >= 60:
        supercell_score += 3
        mcs_score -= 3

    elif coverage >= 50:
        supercell_score += 0
        mcs_score += 0

    elif coverage >= 40:
        mcs_score += 3
        supercell_score -= 3

    elif coverage >= 20:
        mcs_score += 5
        supercell_score -= 5

    else:
        mcs_score += 15
        supercell_score = supercell_score * 0.45


    if mcs_score >= 15 and damaging_wind_score >= 30:
        mcs_score += 5


    if srh >= 300 and lcl <= 850:
        tornado_score += 8

    if srh >= 250 and lcl <= 850:
        tornado_score += 5

    if srh <= 150 and low_shear <= 15:
        tornado_score * 0.3

    if cin <= -150:
        risk *= 0.7
        supercell_score = supercell_score * 0.45
        mcs_score = mcs_score * 0.45
        tornado_score = tornado_score * 0.45
        damaging_wind_score = damaging_wind_score * 0.45
        hail_score = hail_score * 0.45

# Low-CAPE convective feasibility adjustment
    if cape <= 100:
        risk *= 0.15
        supercell_score *= 0.40
        mcs_score *= 0.40
        tornado_score *= 0.65
        damaging_wind_score *= 0.60
        hail_score *= 0.40
    elif cape <= 200:
        risk *= 0.35
        supercell_score *= 0.60
        mcs_score *= 0.60
        tornado_score *= 0.75
        damaging_wind_score *= 0.70
        hail_score *= 0.60
    elif cape <= 300:
        risk *= 0.70
        supercell_score *= 0.75
        mcs_score *= 0.75
        tornado_score *= 0.85
        damaging_wind_score *= 0.80
        hail_score *= 0.75
    elif cape <= 500:
        risk *= 0.90
        supercell_score *= 0.90
        mcs_score *= 0.90
        tornado_score *= 0.95
        damaging_wind_score *= 0.90
        hail_score *= 0.90


    if cape >= 300 and cin >= -30:
        risk += 5
    

    risk = min(risk, 50)

    tornado_score = min(tornado_score, 40)
    hail_score = min(hail_score, 40)
    damaging_wind_score = min(damaging_wind_score, 40)

    supercell_score = min(supercell_score, 20)
    mcs_score = min(mcs_score, 20)

    if high_shear < 20:
        supercell_score = 0


    tornado = tornado_score >= 25
    hail = hail_score >= 25
    wind = damaging_wind_score >= 25

    supercell = supercell_score >= 15
    mcs = mcs_score >= 15



    threats = get_threats(
        tornado_score,
        hail_score,
        damaging_wind_score
    )

    highest = max(
        tornado_score,
        hail_score,
        damaging_wind_score
    )


    storm_type = None


    # Extreme cases
    if tornado_score >= 35 and supercell and risk >= 35:
        storm_type = (
            "Extreme Tornadic Supercell Potential"
        )

    elif mcs_score >= 18 and damaging_wind_score >= 30 and risk >= 35:
        storm_type = (
            "High-End MCS / Derecho Potential"
        )

    elif mcs_score >= 12 and damaging_wind_score >= 20 and risk >= 25:
        storm_type = (
            "Organized MCS likely, with damaging wind potential"
        )

    elif (
        risk < 15
    ):
        storm_type = (
            "Fairweather Likely, with minimal severe "
            "weather potential"
        )

    elif (
        risk < 18
    ):
        storm_type = (
            "Non-Severe Thunderstorms likely, with "
            "possible severe weather potential"
        )

    elif highest < 20 and risk >= 18:
        storm_type = (
            "Severe Thunderstorms likely"
        )

    elif supercell:

        if tornado and hail and wind and cape >= 1000:
            storm_type = (
                "All-Hazards Supercell likely"
            )

        elif tornado and hail and cape >= 1000:
            storm_type = (
                "Tornadic Hail Supercell potential"
            )

        elif tornado and wind and cape >= 1000:
            storm_type = (
                "Tornadic Wind Supercell potential"
            )

        elif hail and wind and cape >= 1000:
            storm_type = (
                "Severe Hail/Wind Supercell potential"
            )

        elif tornado and cape >= 1000:
            storm_type = (
                "Tornadic Supercell potential"
            )

        elif hail and cape >= 1000:
            storm_type = (
                "Hail-Dominant Supercell potential"
            )

        elif wind and cape >= 1000:
            storm_type = (
                "Wind-Dominant Supercell potential"
            )

        elif cape >= 1000:
            storm_type = (
                "Supercell likely"
            )

    elif mcs:

        if wind and hail and cape >= 500:
            storm_type = (
                "Severe MCS likely with Hail/Wind Potential"
            )

        elif wind and cape >= 500:
            storm_type = (
                "Wind-Dominant MCS likely"
            )

        elif hail and cape >= 500:
            storm_type = (
                "Hail-Producing MCS likely"
            )

        elif tornado and cape >= 500:
            storm_type = (
                "MCS likely with embedded tornado potential"
            )

        elif high_shear >= 30 and cape >= 500:
            storm_type = (
                "Organized MCS likely"
            )

    elif wind and hail:
        storm_type = (
            "Severe Hail/Wind Storm potential"
        )

    elif wind:
        storm_type = (
            "Damaging Wind Storm likely"
        )

    elif hail:
        storm_type = (
            "Large Hail Storm likely"
        )

    else:
        storm_type = (
            "General Thunderstorm Potential"
        )



    rounded_risk = round(
        risk,
        2
    )

    return {
        "risk": rounded_risk,

        "category": get_risk_category(rounded_risk),

        "risk_threat": get_risk_threat(rounded_risk),

        "tornado": round(tornado_score, 2),

        "hail": round(hail_score, 2),

        "wind": round(damaging_wind_score, 2),

        "supercell": round(supercell_score, 2),

        "mcs": round(mcs_score, 2),

        "threats": threats,

        "issued": (
            "No risk for severe storms today."
            if risk < 15
            else generate_storm_forecast([
                {
                    "risk": get_risk_threat(risk),
                    "areas": "Schweinfurt",
                    "threats": threats
                }
            ])
        ),

        "storm_type": storm_type,

        "criticism": generate_criticism(
            cape,
            high_shear,
            low_shear,
            dewpoint,
            srh,
            cin,
            lcl,
            rh,
            temp,
            lapse
        )
    }