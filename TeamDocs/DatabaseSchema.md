# Database Schema
## Gist

The database has three active areas today:

| Area | Main Tables | Why It Matters |
|---|---|---|
| Instrument universe | `StockDB`, `WatchedInstrument`, `OptionInstrument` | Defines what symbols/contracts we track. |
| Market history | `UnderlyingSnapshot`, `UnderlyingCandle5m`, `OptionSnapshot`, `OptionSnapshotCalc`, `MarketActivityDaily` | Feeds prediction and backtesting. |
| Calendar/auth | `TradingCalendar`, `KiteAccessToken` | Supports trading-day logic and Kite API access. |

Most signal/trade/live tables are planned or partial. Start with the active tables above unless you are changing future model/trading workflows.

## Reference

This is the single reference for database tables and views used by the project.

Database: Azure SQL Server / SQL Server
Schema: `dbo`
Main DB client: `src/data_manager/db/database_client.py`
Main migration: `src/data_manager/db/migrations/001_create_trading_system_tables.sql`

## Data Areas

| Area | Tables | Purpose |
|---|---|---|
| Instrument reference | `StockDB`, `WatchedInstrument`, `OptionInstrument` | Stores the universe of stocks, indices, and option contracts that the system can track. |
| Market data | `UnderlyingSnapshot`, `UnderlyingCandle5m`, `OptionSnapshot`, `OptionSnapshotCalc`, `OptionCandle5m`, `MarketActivityDaily` | Stores daily/intraday prices, option-chain snapshots, Greeks, IV, volume, and OI context. |
| Calendar | `TradingCalendar` | Stores exchange trading days, holidays, and expiry markers. |
| Signal engine | `SignalFeatureDaily`, `SignalPrediction`, `SignalBacktestLabel`, `ModelRun` | Stores features, predictions, labels, and model run metadata. |
| News / macro / events | `MacroFactorDaily`, `NewsEvent`, `CorporateEventCalendar` | Stores external context used by InstrumentWatcher and prediction features. |
| Option trade selection | `OptionTradePlan`, `OptionPaperTradeResult` | Stores selected option trades and paper-trade outcomes. |
| Live trading placeholders | `LiveOrder`, `LivePosition`, `ExecutionFill`, `RiskLimitDaily` | Future/live execution tables created by migration but not central to the current data refresh flow. |
| Auth | `KiteAccessToken` | Stores the current Kite access token. |

## Current Active Data Flow

1. `StockDB` is populated from Kite instrument master data.
2. `WatchedInstrument` stores the active stocks/indices that should be tracked.
3. `OptionInstrument` stores option contracts for active watched underlyings.
4. Daily/backfill scripts populate:
   - `UnderlyingSnapshot`
   - `UnderlyingCandle5m`
   - `OptionSnapshot`
   - `OptionSnapshotCalc`
   - `MarketActivityDaily`
5. Readers under `src/data_manager` query these tables for analysis, backtests, and services.

## Table Catalog

| Table | Status | Contains | Primary Purpose |
|---|---|---|---|
| `StockDB` | Active | Kite stock/index instrument master rows. | Search and seed `WatchedInstrument`. |
| `WatchedInstrument` | Active | The selected universe of stocks/indices. | Source list for daily refresh, backfill, and InstrumentWatcher output. |
| `OptionInstrument` | Active | Option contract reference data. | Maps Kite option tokens to underlying/expiry/strike/type. |
| `UnderlyingSnapshot` | Active | Daily OHLCV per watched underlying. | Daily price snapshots/features. |
| `UnderlyingCandle5m` | Active | 5-minute OHLCV per watched underlying. | Intraday features, labels, backtests. |
| `OptionSnapshot` | Active | Raw option quote snapshots. | Option chain state, price, OI, bid/ask, volume. |
| `OptionSnapshotCalc` | Active | IV and Greeks per option snapshot. | Option selection and analytics. |
| `MarketActivityDaily` | Active | Near-month futures/OI/volume proxy data. | Derivative activity and volume/OI features. |
| `TradingCalendar` | Active | Trading day and expiry calendar. | Trading-day aware refresh/backtest scheduling. |
| `KiteAccessToken` | Active | Latest Kite access token. | Kite API authentication. |
| `SignalFeatureDaily` | Planned/partial | Daily feature-store rows. | Prediction input features. |
| `SignalPrediction` | Planned/partial | Model prediction outputs. | Store signal direction/confidence/no-trade decisions. |
| `SignalBacktestLabel` | Planned/partial | Realized outcomes for predictions. | Backtest evaluation labels. |
| `OptionCandle5m` | Planned/partial | 5-minute option candles. | Option-specific trade simulation and execution analysis. |
| `OptionTradePlan` | Planned/partial | Selected option trade plan. | Converts prediction into option contract, entry, stop, target. |
| `OptionPaperTradeResult` | Planned/partial | Paper/simulated option trade result. | Evaluate option trade plans. |
| `MacroFactorDaily` | Placeholder | Macro market context. | Macro feature inputs. |
| `NewsEvent` | Placeholder | News/event records and impact metadata. | InstrumentWatcher/news feature inputs. |
| `CorporateEventCalendar` | Placeholder | Earnings/events per symbol. | Event-risk features. |
| `ModelRun` | Placeholder | Model run metadata/config/metrics. | Experiment tracking. |
| `LiveOrder` | Placeholder | Broker order records. | Future live trading audit trail. |
| `LivePosition` | Placeholder | Open/closed live positions. | Future live position tracking. |
| `ExecutionFill` | Placeholder | Broker execution fills. | Future execution audit trail. |
| `RiskLimitDaily` | Placeholder | Daily risk controls and realized PnL. | Future live risk gate. |

