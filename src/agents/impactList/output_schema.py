 # impactList Output Schema

```json
{
  "event_id": "string",
  "source_commodities": [
    {
      "commodity": "string",
      "expected_price_direction": "up | down | mixed | uncertain",
      "confidence": 0.0
    }
  ],
  "sector_impacts": [
    {
      "impact_rank": 1,
      "commodity": "string",
      "sector": "string",
      "sub_sector": "string | null",
      "impact_channel": "revenue | input_cost | inventory_gain_loss | working_capital | demand | regulation | logistics | sentiment | other",
      "directness": "direct | indirect | second_order",
      "beneficiary_or_harmed": "beneficiary | harmed | mixed | uncertain",
      "expected_stock_direction": "up | down | mixed | uncertain",
      "stock_price_sensitivity": "very_high | high | medium | low",
      "fundamental_impact_timeline": "same_day | 1_3_days | 1_4_weeks | 1_6_months | 6_months_plus | uncertain",
      "market_reaction_timeline": "same_day | 1_3_days | 1_4_weeks | uncertain",
      "commodity_cost_or_revenue_exposure": "very_high | high | medium | low | unknown",
      "pass_through_ability": "high | medium | low | regulated | unknown",
      "reasoning": "string",
      "example_companies": [
        {
          "company_name": "string",
          "ticker": "string | null",
          "exchange": "string | null",
          "why_relevant": "string",
          "expected_direction": "up | down | mixed | uncertain",
          "company_confidence": 0.0
        }
      ],
      "risks_to_thesis": ["string"],
      "confidence": 0.0
    }
  ],
  "ranking_method": {
    "score_formula": "string",
    "notes": "string"
  },
  "requires_human_review": true
}