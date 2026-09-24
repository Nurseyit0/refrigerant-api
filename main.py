from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from CoolProp.CoolProp import PropsSI

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

FLUIDS = {
    "R290": "Propane",
    "R744": "CarbonDioxide",
    "R717": "Ammonia",
    "R32": "R32",
    "R134a": "R134a",
    "R1234yf": "R1234yf",
    "R22": "R22",
}

MAX_TCOND_C = {
    "R744": 30.0,
}


class CycleRequest(BaseModel):
    refrigerant: str
    Tev: float
    Tcond: float
    eta: float


class CompareRequest(BaseModel):
    Tev: float
    Tcond: float
    eta: float


class SensitivityRequest(BaseModel):
    refrigerant: str
    Tev: float
    Tcond: float
    eta: float
    mode: str
    lo: float
    hi: float
    steps: int = 40


class DomeRequest(BaseModel):
    refrigerant: str


def _validate_common(refrigerant: str, eta: float):
    if refrigerant not in FLUIDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown refrigerant: {refrigerant}"
        )

    if not 0 < eta <= 1:
        raise HTTPException(
            status_code=400,
            detail="Compressor efficiency must be between 0 and 1"
        )


def _check_subcritical(refrigerant: str, Tcond_C: float):
    cap = MAX_TCOND_C.get(refrigerant)

    if cap is not None and Tcond_C >= cap:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Обычный недокритический цикл невозможен при этой "
                f"температуре конденсации для {refrigerant} "
                f"(критическая точка ≈31 °C). "
                f"Ограничение: Tcond ≤ {cap:.0f} °C."
            ),
        )


def _solve_cycle(
    refrigerant: str,
    Tev_C: float,
    Tcond_C: float,
    eta: float
):
    _validate_common(refrigerant, eta)
    _check_subcritical(refrigerant, Tcond_C)

    if Tcond_C <= Tev_C:
        raise HTTPException(
            status_code=422,
            detail=(
                "Температура конденсации должна быть выше "
                "температуры кипения."
            ),
        )

    fluid = FLUIDS[refrigerant]

    Tev = Tev_C + 273.15
    Tcond = Tcond_C + 273.15

    try:
        # State 1: saturated vapor
        P1 = PropsSI("P", "T", Tev, "Q", 1, fluid)
        h1 = PropsSI("H", "T", Tev, "Q", 1, fluid)
        s1 = PropsSI("S", "T", Tev, "Q", 1, fluid)

        # State 3: saturated liquid
        P2 = PropsSI("P", "T", Tcond, "Q", 0, fluid)
        h3 = PropsSI("H", "T", Tcond, "Q", 0, fluid)

        # Throttling valve: h4 = h3
        h4 = h3

        # Ideal isentropic compression
        h2s = PropsSI("H", "P", P2, "S", s1, fluid)
        T2s = PropsSI("T", "P", P2, "S", s1, fluid)

        # Real compression
        h2 = h1 + (h2s - h1) / eta

        T2 = PropsSI("T", "P", P2, "H", h2, fluid)
        s2 = PropsSI("S", "P", P2, "H", h2, fluid)

    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=(
                f"CoolProp не может рассчитать цикл для этих условий "
                f"({refrigerant}, Tev={Tev_C}°C, "
                f"Tcond={Tcond_C}°C): {e}"
            ),
        )

    # Energy balances
    q_evap = h1 - h4
    w_comp = h2 - h1
    q_cond = h2 - h3

    COP = q_evap / w_comp

    # Carnot COP
    COP_carnot = Tev / (Tcond - Tev)

    relative_efficiency = COP / COP_carnot

    return {
        "refrigerant": refrigerant,
        "COP": COP,
        "COP_Carnot": COP_carnot,
        "relative_efficiency": relative_efficiency,

        "states": {
            "1": {
                "T_C": Tev_C,
                "P_kPa": P1 / 1000,
                "h_kJkg": h1 / 1000,
                "s_kJkgK": s1 / 1000,
            },

            "2s": {
                "T_C": T2s - 273.15,
                "P_kPa": P2 / 1000,
                "h_kJkg": h2s / 1000,
            },

            "2": {
                "T_C": T2 - 273.15,
                "P_kPa": P2 / 1000,
                "h_kJkg": h2 / 1000,
                "s_kJkgK": s2 / 1000,
            },

            "3": {
                "T_C": Tcond_C,
                "P_kPa": P2 / 1000,
                "h_kJkg": h3 / 1000,
            },

            "4": {
                "P_kPa": P1 / 1000,
                "h_kJkg": h4 / 1000,
            },
        },

        "q_evap_kJkg": q_evap / 1000,
        "work_kJkg": w_comp / 1000,
        "q_cond_kJkg": q_cond / 1000,
    }