## Instrument Reference Tables

### `dbo.StockDB`

Reference data for instruments fetched from Kite.

| Column | Type | Description |
|---|---|---|
| `exchange` | `VARCHAR` | Exchange identifier such as `NSE`, `BSE`, `NFO`. |
| `tradingsymbol` | `VARCHAR` | Kite trading symbol. |
| `name` | `VARCHAR` | Display name. |
| `instrument_token` | `BIGINT/INT` | Kite instrument token. |
| `segment` | `VARCHAR` | Kite segment, for example `NSE`, `NSE-INDICES`, `NFO-OPT`. |
| `tick_size` | `FLOAT` | Minimum price increment. |
| `lot_size` | `INT` | Lot size. |

Used by:

| File | Usage |
|---|---|
| `src/data_manager/db/database_client.py` | Insert/truncate/search/count stock master rows. |
| `scripts/populate_watched_instruments.py` | Seeds index rows into `WatchedInstrument`. |

### `dbo.WatchedInstrument`

Active universe of instruments that the system tracks.

| Column | Type | Description |
|---|---|---|
| `watched_id` | `BIGINT IDENTITY PK` | Internal watched instrument id. |
| `tradingsymbol` | `VARCHAR(50)` | Canonical symbol used by the app, such as `NIFTY`, `BANKNIFTY`, `RELIANCE`. |
| `exchange` | `VARCHAR(20)` | Exchange. |
| `name` | `VARCHAR(200)` | Display/company/index name. |
| `instrument_token` | `BIGINT` | Kite token for underlying/index. |
| `segment` | `VARCHAR(50)` | Kite segment. |
| `tick_size` | `FLOAT` | Minimum price increment. |
| `lot_size` | `INT` | Lot size. |
| `instrument_type` | `VARCHAR(30)` | `STOCK`, `INDEX`, or `SECTOR_INDEX`. |
| `sector` | `VARCHAR(100)` | Sector classification, if known. |
| `industry` | `VARCHAR(100)` | Industry classification, if known. |
| `is_fo_enabled` | `BIT` | Whether F&O/option workflows apply. |
| `is_active` | `BIT` | Whether daily/backfill jobs should include it. |
| `created_at` | `DATETIME2` | Row creation timestamp. |
| `updated_at` | `DATETIME2` | Last update timestamp. |

Key constraints/indexes:

| Constraint/Index | Definition |
|---|---|
| `pk_watched_instrument` | Primary key on `watched_id`. |
| `uq_watched_instrument` | Unique on `(tradingsymbol, exchange)`. |
| `ix_watched_active` | `(is_active, instrument_type, tradingsymbol)`. |
| `ix_watched_token` | `(instrument_token)`. |

### `dbo.OptionInstrument`

Option contract master table.

| Column | Type | Description |
|---|---|---|
| `id` | `INT/BIGINT IDENTITY PK` | Internal option instrument id. |
| `fetch_date` | `DATE` | Date when contract was fetched. |
| `instrument_token` | `BIGINT/INT` | Kite option instrument token. |
| `underlying` | `VARCHAR` | Underlying symbol. |
| `exchange` | `VARCHAR` | Exchange, usually `NFO`. |
| `tradingsymbol` | `VARCHAR` | Option trading symbol. |
| `name` | `VARCHAR` | Kite name field. |
| `strike` | `FLOAT` | Strike price. |
| `expiry` | `DATE` | Expiry date. |
| `instrument_type` | `VARCHAR` | `CE` or `PE`. |
| `lot_size` | `INT` | Lot size. |
| `tick_size` | `FLOAT` | Minimum price increment. |
| `segment` | `VARCHAR` | Kite segment. |

