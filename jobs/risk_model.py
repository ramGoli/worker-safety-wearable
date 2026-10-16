"""Next-shift incident risk: shift-level features -> probability that a worker has a fall or SOS on their next
shift, plus the two factors that push it up the most. Pure pandas/scikit-learn, so it's tested on a laptop and
imported unchanged by the nightly job."""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

# Same zones and typical noise as tools/crew.py and the console's site map.
ZONES = {"Assembly line": 88, "Workshop": 92, "Office": 52, "Warehouse": 72, "Loading dock": 80}
NUMERIC = ["hours", "avg_noise_db", "noise_dose_pct", "motion_std", "incidents_7d"]
FEATURES = NUMERIC + [f"zone_{z}" for z in ZONES]
PLANNED_HOURS = 8
INCIDENTS_7D_CAP = 5  # history rarely shows more; a burst of drop tests shouldn't push the model off the chart
SHIFT_S = 8 * 3600

# The hidden "truth" of the simulated history: louder zones, long shifts, jerky motion and recent incidents
# raise the odds. The model has to rediscover this from the data.
_TRUE_INTERCEPT = -4.2
_TRUE_ZONE = {"Assembly line": 0.3, "Workshop": 0.6, "Office": -0.8, "Warehouse": 0.0, "Loading dock": 0.5}


def noise_dose_pct(avg_noise_db, hours):
    """NIOSH dose (85 dB for 8 h = 100%, +3 dB halves the time), same rule as the App's exposure.py."""
    if avg_noise_db < 80:
        return 0.0
    return hours * 3600 * 2 ** ((avg_noise_db - 85) / 3) / SHIFT_S * 100


def simulate_history(workers=30, days=30, seed=7):
    """One row per worker per day, labelled had_incident, with source='simulated'."""
    rng = np.random.default_rng(seed)
    rows, recent = [], {w: [] for w in range(workers)}
    start = pd.Timestamp("2026-09-14")
    for day in range(days):
        for w in range(workers):
            zone = list(ZONES)[(w + day // 7) % len(ZONES)] if rng.random() < 0.8 else rng.choice(list(ZONES))
            hours = float(np.clip(rng.normal(8.5, 1.2), 4, 12))
            noise = float(rng.normal(ZONES[zone], 3))
            motion = float(abs(rng.normal(0.06, 0.03)))
            prior = sum(recent[w][-7:])
            logit = (_TRUE_INTERCEPT + 0.06 * (noise - 70) + 0.35 * (hours - 8) + 12 * (motion - 0.06)
                     + 0.7 * prior + _TRUE_ZONE[zone])
            hit = int(rng.random() < 1 / (1 + np.exp(-logit)))
            recent[w].append(hit)
            rows.append({"device_id": f"hist-{w + 1:02d}", "worker_name": f"Worker {w + 1}",
                         "shift_date": (start + pd.Timedelta(days=day)).date(), "zone": zone,
                         "hours": round(hours, 2), "avg_noise_db": round(noise, 1),
                         "noise_dose_pct": round(noise_dose_pct(noise, hours), 1), "motion_std": round(motion, 4),
                         "incidents_7d": prior, "had_incident": hit, "source": "simulated"})
    return pd.DataFrame(rows)


def matrix(df):
    x = df[NUMERIC].astype(float).copy()
    x["incidents_7d"] = x["incidents_7d"].clip(upper=INCIDENTS_7D_CAP)
    for z in ZONES:
        x[f"zone_{z}"] = (df["zone"] == z).astype(float)
    return x[FEATURES]


def train(df):
    """Logistic regression, evaluated on the last 20% of days so the AUC reflects unseen shifts."""
    df = df.sort_values("shift_date")
    cut = sorted(df["shift_date"].unique())[int(df["shift_date"].nunique() * 0.8)]
    fit, hold = df[df["shift_date"] < cut], df[df["shift_date"] >= cut]
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    model.fit(matrix(fit), fit["had_incident"])
    auc = roc_auc_score(hold["had_incident"], model.predict_proba(matrix(hold))[:, 1]) \
        if hold["had_incident"].nunique() == 2 else float("nan")
    model.fit(matrix(df), df["had_incident"])  # final model uses every day
    return model, {"holdout_auc": float(auc), "shifts": int(len(df)), "incident_rate": float(df["had_incident"].mean())}


_LABELS = {"hours": lambda v: f"{v:.0f} h shift", "avg_noise_db": lambda v: f"≈{v:.0f} dB noise",
           "noise_dose_pct": lambda v: f"{v:.0f}% noise dose", "motion_std": lambda v: "jerky motion",
           "incidents_7d": lambda v: f"{v:.0f} incident{'s' if v != 1 else ''} in 7 days"}


def top_factors(model, row, n=2):
    """The n features that raise this worker's risk the most (coefficient × standardized value)."""
    scaler, lr = model.named_steps["standardscaler"], model.named_steps["logisticregression"]
    x = matrix(pd.DataFrame([row]))
    contrib = (scaler.transform(x)[0]) * lr.coef_[0]
    out = []
    for i in np.argsort(-contrib):
        name = FEATURES[i]
        if name.startswith("zone_") and name != f"zone_{row['zone']}":
            continue  # "not in the Office" isn't something to tell a supervisor; only the worker's own zone is
        out.append(f"works in {name[5:]}" if name.startswith("zone_") else _LABELS[name](float(row[name])))
        if len(out) == n:
            break
    return out


def score(model, df):
    """Adds risk (0-1) and top_factors to next-shift feature rows."""
    out = df.copy().reset_index(drop=True)
    out["risk"] = model.predict_proba(matrix(out))[:, 1]
    out["top_factors"] = [top_factors(model, r) for r in out.to_dict("records")]
    return out


def next_shift_rows(live, workers, incidents_7d):
    """Features for each current worker's next shift: their zone now, a planned 8 h shift, last shift's levels."""
    last = live.sort_values("shift_date").groupby("device_id").tail(1).set_index("device_id")
    rows = []
    for w in workers.to_dict("records"):
        zone = w.get("zone") if w.get("zone") in ZONES else "Office"
        prev = last.loc[w["device_id"]] if w["device_id"] in last.index else None
        noise = float(prev["avg_noise_db"]) if prev is not None else float(ZONES[zone])
        rows.append({"device_id": w["device_id"], "worker_name": w.get("worker_name"), "zone": zone,
                     "hours": PLANNED_HOURS, "avg_noise_db": noise,
                     "noise_dose_pct": noise_dose_pct(noise, PLANNED_HOURS),
                     "motion_std": float(prev["motion_std"]) if prev is not None else 0.06,
                     "incidents_7d": int(incidents_7d.get(w["device_id"], 0))})
    return pd.DataFrame(rows)