# =========================
# MAIN API
# =========================

@app.get("/")
def home():
    return {
        "status": "CoolProp API is running"
    }


# =========================
# CYCLE CALCULATOR
# =========================

@app.post("/cycle")
def calculate_cycle(data: CycleRequest):
    return _solve_cycle(
        data.refrigerant,
        data.Tev,
        data.Tcond,
        data.eta
    )


# =========================
# COMPARE REFRIGERANTS
# =========================

@app.post("/compare")
def compare(data: CompareRequest):

    results = []

    for key in FLUIDS:

        try:
            result = _solve_cycle(
                key,
                data.Tev,
                data.Tcond,
                data.eta
            )

            results.append({
                "refrigerant": key,
                "COP": result["COP"]
            })

        except HTTPException as e:

            results.append({
                "refrigerant": key,
                "COP": None,
                "error": e.detail
            })

    return {
        "Tev": data.Tev,
        "Tcond": data.Tcond,
        "eta": data.eta,
        "results": results
    }


# =========================
# SENSITIVITY ANALYSIS
# =========================

@app.post("/sensitivity")
def sensitivity(data: SensitivityRequest):

    _validate_common(
        data.refrigerant,
        data.eta
    )

    if data.mode not in ("tev", "tcond"):
        raise HTTPException(
            status_code=400,
            detail="mode must be 'tev' or 'tcond'"
        )

    if not 2 <= data.steps <= 200:
        raise HTTPException(
            status_code=400,
            detail="steps must be between 2 and 200"
        )

    if data.hi <= data.lo:
        raise HTTPException(
            status_code=400,
            detail="hi must be greater than lo"
        )

    points = []

    for i in range(data.steps + 1):

        t = (
            data.lo
            + (data.hi - data.lo) * i / data.steps
        )

        try:

            if data.mode == "tev":

                result = _solve_cycle(
                    data.refrigerant,
                    t,
                    data.Tcond,
                    data.eta
                )

            else:

                result = _solve_cycle(
                    data.refrigerant,
                    data.Tev,
                    t,
                    data.eta
                )

            points.append({
                "T_C": t,
                "COP": result["COP"]
            })

        except HTTPException:
            continue

    return {
        "refrigerant": data.refrigerant,
        "mode": data.mode,
        "points": points
    }


# =========================
# SATURATION DOME FOR P-h
# =========================

@app.post("/dome")
def dome(data: DomeRequest):

    if data.refrigerant not in FLUIDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown refrigerant: {data.refrigerant}"
        )

    fluid = FLUIDS[data.refrigerant]

    try:
        Tcrit = PropsSI(
            "Tcrit",
            fluid
        )

    except ValueError as e:

        raise HTTPException(
            status_code=422,
            detail=(
                f"Не удалось получить "
                f"критическую температуру: {e}"
            )
        )

    try:
        Tmin = PropsSI(
            "Ttriple",
            fluid
        )

    except ValueError:
        Tmin = 183.15

    Thigh = Tcrit - 1.0

    Tlow = max(
        Tmin,
        Thigh - 340
    )

    N = 50

    points = []

    for i in range(N + 1):

        T = (
            Tlow
            + (Thigh - Tlow) * i / N
        )

        try:

            P = PropsSI(
                "P",
                "T",
                T,
                "Q",
                0,
                fluid
            )

            h_liq = PropsSI(
                "H",
                "T",
                T,
                "Q",
                0,
                fluid
            )

            h_vap = PropsSI(
                "H",
                "T",
                T,
                "Q",
                1,
                fluid
            )

            points.append({
                "T_C": T - 273.15,
                "P_kPa": P / 1000,
                "h_liquid_kJkg": h_liq / 1000,
                "h_vapor_kJkg": h_vap / 1000
            })

        except ValueError:
            continue

    return {
        "refrigerant": data.refrigerant,
        "Tcrit_C": Tcrit - 273.15,
        "points": points
    }