Expected uniqueness: `instrument_token` should be unique for active inserts. Older docs mention `(underlying, instrument_token)`.

Used by:

| File | Usage |
|---|---|
| `scripts/daily_optionInstrument_refresh.py` | Refreshes contracts for watched underlyings. |
| `scripts/backfill/backfill_nifty_options.py` | Reads index option contracts for option snapshot backfill. |
| `scripts/backfill/backfill_stocks_options.py` | Reads stock option contracts for option snapshot backfill. |
| `src/data_manager/db/database_client.py` | Upsert/select option instruments. |

## Market Data Tables

### `dbo.UnderlyingSnapshot`

Daily OHLCV per watched stock/index.

| Column | Type | Description |
|---|---|---|
| `underlying` | `VARCHAR` | Watched symbol. |
| `trade_date` | `DATE` | Trading date. |
| `loaded_at` | `DATETIME2` | Time data was loaded. |
| `open_price` | `FLOAT` | Daily open. |
| `high_price` | `FLOAT` | Daily high. |
| `low_price` | `FLOAT` | Daily low. |
| `close_price` | `FLOAT` | Daily close. |
| `volume` | `BIGINT/INT` | Daily volume. |

Expected uniqueness: `(underlying, trade_date)`.

Purpose: compact daily price history for features, summaries, and backtests.

### `dbo.UnderlyingCandle5m`

5-minute OHLCV candles for watched stocks/indices.

| Column | Type | Description |
|---|---|---|
| `underlying` | `VARCHAR` | Watched symbol. |
| `trade_date` | `DATE` | Trading date. |
| `candle_time` | `DATETIME2` | Candle timestamp. |
| `open_price` | `FLOAT` | Candle open. |
| `high_price` | `FLOAT` | Candle high. |
| `low_price` | `FLOAT` | Candle low. |
| `close_price` | `FLOAT` | Candle close. |
| `volume` | `BIGINT/INT` | Candle volume. |

Expected uniqueness: `(underlying, candle_time)`.

Purpose: intraday technical analysis, label creation, stop/target testing, and replay/backtest inputs.

### `dbo.OptionSnapshot`

Raw option market snapshot table.

| Column | Type | Description |
|---|---|---|
| `id` | `INT/BIGINT IDENTITY PK` | Internal snapshot id. |
| `option_instrument_id` | `INT/BIGINT FK` | Reference to `OptionInstrument.id`. |
| `snapshot_time` | `DATETIME2` | Snapshot timestamp. |
| `underlying_price` | `FLOAT` | Underlying price at snapshot time. |
| `last_price` | `FLOAT` | Option last traded price. |
| `bid_price` | `FLOAT` | Best bid price. |
| `bid_qty` | `INT` | Best bid quantity. |
| `ask_price` | `FLOAT` | Best ask price. |
| `ask_qty` | `INT` | Best ask quantity. |
| `volume` | `BIGINT/INT` | Option volume. |
| `open_interest` | `BIGINT/INT` | Option open interest. |

Purpose: option-chain state at selected times. Current scripts target two snapshots per day, around `09:15` and `15:15`.

### `dbo.OptionSnapshotCalc`

Calculated analytics for each option snapshot.

| Column | Type | Description |
|---|---|---|
| `option_snapshot_id` | `INT/BIGINT FK` | Reference to `OptionSnapshot.id`. |
| `implied_volatility` | `FLOAT` | Calculated IV. |
| `delta` | `FLOAT` | Option delta. |
| `gamma` | `FLOAT` | Option gamma. |
| `theta` | `FLOAT` | Option theta. |
| `vega` | `FLOAT` | Option vega. |

Purpose: option selection, strategy scoring, and volatility analysis.

### `dbo.OptionCandle5m`

5-minute option candles for option-specific replay and execution evaluation.

