# ⛽ GasPredict: Fuel Price Predictive Engine & Monitoring

> **Status:** Proof of Concept / Research Concluded. 
> **Goal:** End-to-end data pipeline, monitoring, and predictive modeling for hyper-local retail fuel prices in Spain.

GasPredict is a dual-track project. It consists of a **Production Monitoring Stack** that tracks real-time fuel prices across the Valencian Community, and a **Machine Learning Research Track** that explores the viability of using state-of-the-art Deep Learning (Temporal Fusion Transformers) for price forecasting.

## 🏗️ Architecture & Tech Stack

The project is divided into a microservices architecture deployed via Docker, and an offline ML research environment.

```mermaid
flowchart TD
    subgraph Data Sources
        MITECO[MITECO Geoportal API\n(Spanish Gov)]
        YF[Yahoo Finance API\n(Macro: Brent, EUR/USD)]
    end

    subgraph Production Engine (Docker)
        ETL_G[ETL Geoportal\nPython]
        ETL_M[ETL Macro\nPython]
        HEUR[Econometric Engine\nHeuristic Recommender]
        NOTIF[Telegram Notifier\nAlert System]
    end

    subgraph Storage & Viz
        TSDB[(TimescaleDB\nPostgreSQL 16)]
        GRAF[Grafana\nDashboards]
    end

    subgraph ML Research (Local/GPU)
        TFT[Temporal Fusion Transformer\nPyTorch Forecasting]
        JUP[Jupyter\nTraining & Eval]
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

## 🧠 The Two Brains: Production vs. Research

### 1. The Production Engine (Heuristic/Econometric)
Currently deployed in production. Instead of a black-box neural network, it uses a deterministic econometric model based on the **Synthetic Crack Spread** and macroeconomic lag.

*   **ETL Pipeline:** Runs twice daily (07:15 and 18:30) fetching prices for all gas stations in the region, alongside Brent and Forex markets.
*   **The Recommender:** Calculates the optimal day to refuel (7-day horizon) based on mathematical baselines (IEH tax floors + VAT) and the 15-day derivative of Brent in Euros.
*   **Telegram Bot:** Sends a daily summary comparing the user's regular station with the cheapest station dynamically found along their commute route.

### 2. The Machine Learning Track (TFT)
A research effort to predict the *exact* price of fuel 7 days ahead using a **Temporal Fusion Transformer (TFT)**. 

*   **Why TFT?** It excels at combining static covariates (Station ID, municipality, brand), known future inputs (day of the week, holidays), and unknown future inputs (past prices, macro indicators).
*   **Performance:** Achieved a validation loss of `0.0235` (QuantileLoss) on the full dataset, capturing the general trend successfully.
*   **Interpretability:** Used TFT's inherent attention mechanisms to extract variable importance (identifying Brent lag as a primary driver, but exposing severe spatial dependencies).

---

## 🔬 Key Learnings: Why the ML Model didn't go to Production

Despite the good validation metrics of the TFT model, a conscious architectural decision was made to keep the heuristic model in production. The domain research revealed fundamental structural issues in the retail fuel market that make pure ML forecasting unreliable for consumer decision-making:

1.  **The "Rockets and Feathers" Effect (Asymmetric Transmission):** Fuel prices rise quickly when crude oil rises (rockets), but fall slowly when it drops (feathers). The trigger for this is the physical inventory rotation of the underground tanks at each specific station. Without real-time access to the station's inventory levels (which is private data), the model is blind to the most critical feature.
2.  **Algorithmic Oligopolies:** Major brands (Repsol, BP, etc.) use private dynamic pricing software (e.g., Kalibrate) with hundreds of business rules. The model attempts to predict statistical human demand, but it is actually fighting against deterministic, private corporate algorithms.
3.  **Regulatory Noise:** Sudden government decrees (e.g., mandatory 20-cent subsidies, tax modifications) introduce massive, unmodellable shocks to the time-series data.

**Conclusion:** The Temporal Fusion Transformer is a powerful architecture, but it requires a domain with genuine signal (like human mobility demand or weather-dependent energy consumption). In highly intervened, inventory-blind markets like retail fuel, a robust heuristic pipeline provides more honest and actionable value to the end user.
