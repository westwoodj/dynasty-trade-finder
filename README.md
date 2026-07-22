# Dynasty Trade Finder

A data aggregation and analysis tool that identifies **arbitrage opportunities** in Dynasty
Superflex PPR fantasy football.

## Features

| Feature | Description |
|---------|-------------|
| 🏈 **Sleeper sync** | Connect to any Sleeper league to pull live roster, team, and player data |
| 📊 **Multi-source values** | Leverages the [Parse.bot](https://parse.bot) API to extract player values from KTC and FantasyCalc simultaneously |
| 🔄 **Trade Calculator** | Grade any proposed trade A+→D with superflex PPR-aware value math |
| ⚡ **Best Trades** | Automatically surfaces the highest-value trade proposals for your team |
| 📈 **Arbitrage Finder** | Spots players whose value differs significantly between sources — buy low / sell high |
| 🏟️ **League Overview** | Full roster-value leaderboard with positional depth for every manager |

## Architecture

```
dynasty-trade-finder/
├── app.py                  # Streamlit web UI
├── requirements.txt
├── src/
│   ├── sleeper_client.py   # Sleeper public REST API client
│   ├── parse_bot_client.py # Parse.bot API client (KTC + FantasyCalc extraction)
│   ├── trade_calculator.py # Superflex PPR trade grading engine
│   └── trade_analyzer.py  # Best-trade finder & arbitrage detector
└── tests/
    ├── test_sleeper_client.py
    ├── test_trade_calculator.py
    └── test_trade_analyzer.py
```

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure your Parse.bot API key

Create `.streamlit/secrets.toml` (already ignored by `.gitignore`):

```toml
[parse_bot]
api_key = "your-parse-bot-api-key"
```

Or enter it directly in the app sidebar at runtime.

### 3. Run the app

```bash
streamlit run app.py
```

Then open <http://localhost:8501> in your browser.

### 4. Connect your Sleeper league

1. Enter your **Sleeper username** in the sidebar.
2. Select the **season** and **league**.
3. Explore the tabs: League Overview → My Roster → Trade Explorer → Best Trades → Arbitrage.

## Running tests

```bash
pytest tests/ -v
```

## How the trade calculator works

### Superflex PPR multipliers

Dynasty superflex leagues allow two QBs to start simultaneously, significantly
increasing QB value.  The calculator applies the following multipliers on top of
raw consensus values:

| Position | Multiplier |
|----------|-----------|
| QB | 1.30× |
| RB | 1.00× |
| WR | 1.00× |
| TE | 1.10× |

### Trade grades

| Grade | Value gain (vs giving side) |
|-------|----------------------------|
| A+ | ≥ +20% |
| A | ≥ +10% |
| B+ | ≥ +5% |
| B | −5% to +5% (roughly even) |
| C+ | −5% to −10% |
| C | −10% to −20% |
| D | < −20% |

### Arbitrage detection

The analyzer normalises each source's player values to a 0–100 scale, then
flags any player where `(max_source_value − min_source_value) / consensus_value`
exceeds the configured threshold (default 20%).  Players flagged as **buy** have
at least one source undervaluing them by ≥ 15% below consensus — a signal to
acquire them before the market corrects.

## Configuration reference

| Setting | Default | Description |
|---------|---------|-------------|
| Parse.bot API key | — | Required for live value extraction |
| Season | Current year | NFL season to sync from Sleeper |
| Starter slots | QB×2, RB×2, WR×3, TE×1 | Superflex roster config used for need scoring |
| Arbitrage threshold | 20% | Minimum spread to flag an opportunity |
| Max assets per side | 2 | Controls 1-for-1 and 2-for-2 best-trade search |