| Column | Type | Description |
|---|---|---|
| `option_candle_id` | `BIGINT IDENTITY PK` | Internal candle id. |
| `option_instrument_id` | `BIGINT` | Reference to option contract. |
| `tradingsymbol` | `VARCHAR(100)` | Option trading symbol. |
| `underlying` | `VARCHAR(50)` | Underlying symbol. |
| `candle_time` | `DATETIME2` | Candle timestamp. |
| `open_price` | `FLOAT` | Candle open. |
| `high_price` | `FLOAT` | Candle high. |
| `low_price` | `FLOAT` | Candle low. |
| `close_price` | `FLOAT` | Candle close. |
| `volume` | `BIGINT` | Option candle volume. |
| `open_interest` | `BIGINT` | Open interest at/near candle time. |
| `data_purpose` | `VARCHAR(30)` | Usage label such as `BACKTEST_CANDIDATE`, `PAPER_TRADE`, `LIVE_TRADE`, `MANUAL_BACKFILL`. |
| `source` | `VARCHAR(50)` | Data source. |
| `created_at` | `DATETIME2` | Creation timestamp. |

Key constraints/indexes:

| Constraint/Index | Definition |
|---|---|
| `uq_option_candle_5m` | Unique on `(option_instrument_id, candle_time)`. |
| `ix_option_candle_symbol_time` | `(tradingsymbol, candle_time)`. |
| `ix_option_candle_underlying_time` | `(underlying, candle_time)`. |
| `ix_option_candle_purpose` | `(data_purpose, underlying, candle_time)`. |

### `dbo.MarketActivityDaily`

Daily near-month futures activity/OI/volume proxy data from NSE FO bhavcopy.

| Column | Type | Description |
|---|---|---|
| `underlying` | `VARCHAR` | Underlying symbol. |
| `trade_date` | `DATE` | Trading date. |
| `fin_instrm_tp` | `VARCHAR` | NSE UDiFF instrument type, currently `IDF` for index futures. |
| `tckr_symb` | `VARCHAR` | NSE ticker symbol. |
| `expiry_date` | `DATE` | Selected near-month futures expiry. |
| `fin_instrm_nm` | `VARCHAR` | NSE instrument name. |
| `open_price` | `FLOAT` | Futures open. |
| `high_price` | `FLOAT` | Futures high. |
| `low_price` | `FLOAT` | Futures low. |
| `close_price` | `FLOAT` | Futures close. |
| `settle_price` | `FLOAT` | Futures settlement price. |
| `underlying_price` | `FLOAT` | Reported underlying price. |
| `open_interest` | `BIGINT/INT` | Futures open interest. |
| `change_in_oi` | `BIGINT/INT` | Change in open interest. |
| `traded_volume` | `BIGINT/INT` | Traded volume. |
| `traded_value` | `FLOAT` | Traded value. |
| `source_url` | `VARCHAR/NVARCHAR` | NSE bhavcopy URL used for the row. |

Expected uniqueness: `(underlying, trade_date)`.

Purpose: futures volume/OI context for market activity and derivative features. Current NSE volume proxy script supports `NIFTY` and `BANKNIFTY`.

## Calendar Table

### `dbo.TradingCalendar`

Exchange calendar table.

| Column | Type | Description |
|---|---|---|
| `calendar_date` | `DATE` | Calendar date. |
| `exchange` | `VARCHAR(20)` | Exchange, default `NSE`. |
| `is_trading_day` | `BIT` | Whether the exchange is open. |
| `is_weekly_expiry` | `BIT` | Weekly expiry marker. |
| `is_monthly_expiry` | `BIT` | Monthly expiry marker. |
| `is_special_session` | `BIT` | Special trading session marker. |
| `notes` | `VARCHAR(300)` | Optional notes. |
| `created_at` | `DATETIME2` | Creation timestamp. |
| `updated_at` | `DATETIME2` | Last update timestamp. |

Primary key: `(calendar_date, exchange)`.

## Signal Engine Tables

### `dbo.SignalFeatureDaily`

Daily feature store for symbols.

| Column Group | Columns |
|---|---|
| Identity/date | `feature_id`, `signal_date`, `symbol`, `instrument_type` |
| OHLCV | `open_915`, `close_1515`, `high_day`, `low_day`, `volume_day` |
| Returns | `ret_1d`, `ret_3d`, `ret_5d`, `ret_10d`, `ret_20d` |
| Moving averages | `sma_5`, `sma_10`, `sma_20`, `ema_5`, `ema_10`, `ema_20`, `close_vs_sma_5`, `close_vs_sma_10`, `close_vs_sma_20` |
| Momentum | `rsi_14`, `macd`, `macd_signal`, `macd_hist`, `roc_5`, `roc_10` |
| Volatility/range | `atr_14`, `atr_pct`, `realized_vol_5d`, `realized_vol_10d`, `realized_vol_20d`, `day_range_pct`, `gap_pct`, `close_position_in_range` |
| Derivatives/options | `futures_oi_change_pct`, `futures_volume_change_pct`, `pcr_oi`, `pcr_volume`, `atm_iv`, `iv_rank_20d`, `skew_put_call`, `max_oi_call_strike`, `max_oi_put_strike`, `distance_from_max_call_oi_pct`, `distance_from_max_put_oi_pct` |
| External context | `macro_score`, `news_score`, `event_risk_score`, `regime` |
| Metadata | `feature_version`, `source_quality_score`, `reason_json`, `strategy_features_json`, `created_at`, `updated_at` |

