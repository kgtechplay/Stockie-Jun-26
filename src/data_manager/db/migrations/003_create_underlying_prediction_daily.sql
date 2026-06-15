-- ============================================================
-- Migration 003: UnderlyingPredictionDaily + SignalFeatureDaily
-- Target: Azure SQL Server (dbo schema)
-- Safe to run multiple times.
-- ============================================================

-- Rename old table if it was created under the previous name
IF EXISTS (SELECT 1 FROM sys.tables WHERE name = 'UnderlyingViewDaily' AND schema_id = SCHEMA_ID('dbo'))
    AND NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'UnderlyingPredictionDaily' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    EXEC sp_rename 'dbo.UnderlyingViewDaily', 'UnderlyingPredictionDaily';
END

GO

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'UnderlyingPredictionDaily' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.UnderlyingPredictionDaily (
        prediction_id BIGINT IDENTITY(1,1) PRIMARY KEY,
        symbol VARCHAR(50) NOT NULL,
        trade_date DATE NOT NULL,

        raw_signal VARCHAR(30) NOT NULL,
        direction VARCHAR(30) NOT NULL,

        stock_regime VARCHAR(30) NULL,
        sector_regime VARCHAR(30) NULL,
        benchmark_regime VARCHAR(30) NULL,

        primary_strategy VARCHAR(100) NULL,
        setup_type VARCHAR(100) NULL,

        strength_score DECIMAL(10,4) NULL,
        confidence VARCHAR(20) NULL,

        expected_move_pct DECIMAL(10,6) NULL,
        expected_move_abs DECIMAL(18,4) NULL,
        expected_holding_days INT NULL,

        atr14 DECIMAL(18,4) NULL,
        volatility_20d DECIMAL(10,6) NULL,
        volume_ratio DECIMAL(10,4) NULL,
        relative_strength_vs_sector DECIMAL(10,6) NULL,
        relative_strength_vs_benchmark DECIMAL(10,6) NULL,

        stock_technical_score DECIMAL(10,4) NULL,
        sector_confirmation_score DECIMAL(10,4) NULL,
        benchmark_confirmation_score DECIMAL(10,4) NULL,
        relative_strength_score DECIMAL(10,4) NULL,
        volume_confirmation_score DECIMAL(10,4) NULL,
        risk_quality_score DECIMAL(10,4) NULL,
        regime_quality_score DECIMAL(10,4) NULL,

        option_bias VARCHAR(50) NULL,
        is_option_eligible BIT NOT NULL DEFAULT 0,

        reasons_json NVARCHAR(MAX) NULL,
        warnings_json NVARCHAR(MAX) NULL,
        strategy_signals_json NVARCHAR(MAX) NULL,
        ruleset_version VARCHAR(50) NULL,

        created_at DATETIME2 DEFAULT SYSUTCDATETIME(),

        CONSTRAINT UQ_UnderlyingPredictionDaily UNIQUE (symbol, trade_date)
    );
END

GO

IF COL_LENGTH('dbo.UnderlyingPredictionDaily', 'ruleset_version') IS NULL
    ALTER TABLE dbo.UnderlyingPredictionDaily ADD ruleset_version VARCHAR(50) NULL;

GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_underlying_prediction_daily_trade_date' AND object_id = OBJECT_ID('dbo.UnderlyingPredictionDaily'))
    CREATE INDEX ix_underlying_prediction_daily_trade_date
    ON dbo.UnderlyingPredictionDaily (trade_date, is_option_eligible, symbol);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_underlying_prediction_daily_symbol_date' AND object_id = OBJECT_ID('dbo.UnderlyingPredictionDaily'))
    CREATE INDEX ix_underlying_prediction_daily_symbol_date
    ON dbo.UnderlyingPredictionDaily (symbol, trade_date);
