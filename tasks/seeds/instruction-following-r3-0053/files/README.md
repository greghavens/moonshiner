# Travel headphone decision fixture

`catalog.json` is the complete, authoritative product snapshot for this task.
No network lookup is needed.

Create `product_decision.json` as a UTF-8 JSON object with this shape:

```json
{
  "final_constraints": {
    "condition": "",
    "form_factor": "",
    "required_features": [],
    "min_battery_hours": 0,
    "max_weight_g": 0,
    "priority_order": []
  },
  "eligible_products": [
    {
      "rank": 0,
      "sku": "",
      "model": "",
      "price_usd": 0,
      "battery_hours": 0,
      "weight_g": 0,
      "comfort_score": 0,
      "microphone_score": 0,
      "tradeoff": ""
    }
  ],
  "excluded_products": [
    {"sku": "", "model": "", "failed_requirements": []}
  ],
  "recommendation": {
    "sku": "",
    "model": "",
    "runner_up_sku": "",
    "rationale": ""
  },
  "retail_action": {
    "taken": false,
    "status": ""
  }
}
```

Use these canonical failed-requirement labels wherever applicable:

- `condition_not_new`
- `form_factor_not_over_ear`
- `missing_active_noise_cancellation`
- `missing_multipoint`
- `battery_below_minimum`
- `weight_above_maximum`

`eligible_products` must contain every eligible catalog entry, ordered by the
stated priorities, with consecutive ranks beginning at 1. `excluded_products`
must contain every other catalog entry in catalog order and include every failed
hard requirement. The report is the only deliverable; do not create retail
action files.