Key constraints/indexes:

| Constraint/Index | Definition |
|---|---|
| `pk_signal_feature_daily` | Primary key on `feature_id`. |
| `uq_signal_feature_daily` | Unique on `(signal_date, symbol, feature_version)`. |
| `ix_signal_feature_symbol_date` | `(symbol, signal_date)`. |
| `ix_signal_feature_regime` | `(signal_date, regime)`. |

### `dbo.SignalPrediction`

Model output table.

| Column | Type | Description |
|---|---|---|
| `prediction_id` | `BIGINT IDENTITY PK` | Internal prediction id. |
| `signal_date` | `DATE` | Date features/signals were generated. |
| `trade_date` | `DATE` | Intended trade date. |
| `symbol` | `VARCHAR(50)` | Symbol. |
| `instrument_type` | `VARCHAR(30)` | Instrument type. |
| `model_name` | `VARCHAR(100)` | Model name. |
| `model_version` | `VARCHAR(50)` | Model version. |
| `direction` | `VARCHAR(20)` | Predicted direction, such as `UP`, `DOWN`, `NO_TRADE`. |
| `confidence` | `FLOAT` | Model confidence. |
| `expected_move_pct` | `FLOAT` | Expected move. |
| `trade_allowed` | `BIT` | Whether downstream trade selection can proceed. |
| `no_trade_reason` | `VARCHAR(200)` | Reason trade is blocked. |
| `regime` | `VARCHAR(30)` | Market regime. |
| `feature_id` | `BIGINT` | Related `SignalFeatureDaily.feature_id`. |
| `reason_json` | `NVARCHAR(MAX)` | Explanation payload. |
| `created_at` | `DATETIME2` | Creation timestamp. |

Unique key: `(signal_date, trade_date, symbol, model_name, model_version)`.

### `dbo.SignalBacktestLabel`

Realized label/outcome table for backtesting predictions.

| Column | Type | Description |
|---|---|---|
| `label_id` | `BIGINT IDENTITY PK` | Internal label id. |
| `signal_date` | `DATE` | Signal date. |
| `trade_date` | `DATE` | Trade date. |
| `symbol` | `VARCHAR(50)` | Symbol. |
| `entry_time` | `DATETIME2` | Simulated entry time. |
| `exit_time` | `DATETIME2` | Simulated exit time. |
| `entry_price` | `FLOAT` | Entry price. |
| `exit_price` | `FLOAT` | Exit price. |
| `realized_return_pct` | `FLOAT` | Realized return. |
| `positive_threshold_pct` | `FLOAT` | Positive label threshold. |
| `negative_threshold_pct` | `FLOAT` | Negative label threshold. |
| `actual_label` | `VARCHAR(20)` | Realized label. |
| `label_version` | `VARCHAR(50)` | Labeling logic version. |
| `created_at` | `DATETIME2` | Creation timestamp. |
| `updated_at` | `DATETIME2` | Last update timestamp. |

Unique key: `(signal_date, trade_date, symbol, label_version)`.

### `dbo.ModelRun`

Model training/evaluation metadata.

| Column | Type | Description |
|---|---|---|
| `model_run_id` | `BIGINT IDENTITY PK` | Internal model run id. |
| `model_name` | `VARCHAR(100)` | Model name. |
| `model_version` | `VARCHAR(50)` | Model version. |
| `run_date` | `DATETIME2` | Run timestamp. |
| `train_start_date` | `DATE` | Training window start. |
| `train_end_date` | `DATE` | Training window end. |
| `test_start_date` | `DATE` | Test window start. |
| `test_end_date` | `DATE` | Test window end. |
| `config_json` | `NVARCHAR(MAX)` | Model configuration. |
| `metrics_json` | `NVARCHAR(MAX)` | Evaluation metrics. |
| `notes` | `NVARCHAR(MAX)` | Notes. |

## News, Macro, and Event Tables

### `dbo.MacroFactorDaily`

Daily macro context table.

