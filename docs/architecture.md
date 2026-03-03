# Trading Bot Architecture

## System Overview

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "background": "#0B1020",
    "mainBkg": "#121A2B",
    "primaryColor": "#3B82F6",
    "primaryTextColor": "#E5E7EB",
    "primaryBorderColor": "#93C5FD",
    "lineColor": "#CBD5E1",
    "secondaryColor": "#10B981",
    "tertiaryColor": "#F59E0B",
    "fontFamily": "Inter, SF Pro Text, Segoe UI, sans-serif"
  }
}}%%
graph TB
    classDef source fill:#0F766E,stroke:#5EEAD4,color:#F0FDFA,stroke-width:1.5px
    classDef scanner fill:#1D4ED8,stroke:#93C5FD,color:#EFF6FF,stroke-width:1.5px
    classDef db fill:#B45309,stroke:#FCD34D,color:#FFFBEB,stroke-width:1.5px
    classDef output fill:#6D28D9,stroke:#C4B5FD,color:#F5F3FF,stroke-width:1.5px

    subgraph Data Sources
        YF[Yahoo Finance API]
        Reddit[Reddit / Manual Trades]
        Group[Group Chat Convos]
    end

    subgraph Scanners
        ES[Elio Scanner<br/>Python + pm2]
        AS[Ana Scanner<br/>Node.js + pm2]
    end

    subgraph Shared DB - Supabase
        HC[(hourly_candles)]
        BS[(breakout_signals)]
        VH[(vix_hourly)]
        MP[(market_predictions)]
        TL[(trades_log)]
    end

    subgraph Outputs
        iMsg[iMessage Group Alert]
        Dash[Supabase Dashboard]
    end

    YF --> ES
    YF --> AS
    Reddit --> TL
    Group --> MP

    ES --> HC
    ES --> BS
    ES --> VH
    AS --> HC
    AS --> BS
    AS --> VH

    BS -->|BREAKOUT_UP / BREAKDOWN| iMsg
    HC --> Dash
    BS --> Dash
    MP --> Dash

    class YF,Reddit,Group source
    class ES,AS scanner
    class HC,BS,VH,MP,TL db
    class iMsg,Dash output
```

## Data Flow (Hourly Cycle)

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "background": "#FFFFFF",
    "mainBkg": "#F8FAFC",
    "primaryTextColor": "#0F172A",
    "lineColor": "#334155",
    "fontFamily": "Inter, SF Pro Text, Segoe UI, sans-serif"
  }
}}%%
sequenceDiagram
    participant PM as pm2
    participant SC as Scanner
    participant YF as Yahoo Finance
    participant DB as Supabase
    participant GC as Group Chat

    PM->>SC: Poll every 5 min
    SC->>SC: Is market open? (9:30-4:30 ET)
    alt Market Closed
        SC->>SC: Sleep
    else Market Open
        SC->>SC: New hour boundary?
        SC->>YF: Fetch SPX + VIX hourly
        YF-->>SC: Candle data
        SC->>SC: Detect breakout/rejection
        SC->>DB: Upsert candles + signals
        alt Strong Signal
            SC->>GC: Alert via iMessage
        end
    end
```

## Breakout Detection Logic

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "background": "#FFFFFF",
    "mainBkg": "#F8FAFC",
    "lineColor": "#334155",
    "fontFamily": "Inter, SF Pro Text, Segoe UI, sans-serif"
  }
}}%%
flowchart TD
    classDef bullish fill:#16A34A,stroke:#166534,color:#F0FDF4,stroke-width:1.5px
    classDef bearish fill:#DC2626,stroke:#7F1D1D,color:#FEF2F2,stroke-width:1.5px
    classDef neutral fill:#64748B,stroke:#334155,color:#F8FAFC,stroke-width:1.5px
    classDef decision fill:#2563EB,stroke:#1E3A8A,color:#EFF6FF,stroke-width:1.5px

    A[Current Hour Candle] --> B{Broke Previous High?}
    B -->|Yes| C{Close > Prev High?}
    C -->|Yes| D[BREAKOUT_UP]:::bullish
    C -->|No| E[REJECTION_HIGH]:::bearish
    B -->|No| F{Broke Previous Low?}
    F -->|Yes| G{Close < Prev Low?}
    G -->|Yes| H[BREAKDOWN]:::bearish
    G -->|No| I[REJECTION_LOW_BOUNCE]:::bullish
    F -->|No| J[INSIDE / Consolidation]:::neutral

    class B,C,F,G decision
```

## Prediction Tracking

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "background": "#FFFFFF",
    "mainBkg": "#F8FAFC",
    "lineColor": "#334155",
    "fontFamily": "Inter, SF Pro Text, Segoe UI, sans-serif"
  }
}}%%
flowchart LR
    A[Pre-Market Thesis] --> B[Log to market_predictions]
    B --> C[Source + Direction + Confidence]
    C --> D[Market Close]
    D --> E[Record Actual Direction]
    E --> F{Correct?}
    F -->|Yes| G[Track Win Rate]
    F -->|No| H[Post-Mortem]
```

## Infrastructure

| Component | Host | Tech | Manager |
|-----------|------|------|---------|
| Elio Scanner | Dan's Mac Mini | Python + yfinance + psycopg2 | pm2 |
| Ana Scanner | Khanh's MacBook | Node.js + pg + https | pm2 |
| Shared DB | Supabase (us-west-2) | PostgreSQL | Supabase |
| Alerts | iMessage Group Chat #6 | imsg CLI / BlueBubbles | OpenClaw |
