# ⛽ GasPredict: Fuel Refueling Optimization Engine

> **Status:** Proof of Concept / Research Concluded. 
> **Goal:** End-to-end data pipeline to optimize refueling decisions based on asymmetric market behaviors ("Rockets and Feathers"), discarding pure ML predictions due to market opacity.

GasPredict is an intelligent fuel monitoring and recommendation system for the Valencian Community. Instead of attempting the impossible task of forecasting the *exact* cent per liter of fuel in an opaque market, it predicts **when the user should refuel** to maximize savings, leveraging deterministic market behaviors.

## 🏗️ Architecture & Tech Stack

The project features a microservices architecture deployed via Docker, handling ETL pipelines, timeseries storage, and automated alerting, alongside an offline ML research environment that was deliberately not pushed to production.

```mermaid
flowchart TD
    subgraph Data Sources
        MITECO[MITECO Geoportal API\n(Spanish Gov)]
        YF[Yahoo Finance API\n(Macro: Brent, EUR/USD)]
    end

    subgraph Production Engine (Docker)
        ETL_G[ETL Geoportal\nPython]
        ETL_M[ETL Macro\nPython]
        HEUR[Decision Engine\nAsymmetric Lag Model]
        NOTIF[Telegram Notifier\nSmart Alerts]
    end

    subgraph Storage & Viz
        TSDB[(TimescaleDB\nPostgreSQL 16)]
        GRAF[Grafana\nDashboards]
    end

    subgraph ML Research (Local/GPU)
        TFT[Temporal Fusion Transformer\nPyTorch Forecasting]
        JUP[Jupyter\nResearch only]
    end

    MITECO --> ETL_G
    YF --> ETL_M
    ETL_G --> TSDB
    ETL_M --> TSDB
    TSDB --> HEUR
    HEUR --> TSDB
    HEUR --> NOTIF
    TSDB --> GRAF
    TSDB -. Parquet Extract .-> TFT
```

### 🛠️ Core Technologies
*   **Data Engineering:** Python, Pandas, SQLAlchemy, Schedule.
*   **Infrastructure & Storage:** Docker Compose, TimescaleDB (Time-series optimized PostgreSQL).
*   **Visualization:** Grafana (Provisioned dashboards via code).
*   **Machine Learning:** PyTorch, PyTorch Forecasting (Temporal Fusion Transformer), Lightning.

---

## 🧠 The Pivot: Why we predict *Action* instead of *Price*

### 1. The Production Engine (Actionable Heuristics)
Currently deployed in production. It uses a deterministic econometric model based on the **Synthetic Crack Spread** and the macroeconomic lag of crude oil.

*   **ETL Pipeline:** Runs twice daily (07:15 and 18:30) fetching prices for all stations in the region, alongside Brent and Forex markets.
*   **The Recommender (The Core):** Calculates the optimal day to refuel within a 7-day window. It hardcodes the "Rockets and Feathers" effect:
    *   *If Crude rises:* The model forces an immediate alert ("Refuel Today!"), mirroring how stations raise prices in 2-3 days (the "Rocket").
    *   *If Crude drops:* The model advises waiting, penalizing the price drop calculation over a 14-day window (the "Feather"), mimicking how stations artificially hold high prices to clear expensive underground inventory.
*   **Telegram Bot:** Sends a daily summary comparing the user's regular station with the dynamically cheapest station along their commute route.

### 2. The Machine Learning Track (The Failed TFT Experiment)
A research effort to predict the *exact* price of fuel 7 days ahead using a **Temporal Fusion Transformer (TFT)**. 

*   **Why TFT?** It theoretically excels at combining static covariates (Station ID), known future inputs (holidays), and unknown future inputs (macro indicators).
*   **Performance:** Achieved a validation loss of `0.0235` (QuantileLoss) on the full dataset. 
*   **The Reality Check:** Despite "good" loss metrics, a 1-3 cent error margin renders the model useless for deciding whether to refuel today or tomorrow. 

---

## 🔬 Key Learnings: The Systemic Limitations of Fuel Forecasting

The conscious architectural decision to deploy the Heuristic Engine over the TFT model highlights critical limitations in applying Deep Learning to intervened markets:

1.  **Blindness to Inventory (The Missing Variable):** The true trigger for price changes is the physical inventory rotation of a station's underground tanks. Since stock levels and tanker delivery schedules are private, the TFT model is completely blind to the primary feature driving local price volatility.
2.  **Fighting Private Algorithms:** Major brands (Repsol, BP, etc.) use dynamic pricing software (e.g., Kalibrate). Attempting to forecast this with statistical ML means trying to predict a deterministic, privately-ruled algorithm rather than natural human demand.
3.  **Regulatory & Fiscal Noise:** Roughly 50% of fuel price in Spain is fixed tax (IEH + VAT). Furthermore, sudden government subsidies introduce massive, unmodellable shocks to the time-series data.

**Conclusion:** Pure Machine Learning (TFT) requires a domain with genuine, endogenous signal (like human mobility demand or behavior). In highly intervened, inventory-blind markets like retail fuel, predicting the *exact price* is a vanity metric. Predicting the *optimal consumer action* via asymmetric heuristics provides honest, robust value.