| Column | Type | Description |
|---|---|---|
| `macro_id` | `BIGINT IDENTITY PK` | Internal macro row id. |
| `factor_date` | `DATE` | Macro date. |
| `india_vix` | `FLOAT` | India VIX value. |
| `gift_nifty_return_pct` | `FLOAT` | GIFT Nifty return. |
| `dow_return_pct` | `FLOAT` | Dow return. |
| `nasdaq_return_pct` | `FLOAT` | Nasdaq return. |
| `sp500_return_pct` | `FLOAT` | S&P 500 return. |
| `crude_return_pct` | `FLOAT` | Crude oil return. |
| `usd_inr_return_pct` | `FLOAT` | USD/INR return. |
| `bond_yield_change` | `FLOAT` | Bond yield change. |
| `event_flag` | `BIT` | Whether a macro event is present. |
| `event_type` | `VARCHAR(100)` | Event type. |
| `source_json` | `NVARCHAR(MAX)` | Source payload. |
| `created_at` | `DATETIME2` | Creation timestamp. |
| `updated_at` | `DATETIME2` | Last update timestamp. |

Unique key: `(factor_date)`.

### `dbo.NewsEvent`

News/event capture table for InstrumentWatcher-style analysis.

| Column | Type | Description |
|---|---|---|
| `news_id` | `BIGINT IDENTITY PK` | Internal news id. |
| `news_time` | `DATETIME2` | Publication/event time. |
| `symbol` | `VARCHAR(50)` | Directly affected symbol, if known. |
| `sector` | `VARCHAR(100)` | Affected sector. |
| `industry` | `VARCHAR(100)` | Affected industry. |
| `source` | `VARCHAR(100)` | News source. |
| `headline` | `NVARCHAR(500)` | Headline. |
| `url` | `NVARCHAR(1000)` | Source URL. |
| `sentiment_score` | `FLOAT` | Sentiment score. |
| `impact_score` | `FLOAT` | Estimated market impact score. |
| `event_type` | `VARCHAR(100)` | Event/category type. |
| `summary` | `NVARCHAR(MAX)` | Summary. |
| `raw_json` | `NVARCHAR(MAX)` | Raw extraction payload. |
| `created_at` | `DATETIME2` | Creation timestamp. |

Indexes: `(symbol, news_time)`, `(sector, news_time)`, `(news_time)`.

### `dbo.CorporateEventCalendar`

Symbol-level event calendar.

| Column | Type | Description |
|---|---|---|
| `event_id` | `BIGINT IDENTITY PK` | Internal event id. |
| `symbol` | `VARCHAR(50)` | Symbol. |
| `event_date` | `DATE` | Event date. |
| `event_type` | `VARCHAR(100)` | Event type. |
| `event_description` | `NVARCHAR(500)` | Description. |
| `source` | `VARCHAR(100)` | Source name. |
| `source_url` | `NVARCHAR(1000)` | Source URL. |
| `created_at` | `DATETIME2` | Creation timestamp. |

Unique key: `(symbol, event_date, event_type)`.

## Option Trade Tables

### `dbo.OptionTradePlan`

Option trade selected from a prediction.

| Column | Type | Description |
|---|---|---|
| `trade_plan_id` | `BIGINT IDENTITY PK` | Internal trade plan id. |
| `prediction_id` | `BIGINT` | Source prediction id. |
| `signal_date` | `DATE` | Signal date. |
| `trade_date` | `DATE` | Intended trade date. |
| `underlying` | `VARCHAR(50)` | Underlying symbol. |
| `direction` | `VARCHAR(20)` | Predicted direction. |
| `option_instrument_id` | `BIGINT` | Selected option contract id. |
| `tradingsymbol` | `VARCHAR(100)` | Selected option symbol. |
| `expiry` | `DATE` | Option expiry. |
| `strike` | `FLOAT` | Selected strike. |
| `option_type` | `VARCHAR(5)` | `CE` or `PE`. |
| `expected_entry_time` | `DATETIME2` | Planned entry time. |
| `expected_entry_price` | `FLOAT` | Planned entry price. |
| `stop_loss_price` | `FLOAT` | Stop price. |
| `target_price` | `FLOAT` | Target price. |
| `expected_underlying_move_pct` | `FLOAT` | Expected underlying move. |
| `expected_option_return_pct` | `FLOAT` | Expected option return. |
| `expected_pnl_per_lot` | `FLOAT` | Expected PnL per lot. |
| `max_loss_per_lot` | `FLOAT` | Max loss per lot. |
| `liquidity_score` | `FLOAT` | Liquidity score. |
| `greek_score` | `FLOAT` | Greeks score. |
| `iv_score` | `FLOAT` | IV score. |
| `risk_reward` | `FLOAT` | Risk/reward ratio. |
| `total_score` | `FLOAT` | Overall selection score. |
| `selection_reason_json` | `NVARCHAR(MAX)` | Explanation payload. |
| `status` | `VARCHAR(30)` | Plan status, default `PLANNED`. |
| `created_at` | `DATETIME2` | Creation timestamp. |
| `updated_at` | `DATETIME2` | Last update timestamp. |

