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

class CycleRequest(BaseModel):
    refrigerant: str
    Tev: float
    Tcond: float
    eta: float


@app.get("/")
def home():
    return {"status": "CoolProp API is running"}


@app.post("/cycle")
def calculate_cycle(data: CycleRequest):

    if data.refrigerant not in FLUIDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown refrigerant: {data.refrigerant}"
        )

    if not 0 < data.eta <= 1:
        raise HTTPException(
            status_code=400,
            detail="Compressor efficiency must be between 0 and 1"
        )

    fluid = FLUIDS[data.refrigerant]

    Tev = data.Tev + 273.15
    Tcond = data.Tcond + 273.15

    # State 1: saturated vapor
    P1 = PropsSI("P", "T", Tev, "Q", 1, fluid)
    h1 = PropsSI("H", "T", Tev, "Q", 1, fluid)
    s1 = PropsSI("S", "T", Tev, "Q", 1, fluid)

    # State 3: saturated liquid
    P2 = PropsSI("P", "T", Tcond, "Q", 0, fluid)
    h3 = PropsSI("H", "T", Tcond, "Q", 0, fluid)

    # State 4: throttling
    h4 = h3

    # State 2s: ideal isentropic compression
    h2s = PropsSI("H", "P", P2, "S", s1, fluid)
    T2s = PropsSI("T", "P", P2, "S", s1, fluid)

    # State 2: real compressor
    h2 = h1 + (h2s - h1) / data.eta
    T2 = PropsSI("T", "P", P2, "H", h2, fluid)
    s2 = PropsSI("S", "P", P2, "H", h2, fluid)

    # Energy balance
    q_evap = h1 - h4
    w_comp = h2 - h1
    q_cond = h2 - h3

    COP = q_evap / w_comp

    # Carnot COP
    COP_carnot = Tev / (Tcond - Tev)

    relative_efficiency = COP / COP_carnot

    return {
        "refrigerant": data.refrigerant,

        "COP": COP,
        "COP_Carnot": COP_carnot,
        "relative_efficiency": relative_efficiency,

        "states": {
            "1": {
                "T_C": data.Tev,
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
                "T_C": data.Tcond,
                "P_kPa": P2 / 1000,
                "h_kJkg": h3 / 1000,
            },

            "4": {
                "P_kPa": P1 / 1000,
                "h_kJkg": h4 / 1000,
            }
        },

        "q_evap_kJkg": q_evap / 1000,
        "work_kJkg": w_comp / 1000,
        "q_cond_kJkg": q_cond / 1000,
    }
