# Predictions Module Documentation

This module supports a full prediction pipeline:

1. Generate index direction predictions (`CALL`, `PUT`, `NO_POSITION`)
2. Optionally map predictions to option contracts
3. Backtest both index signal quality and option trade outcomes

All generated artifacts are written to `output/`.

## Runtime architecture

Core logic lives under `src/`:

- `src/prediction/prediction_service.py`
- `src/prediction/aggregator/index_aggregator.py`
- `src/prediction/aggregator/option_aggregator.py`
- `src/prediction/technical/strategies.py`
- `src/prediction/technical/option_selection_strategies.py`
- `src/prediction/providers/underlying_data_provider.py`
- `src/prediction/providers/options_data_provider.py`
- `src/backtest/index_backtest.py`
- `src/backtest/e2e_backtest.py`

## API usage (recommended)

### Run predictions
```bash
curl -X POST http://localhost:5000/api/predictions/run \
  -H "Content-Type: application/json" \
  -d '{"instrument":"NIFTY","strategies":["MaTrend_001"],"use_agentic":true}'
```

### Run index backtest
```bash
curl -X POST http://localhost:5000/api/predictions/backtest \
  -H "Content-Type: application/json" \
  -d '{"instrument":"NIFTY"}'
```

### List generated files
```bash
curl "http://localhost:5000/api/predictions/files?instrument=NIFTY"
```

## Strategy names (source of truth)

Prediction strategy names are defined in:
- `src/prediction/technical/strategies.py` (`PREDICTION_STRATEGIES`)

Selection strategies are defined in:
- `src/prediction/technical/option_selection_strategies.py` (`SELECTION_STRATEGIES`)

Current prediction names include:
- `trendUpRangeBreakout`
- `MaTrend_001`
- `MaTrend_0005`
- `trendUpMaTrend_001`
- `trendUpMaTrend_0005`
- `trendDownRangeBreakout`
- `trendDownMaTrend_001`
- `trendDownMaTrend_0005`
- `RsiMeanReversion_7030`
- `RsiMeanReversion_6535`
- `rangeRsiMeanReversion_7030`
- `rangeRsiMeanReversion_6535`
- `BollingerMeanReversion`
- `rangeBollingerMeanReversion`
- `choppy`
- `unknown`

## Data dependencies

Core tables used by prediction/backtest modules include:
- `dbo.UnderlyingSnapshot`
- `dbo.UnderlyingCandle5m`
- `dbo.OptionInstrument`
- `dbo.OptionSnapshot`
- `dbo.OptionSnapshotCalc`
- Optional proxy data from `dbo.MarketActivityDaily`

## Prediction schema checklist

### Required for prediction generation
- `dbo.UnderlyingSnapshot`
  - `underlying`, `trade_date`, `open_price`, `high_price`, `low_price`, `close_price`

### Required for index backtest
- `dbo.UnderlyingSnapshot`
  - `trade_date`, `open_price`, `close_price`
- `dbo.UnderlyingCandle5m`
  - `underlying`, `trade_date`, `low_price`, `high_price`

### Required for option selection and e2e backtest
- `dbo.OptionInstrument`
  - `id`, `instrument_token`, `underlying`, `tradingsymbol`, `expiry`, `strike`, `lot_size`
- `dbo.OptionSnapshot`
  - `id`, `option_instrument_id`, `snapshot_time`, `last_price`, `volume`, `open_interest`
- `dbo.OptionSnapshotCalc`
  - `option_snapshot_id`, `implied_volatility`, `delta`, `gamma`
- `dbo.MarketActivityDaily` (optional enrichment)
  - `underlying`, `trade_date`, `fin_instrm_tp`, `tckr_symb`, `open_interest`, `traded_volume`, `traded_value`