### `dbo.OptionPaperTradeResult`

Paper-trade outcome for an option trade plan.

| Column | Type | Description |
|---|---|---|
| `paper_trade_id` | `BIGINT IDENTITY PK` | Internal paper trade id. |
| `trade_plan_id` | `BIGINT` | Source trade plan id. |
| `trade_date` | `DATE` | Trade date. |
| `underlying` | `VARCHAR(50)` | Underlying symbol. |
| `tradingsymbol` | `VARCHAR(100)` | Option symbol. |
| `simulated_entry_time` | `DATETIME2` | Simulated entry time. |
| `simulated_exit_time` | `DATETIME2` | Simulated exit time. |
| `entry_price` | `FLOAT` | Entry price. |
| `exit_price` | `FLOAT` | Exit price. |
| `stop_loss_price` | `FLOAT` | Stop price. |
| `target_price` | `FLOAT` | Target price. |
| `exit_reason` | `VARCHAR(30)` | `TARGET`, `STOP_LOSS`, `EOD_EXIT`, `NO_FILL`, etc. |
| `lot_size` | `INT` | Lot size. |
| `quantity` | `INT` | Quantity. |
| `gross_pnl` | `FLOAT` | Gross PnL. |
| `estimated_transaction_cost` | `FLOAT` | Cost estimate. |
| `estimated_slippage` | `FLOAT` | Slippage estimate. |
| `net_pnl` | `FLOAT` | Net PnL. |
| `return_pct` | `FLOAT` | Return percentage. |
| `created_at` | `DATETIME2` | Creation timestamp. |

## Live Trading Placeholder Tables

### `dbo.LiveOrder`

Broker order audit table.

Columns: `live_order_id`, `trade_plan_id`, `broker`, `broker_order_id`, `order_time`, `tradingsymbol`, `exchange`, `transaction_type`, `order_type`, `product_type`, `quantity`, `price`, `trigger_price`, `status`, `status_message`, `raw_response_json`, `created_at`.

Purpose: store submitted broker orders and raw broker responses.

### `dbo.LivePosition`

Live position tracking table.

Columns: `live_position_id`, `trade_plan_id`, `tradingsymbol`, `underlying`, `open_time`, `close_time`, `quantity`, `avg_entry_price`, `avg_exit_price`, `stop_loss_price`, `target_price`, `status`, `gross_pnl`, `net_pnl`, `created_at`, `updated_at`.

Purpose: track current and closed positions in a future live trading flow.

### `dbo.ExecutionFill`

Broker fill/audit table.

Columns: `execution_fill_id`, `live_order_id`, `broker_order_id`, `fill_time`, `tradingsymbol`, `quantity`, `fill_price`, `fees`, `raw_json`, `created_at`.

Purpose: store fills linked to broker orders.

### `dbo.RiskLimitDaily`

Daily risk-control state table.

Columns: `risk_limit_id`, `trade_date`, `max_loss_per_day`, `max_trades_per_day`, `max_open_positions`, `realized_pnl`, `open_risk`, `trades_taken`, `trading_enabled`, `disable_reason`, `created_at`, `updated_at`.

Unique key: `(trade_date)`.

Purpose: future live trading risk gate.

## Auth Table

### `dbo.KiteAccessToken`

Stores the current Kite access token.

| Column | Type | Description |
|---|---|---|
| `id` | `INT IDENTITY PK` | Token row id. |
| `access_token` | `NVARCHAR(MAX)` | Kite access token. |
| `created_at` | `DATETIME2` | Creation timestamp. |
| `updated_at` | `DATETIME2` | Last update timestamp. |

Notes:

- `database_client.py` creates this table dynamically if missing.
- `get_kite_access_token()` also checks legacy/case variants: `kiteAccessToken`, `KiteAccessToken`, with and without `dbo`.

## Views

### `dbo.vw_LatestOptionSnapshot`

Returns the latest row from `OptionSnapshot` per `option_instrument_id`.

Columns: all `OptionSnapshot` columns plus ranking logic internally.

