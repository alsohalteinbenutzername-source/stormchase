import math


RISK_SCORE_SCALE = 78.95
RISK_SCORE_SHAPE = 2.18
TORNADO_RISK_WEIGHT_EXPONENT = 0.78


def _finite_score_value(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _unit_interval(value):
    return min(max(value, 0.0), 1.0)


def _tornado_interaction_gate(
    lcl,
    srh_0_1km,
    srh_0_3km,
    srh_efficiency,
    shear_0_500m,
    critical_angle,
    low_level_lapse,
    cape_0_3km,
    lfc,
    cin,
):
    """Combine low-level buoyancy, shear, helicity, and boundary-layer factors."""
    cape_factor = 0.7 + 0.3 * _unit_interval(cape_0_3km / 400.0)
    lcl_factor = (
        1.0
        if lcl <= 1100.0
        else _unit_interval((1800.0 - lcl) / 700.0)
    )

    helicity = max(srh_0_1km, 0.0)
    helicity_factor = 0.25 + 0.75 * math.sqrt(
        _unit_interval(helicity / 300.0)
    )

    efficiency_factor = 0.25 + 0.75 * _unit_interval(srh_efficiency)
    concentration = _unit_interval(helicity / max(abs(srh_0_3km), 1.0))
    concentration_factor = 0.25 + 0.75 * concentration
    shear_factor = 0.2 + 0.8 * _unit_interval(shear_0_500m / 15.0)

    if critical_angle < 45.0 or critical_angle > 135.0:
        angle_factor = 0.2
    elif critical_angle < 70.0:
        angle_factor = 0.2 + 0.8 * (critical_angle - 45.0) / 25.0
    elif critical_angle > 110.0:
        angle_factor = 0.2 + 0.8 * (135.0 - critical_angle) / 25.0
    else:
        angle_factor = 1.0

    if low_level_lapse < 4.5:
        lapse_factor = max(0.75, 1.0 - 0.08 * (4.5 - low_level_lapse))
    elif low_level_lapse > 8.5:
        lapse_factor = max(0.75, 1.0 - 0.08 * (low_level_lapse - 8.5))
    else:
        lapse_factor = 1.0

    lfc_factor = (
        1.0
        if lfc <= 1000.0
        else max(0.4, 1.0 - (lfc - 1000.0) / 2500.0)
    )
    cin_factor = (
        1.0
        if cin >= -50.0
        else max(0.45, 1.0 - (abs(cin) - 50.0) / 300.0)
    )

    factors = (
        cape_factor,
        lcl_factor,
        helicity_factor,
        efficiency_factor,
        concentration_factor,
        shear_factor,
        angle_factor,
        lapse_factor,
        lfc_factor,
        cin_factor,
    )
    return math.prod(factors)


def get_risk_category(risk):

    if risk >= 85:
        return "Extreme Severe Weather Potential"
    elif risk >= 70:
        return "High Severe Weather Potential"
    elif risk >= 60:
        return "Moderate Severe Weather Potential"
    elif risk >= 45:
        return "Slight Severe Weather Potential"
    elif risk >= 20:
        return "Marginal Storm Potential"
    else:
        return "Fair Weather / Very Low Storm Potential"



def get_risk_threat(risk):
    if risk >= 70:
        return "High"
    elif risk >= 60:
        return "Moderate"
    elif risk >= 45:
        return "Slight"
    elif risk >= 20:
        return "Marginal"





def get_threats(tornado_score, hail_score, wind_score):

    threats = []
    if hail_score >= 60:
        threats.append("a significant large hail risk")
    elif hail_score >= 50:
        threats.append("a moderate large hail risk")
    elif hail_score >= 30:
        threats.append("an isolated large hail risk")

    if wind_score >= 60:
        threats.append("a significant risk of damaging wind gusts")
    elif wind_score >= 50:
        threats.append("a moderate risk of damaging wind gusts")
    elif wind_score >= 30:
        threats.append("an isolated risk of damaging wind gusts")

    if tornado_score >= 60:
        threats.append("a significant risk of tornadoes")
    elif tornado_score >= 50:
        threats.append("a considerable risk of tornadoes")
    elif tornado_score >= 30:
        threats.append("an isolated risk of tornadoes")

    return threats


def generate_storm_forecast(outlook_areas):

    forecast_lines = []

    for outlook in outlook_areas:

        risk = outlook["risk"]
        areas = outlook["areas"]
        threats = outlook["threats"]

        if not threats:
            continue

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
            f"this area of interest mainly for {threat_text}."
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
    *,
    srh_0_1km,
    srh_0_3km,
    srh_efficiency,
    shear_0_500m,
    critical_angle,
    low_level_lapse,
    cape_0_3km,
    lfc,
):

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

    profile_values = {
        "srh_0_1km": srh_0_1km,
        "srh_0_3km": srh_0_3km,
        "srh_efficiency": srh_efficiency,
        "shear_0_500m": shear_0_500m,
        "critical_angle": critical_angle,
        "low_level_lapse": low_level_lapse,
        "cape_0_3km": cape_0_3km,
        "lfc": lfc,
    }
    for name, value in profile_values.items():
        normalized_value = _finite_score_value(value)
        if normalized_value is None:
            raise ValueError(f"{name} must be a finite number.")
        profile_values[name] = normalized_value
    srh_0_1km = profile_values["srh_0_1km"]
    srh_0_3km = profile_values["srh_0_3km"]
    srh_efficiency = profile_values["srh_efficiency"]
    shear_0_500m = profile_values["shear_0_500m"]
    critical_angle = profile_values["critical_angle"]
    low_level_lapse = profile_values["low_level_lapse"]
    cape_0_3km = profile_values["cape_0_3km"]
    lfc = profile_values["lfc"]
    if not 0.0 <= critical_angle <= 180.0:
        raise ValueError("critical_angle must be between 0 and 180 degrees.")
    if not 0.0 <= srh_efficiency <= 1.0:
        raise ValueError("srh_efficiency must be between 0 and 1.")

    tornado_helicity = srh_0_1km

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
    ], tornado_helicity), 20)

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


    if tornado_helicity >= 300 and lcl <= 850:
        tornado_score += 8

    if tornado_helicity >= 250 and lcl <= 850:
        tornado_score += 5

    if tornado_helicity <= 150 and low_shear <= 15:
        tornado_score *= 0.3

    if cin <= -150:
        risk *= 0.7

    # reduce scores when low cape limits convection.
    if cape <= 100:
        risk *= 0.15
        supercell_score *= 0.40
        tornado_score *= 0.65
        damaging_wind_score *= 0.60
        hail_score *= 0.40
    elif cape <= 200:
        risk *= 0.35
        supercell_score *= 0.60
        tornado_score *= 0.75
        damaging_wind_score *= 0.70
        hail_score *= 0.60
    elif cape <= 300:
        risk *= 0.70
        supercell_score *= 0.75
        tornado_score *= 0.85
        damaging_wind_score *= 0.80
        hail_score *= 0.75
    elif cape <= 500:
        risk *= 0.90
        supercell_score *= 0.90
        tornado_score *= 0.95
        damaging_wind_score *= 0.90
        hail_score *= 0.90


    if cape >= 300 and cin >= -30:
        risk += 5
    

    raw_risk = max(risk * 2, 0.0)
    risk = 100.0 * -math.expm1(
        -((raw_risk / RISK_SCORE_SCALE) ** RISK_SCORE_SHAPE)
    )

    tornado_score = min(tornado_score, 40) * 2
    risk_weight = min(
        max(round(risk, 2) / 100, 0.0),
        1.0,
    ) ** TORNADO_RISK_WEIGHT_EXPONENT
    risk_weight = max(risk_weight, 0.85)
    interaction_gate = _tornado_interaction_gate(
        lcl,
        srh_0_1km,
        srh_0_3km,
        srh_efficiency,
        shear_0_500m,
        critical_angle,
        low_level_lapse,
        cape_0_3km,
        lfc,
        cin,
    )
    tornado_score *= risk_weight * (0.55 + 0.45 * interaction_gate)
    hail_score = min(hail_score, 40) * 2
    damaging_wind_score = min(damaging_wind_score, 40) * 2

    supercell_score = min(supercell_score, 20)

    if high_shear < 20:
        supercell_score = 0


    tornado = tornado_score >= 50
    hail = hail_score >= 50
    wind = damaging_wind_score >= 50

    supercell = supercell_score >= 15



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


    # label the highest-impact combinations.
    if tornado_score >= 70 and supercell and risk >= 70:
        storm_type = (
            "Extreme Tornadic Supercell Potential"
        )

    elif (
        risk < 20
    ):
        storm_type = (
            "Fairweather Likely, with minimal severe "
            "weather potential"
        )

    elif (
        risk < 30
    ):
        storm_type = (
            "Non-Severe Thunderstorms likely, with "
            "possible severe weather potential"
        )

    elif highest < 40 and risk >= 30:
        storm_type = (
            "Severe Thunderstorms likely"
        )

    elif supercell:

        if tornado and hail and wind and risk >= 45:
            storm_type = (
                "All-Hazards Supercell likely"
            )

        elif tornado and hail and risk >= 45:
            storm_type = (
                "Tornadic Hail Supercell potential"
            )

        elif tornado and wind and risk >= 45:
            storm_type = (
                "Tornadic Wind Supercell potential"
            )

        elif hail and wind and risk >= 45:
            storm_type = (
                "Severe Hail/Wind Supercell potential"
            )

        elif tornado and risk >= 45:
            storm_type = (
                "Tornadic Supercell potential"
            )

        elif hail and risk >= 45:
            storm_type = (
                "Hail-Dominant Supercell potential"
            )

        elif wind and risk >= 45:
            storm_type = (
                "Wind-Dominant Supercell potential"
            )

        elif risk >= 45:
            storm_type = (
                "Supercell likely"
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

        "threats": threats,

        "issued": (
            "No risk for severe storms today."
            if risk < 30
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