### `dbo.vw_LatestOptionSnapshotWithGreeks`

Joins latest option snapshot rows to `OptionSnapshotCalc`.

Columns: `option_instrument_id`, `snapshot_time`, `underlying_price`, `last_price`, `bid_price`, `ask_price`, `volume`, `open_interest`, `implied_volatility`, `delta`, `gamma`, `theta`, `vega`.

### `dbo.vw_SignalBacktestEvaluation`

Joins `SignalPrediction` to `SignalBacktestLabel` and adds `is_correct`.

Purpose: evaluate prediction direction against realized labels.

### `dbo.vw_OptionTradePlanWithResult`

Joins `OptionTradePlan` to `OptionPaperTradeResult`.

Purpose: analyze planned trades with realized paper-trade results.

### Legacy Compatibility Note: `dbo.vw_OptionLatestSnapshot`

Older code in `DatabaseClient.fetch_latest_option_chain_for_underlying()` first tries `dbo.vw_OptionLatestSnapshot` and then falls back to a direct latest-snapshot join. The migration creates `dbo.vw_LatestOptionSnapshot`, so the code/view naming should be aligned when this area is cleaned up.

## Relationships

```text
StockDB
  -> WatchedInstrument
       -> OptionInstrument
            -> OptionSnapshot
                 -> OptionSnapshotCalc
            -> OptionCandle5m

WatchedInstrument
  -> UnderlyingSnapshot
  -> UnderlyingCandle5m
  -> MarketActivityDaily

SignalFeatureDaily
  -> SignalPrediction
       -> OptionTradePlan
            -> OptionPaperTradeResult
            -> LiveOrder
                 -> ExecutionFill
            -> LivePosition
```

## Refresh and Backfill Ownership

| Table | Main writer |
|---|---|
| `StockDB` | `DatabaseClient` stock insert/truncate methods. |
| `WatchedInstrument` | `scripts/populate_watched_instruments.py`, `DatabaseClient.upsert_watched_instruments()`. |
| `OptionInstrument` | `scripts/daily_optionInstrument_refresh.py`, `DatabaseClient.upsert_option_instruments()`. |
| `UnderlyingSnapshot` | `scripts/daily_market_refresh.py`, `scripts/backfill/backfill_nifty_underlying.py`, `scripts/backfill/backfill_stocks_underlying.py`. |
| `UnderlyingCandle5m` | `scripts/daily_market_refresh.py`, `scripts/backfill/backfill_nifty_underlying.py`, `scripts/backfill/backfill_stocks_underlying.py`. |
| `OptionSnapshot` | `scripts/daily_market_refresh.py`, `scripts/backfill/backfill_nifty_options.py`, `scripts/backfill/backfill_stocks_options.py`, `DatabaseClient.bulk_insert_option_data()`. |
| `OptionSnapshotCalc` | Same as `OptionSnapshot`; calculated during option data ingestion. |
| `MarketActivityDaily` | `scripts/backfill/backfill_nifty_volumeproxy.py`. |
| `TradingCalendar` | `scripts/build_trading_calendar.py`. |
| `KiteAccessToken` | `scripts/daily_get_kite_access_token.py`, `DatabaseClient.save_kite_access_token()`. |

## Source Files

| File | Role |
|---|---|
| `src/data_manager/db/database_client.py` | Main SQL client and active insert/query methods. |
| `src/data_manager/db/migrations/001_create_trading_system_tables.sql` | Idempotent migration for signal/trading/planned tables and views. |
| `src/common/models.py` | Dataclass models for instruments, option data, signals, and trade plans. |
| `src/data_manager/option_history_reader.py` | Reads option, underlying, and market activity history for analysis. |
| `src/data_manager/underlying_history_reader.py` | Reads underlying daily and 5-minute history. |
| `scripts/daily_market_refresh.py` | Daily orchestrated market data refresh. |
| `scripts/daily_market_refresh.py` | Main daily refresh entrypoint for underlying and option market data via `BackfillService`. |
| `scripts/daily_optionInstrument_refresh.py` | Daily option instrument refresh. |
| `scripts/backfill/backfill_nifty_underlying.py` | Index underlying backfill. |
| `scripts/backfill/backfill_stocks_underlying.py` | Stock underlying backfill. |
| `scripts/backfill/backfill_nifty_options.py` | Index option snapshot backfill. |
| `scripts/backfill/backfill_stocks_options.py` | Stock option snapshot backfill. |
| `scripts/backfill/backfill_nifty_volumeproxy.py` | Market activity/OI/volume proxy backfill. |

